# -*- coding: utf-8 -*-
"""独立悬浮窗口(仅供 app_window.py 窗口壳使用, v0.3.1.34)。

「信息以新窗口形式打开」的落地部件: 每个悬浮窗是一个**无边框 WinForms 窗体**, 顶部自绘
迷你标题栏(页面标题 + `⤓ 塞回标签页` + `— 最小化` + `□ 全屏` + `✕ 关闭`), 主体承载**同一个**
WebView2 控件 —— 标签页与悬浮窗之间只做 `Control.Parent` 迁移: 控件实例不变, 页面状态
(播放进度/滚动位置/表单)原样保留, **不重载**。

生命周期(由 ShellApi 驱动):
    open(tab, pages)   标签页/新链接 → 新建悬浮窗并把控件迁进来
    dock(tid)          悬浮窗 → 迁回主窗口标签页(销毁窗体, 控件保留)
    close(tid)         摘出控件并销毁窗体(控件随后由控件池 dispose)

与主窗口标签栏一致: 悬浮窗也吃主界面的背景配色方案(ui_bg_scheme, 见 set_bg)。
所有窗体/控件操作都在 UI 线程执行(内部 marshal), 管理器方法可从任意线程调用。

注意: pythonnet 对 .NET 虚方法重写(OnPaint 等)不可靠 —— 必须用 Paint/MouseClick 事件订阅。
"""
import os
import re
import threading

_FLOAT_W, _FLOAT_H = 1120, 760     # 默认尺寸(逻辑像素, 会乘 DPI 缩放)
_FLOAT_MIN = (420, 300)            # 最小尺寸
_BTN_DOCK, _BTN_CLOSE = "dock", "close"
_BTN_MIN, _BTN_MAX = "min", "max"   # bug4: 迷你标题栏补「最小化」「全屏(最大化)」两枚按钮


def install_floats(win, api, pages, bg_getter=None, accent_getter=None, tabstrip_h=None):
    """在窗口壳里安装悬浮窗管理器。须在 GUI 就绪后调用(内部已 marshal)。

    tabstrip_h: 主窗口标签栏高度(逻辑像素) —— 拖动悬浮窗标题栏松手落在该区域内即
    **合并成标签页**(与浏览器把窗口拖进另一个窗口标签栏的交互一致)。

    返回管理器(方法均以 tab_id 为键):
      open(tab, pages) / dock(tid) / close(tid) / close_all() / set_title(tid, title) /
      focus(tid) / exists(tid) / set_bg(raw) / bar_h
    """
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing import (Color, Font, Pen, Rectangle, RectangleF, Point, Size,
                                SolidBrush, StringFormat, StringAlignment,
                                StringFormatFlags, StringTrimming)
    from System.Drawing.Drawing2D import SmoothingMode
    import System.Windows.Forms as WinForms

    main = win.native
    scale = float(getattr(main, "_scale", 1.0) or 1.0)
    bar_h = max(26, int(30 * scale))
    btn_w = int(34 * scale)
    font = Font("Microsoft YaHei", 8.5 * scale)
    btn_font = Font("Microsoft YaHei", 10.0 * scale)   # 符号=1.25倍字号(与标签栏一致)

    # 与标签栏同款默认配色(accent_watch 会用 ui_bg_scheme 覆盖)
    st = {"bar": Color.FromArgb(10, 12, 16),
          "fg_on": Color.FromArgb(226, 230, 238),
          "fg_off": Color.FromArgb(150, 158, 172),
          "hover": Color.FromArgb(30, 34, 44),
          "sep": Color.FromArgb(52, 57, 68),
          "accent": Color.FromArgb(251, 114, 153),   # 与标签栏同源(float_ball_accent)
          "close_hot": Color.FromArgb(232, 17, 35)}

    wins = {}          # tid -> {"form","bar","title","hover","chrome","closing"}
    _EDGE = Color.FromArgb(15, 17, 21)   # bug10: 贴边热区固定底色(=窗体深色底, 不随主题变浅)
    lock = threading.Lock()
    seq = {"n": 0}     # 多窗口层叠偏移计数

    sf = StringFormat()
    sf.Alignment = StringAlignment.Near
    sf.LineAlignment = StringAlignment.Center
    sf.FormatFlags = StringFormatFlags.NoWrap
    sf.Trimming = StringTrimming.EllipsisCharacter
    sf_c = StringFormat()   # 符号居中(⤓/✕)
    sf_c.Alignment = StringAlignment.Center
    sf_c.LineAlignment = StringAlignment.Center

    def _ui(fn):
        """marshal 到 UI 线程(窗体未就绪时直接执行)。"""
        try:
            if main.InvokeRequired:
                main.BeginInvoke(WinForms.MethodInvoker(fn))
            else:
                fn()
        except Exception:
            import traceback
            traceback.print_exc()

    def _nc_drag(form, ht):
        """进入系统模态循环(移动 HTCAPTION / 缩放 HTLEFT...)。"""
        import ctypes
        try:
            u32 = ctypes.windll.user32
            u32.ReleaseCapture()
            u32.SendMessageW(form.Handle.ToInt32(), 0xA1, ht, 0)
        except Exception:
            pass

    def _place(form):
        """把新窗放到主窗口附近(层叠错开), 并夹在屏幕工作区内。"""
        try:
            scr = WinForms.Screen.FromControl(main)
            wa = scr.WorkingArea
            w = min(int(_FLOAT_W * scale), max(int(_FLOAT_MIN[0] * scale), wa.Width - 40))
            h = min(int(_FLOAT_H * scale), max(int(_FLOAT_MIN[1] * scale), wa.Height - 40))
            mb = main.Bounds
            x = mb.X + int((mb.Width - w) / 2.0)
            y = mb.Y + int((mb.Height - h) / 3.0)
            off = int(26 * scale) * (seq["n"] % 8)
            seq["n"] += 1
            x = min(max(wa.X + 10, x + off), wa.X + wa.Width - w - 10)
            y = min(max(wa.Y + 10, y + off), wa.Y + wa.Height - h - 10)
            form.Bounds = Rectangle(x, y, w, h)
        except Exception:
            pass

    _BTN_ORDER = (_BTN_DOCK, _BTN_MIN, _BTN_MAX, _BTN_CLOSE)   # 从左到右

    def _btn_at(win, x):
        """返回 _BTN_DOCK / _BTN_MIN / _BTN_MAX / _BTN_CLOSE 或 None(迷你标题栏右侧按钮带)。"""
        try:
            bw = btn_w
            n = len(_BTN_ORDER)
            zx = win["bar"].Width - bw * n
            if x < zx:
                return None
            i = int((x - zx) // bw)
            return _BTN_ORDER[min(max(i, 0), n - 1)]
        except Exception:
            return None

    def _is_max(win):
        """悬浮窗当前是否处于全屏(最大化)状态。"""
        try:
            return win["form"].WindowState == WinForms.FormWindowState.Maximized
        except Exception:
            return False

    def _toggle_max(win):
        """全屏(最大化) ↔ 还原。无边框窗体靠 MaximizedBounds=屏幕工作区 铺满。"""
        try:
            form = win["form"]
            form.WindowState = (WinForms.FormWindowState.Normal
                                if form.WindowState == WinForms.FormWindowState.Maximized
                                else WinForms.FormWindowState.Maximized)
            win["hover"] = None
            win["bar"].Invalidate()
        except Exception:
            pass

    def _paint(win, e):
        form = win["form"]
        bar = win["bar"]
        g = e.Graphics
        g.SmoothingMode = SmoothingMode.AntiAlias
        g.Clear(st["bar"])
        bw = btn_w
        zx = bar.Width - bw * len(_BTN_ORDER)
        rect = RectangleF(float(int(12 * scale)), 0.0,
                          float(max(10, zx - int(20 * scale))), float(bar_h))
        if win.get("drop_hint"):
            # 光标已落在主窗口标签栏上 → 松手即合并。
            # 悬浮窗此时正盖着标签栏(主窗口侧的高亮被挡住看不见), 所以提示必须画在**自己身上**:
            # 标题区整片染强调色 + 居中提示语 + 整条强调色描边。
            ac = st["accent"]
            g.FillRectangle(SolidBrush(Color.FromArgb(56, ac.R, ac.G, ac.B)),
                            0.0, 0.0, float(zx), float(bar_h))
            g.DrawRectangle(Pen(ac, max(1.0, 2.0 * scale)),
                            1.0, 1.0, float(max(1, zx - 2)), float(bar_h - 2))
            sf_h = StringFormat()
            sf_h.Alignment = StringAlignment.Center
            sf_h.LineAlignment = StringAlignment.Center
            sf_h.FormatFlags = StringFormatFlags.NoWrap
            g.DrawString("松手合并到标签栏", font, SolidBrush(st["fg_on"]),
                         RectangleF(0.0, 0.0, float(zx), float(bar_h)), sf_h)
        else:
            g.DrawString(win["title"] or "页面", font, SolidBrush(st["fg_on"]), rect, sf)
        g.DrawLine(Pen(st["sep"], 1.0),
                   float(zx), float(int(5 * scale)), float(zx), float(bar_h - int(5 * scale)))
        for i, (name, sym) in enumerate(((_BTN_DOCK, "⤓"), (_BTN_MIN, "—"),
                                         (_BTN_MAX, "▣" if _is_max(win) else "□"),
                                         (_BTN_CLOSE, "✕"))):
            bx = zx + bw * i
            if win.get("hover") == name:
                g.FillRectangle(SolidBrush(st["close_hot"] if name == _BTN_CLOSE else st["hover"]),
                                float(bx), 0.0, float(bw), float(bar_h))
            g.DrawString(sym, btn_font, SolidBrush(st["fg_on"]),
                         RectangleF(float(bx), 0.0, float(bw), float(bar_h)), sf_c)

    def _raise_chrome(win):
        """把迷你标题栏与缩放热区重新提到最前(WebView2 控件加入后可能盖住它们)。"""
        try:
            for c in win.get("chrome") or ():
                c.BringToFront()
        except Exception:
            pass

    def _hint(on, win=None):
        """拖拽期间提示"松手即合并到标签栏"。

        两条通道:
          1. 主窗口标签栏整条高亮(shell_tabstrip.highlight) —— 语义上的落点目标;
          2. 悬浮窗自己迷你标题栏染色 + 提示语 —— 拖拽时悬浮窗正盖在标签栏上,
             主窗口侧的高亮用户多半看不到, 所以可见提示必须画在悬浮窗身上。
        """
        try:
            fn = getattr(api, "_strip_update", None)
            h = getattr(fn, "highlight", None)
            if h is not None:
                h(bool(on))
        except Exception:
            pass
        if win is not None:
            try:
                if bool(win.get("drop_hint")) != bool(on):
                    win["drop_hint"] = bool(on)
                    win["bar"].Invalidate()
            except Exception:
                pass

    def _over_tabbar():
        """光标是否落在主窗口标签栏(含少量容差) —— 松手即合并。"""
        if not tabstrip_h:
            return False
        try:
            if main.WindowState == WinForms.FormWindowState.Minimized:
                return False
            lp = main.PointToClient(WinForms.Cursor.Position)
            if lp.X < 0 or lp.Y < 0 or lp.X > main.ClientSize.Width:
                return False
            return lp.Y <= int(tabstrip_h) + int(8 * scale)
        except Exception:
            return False

    def _make_win(tid, title):
        form = WinForms.Form()
        form.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")   # None 是 Python 关键字
        form.Text = title or "页面"
        form.BackColor = st["bar"]
        form.ShowInTaskbar = True
        # 用户新报修(m02020): 原来用系统级置顶 form.TopMost = True → 悬浮窗压在整个桌面的
        # 最上层, QQ 之类别的应用都被它盖住。改成 **owner(归属)关系**: 悬浮窗只保持在主窗口
        # 之上; 别的应用照样能盖住它, 切回本软件时它仍在主窗口之上。
        try:
            form.Owner = main
        except Exception:
            pass
        form.StartPosition = WinForms.FormStartPosition.Manual
        # bug10: 无边框悬浮窗同样会被 Win10/11 画一圈系统色边框(浅色主题下发白,
        # "窗口好像可被选中") → 关 DWM 非客户区渲染 + 强制深色边框 + Win11 请求不画边框
        try:
            import ctypes
            hwnd = form.Handle.ToInt32()
            v = ctypes.c_int(2)   # DWMWA_NCRENDERING_POLICY = DISABLED
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(v), ctypes.sizeof(v))
            on = ctypes.c_int(1)
            for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE(新旧属性号)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on))
            none_c = ctypes.c_int(-2)   # DWMWA_COLOR_NONE
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(none_c), ctypes.sizeof(none_c))
        except Exception:
            pass
        form.MinimumSize = Size(int(_FLOAT_MIN[0] * scale), int(_FLOAT_MIN[1] * scale))
        try:
            form.MaximizedBounds = WinForms.Screen.FromControl(main).WorkingArea
        except Exception:
            pass

        bar = WinForms.Panel()
        bar.DoubleBuffered = True
        bar.Dock = WinForms.DockStyle.Top
        bar.Height = bar_h
        bar.BackColor = st["bar"]
        bar.Cursor = WinForms.Cursors.Default
        form.Controls.Add(bar)

        win = {"form": form, "bar": bar, "title": title or "页面",
               "hover": None, "chrome": [bar], "closing": False,
               "dragging": False, "dx": 0, "dy": 0, "drop_hint": False}
        wins[tid] = win

        bar.Paint += lambda _s, e: _paint(win, e)
        bar.Resize += lambda _s, _e: bar.Invalidate()

        def on_move(_s, args):
            if win.get("dragging"):
                # 自绘拖动: 需要拿到"松手落点"判断是否拖进了主窗口标签栏(见 on_up),
                # 所以不用 HTCAPTION 系统模态循环(那条路我们收不到落点)。
                try:
                    cur = WinForms.Cursor.Position
                    form.Location = Point(cur.X - win["dx"], cur.Y - win["dy"])
                    win["drag_moved"] = True
                except Exception:
                    pass
                _hint(_over_tabbar(), win)
            name = _btn_at(win, args.X)
            if name != win["hover"]:
                win["hover"] = name
                bar.Invalidate()

        def on_leave(_s, _a):
            if win["hover"] is not None:
                win["hover"] = None
                bar.Invalidate()

        def on_down(_s, args):
            if args.Button != WinForms.MouseButtons.Left:
                return
            if _btn_at(win, args.X) is not None:
                return
            if _is_max(win):
                return          # 全屏态不允许自绘拖动(拖了会把窗口挪出屏幕/破坏最大化)
            if args.Y <= int(4 * scale):
                _nc_drag(form, 12)   # HTTOP: 标题栏上缘 4px = 顶边缩放带
                return
            try:
                cur = WinForms.Cursor.Position
                win["dragging"] = True
                win["dx"] = cur.X - form.Location.X
                win["dy"] = cur.Y - form.Location.Y
                bar.Capture = True   # 拖出标题栏/窗体也持续收 MouseMove/MouseUp
            except Exception:
                win["dragging"] = False

        def on_up(_s, _a):
            if not win.get("dragging"):
                return
            win["dragging"] = False
            try:
                bar.Capture = False
            except Exception:
                pass
            over = _over_tabbar()
            _hint(False, win)
            if over:   # 拖到主窗口标签栏松手 → 合并成标签页(浏览器同款交互)
                try:
                    api.window_dock(tid)
                except Exception:
                    pass

        def on_dbl(_s, _a):
            _toggle_max(win)

        def on_click(_s, args):
            if win.pop("drag_moved", False):   # 刚拖过窗口 → 吞掉这次点击
                return
            name = _btn_at(win, args.X)
            if name == _BTN_CLOSE:
                try:
                    api.tab_close(tid)
                except Exception:
                    pass
            elif name == _BTN_DOCK:
                try:
                    api.window_dock(tid)
                except Exception:
                    pass
            elif name == _BTN_MIN:
                try:
                    form.WindowState = WinForms.FormWindowState.Minimized
                except Exception:
                    pass
            elif name == _BTN_MAX:
                _toggle_max(win)

        bar.MouseDown += on_down
        bar.MouseMove += on_move
        bar.MouseUp += on_up
        bar.MouseLeave += on_leave
        bar.MouseDoubleClick += on_dbl
        bar.MouseClick += on_click

        def on_closing(_s, e):
            # 用户经任务栏/Alt+F4 关窗 → 视同关标签(摘控件+销毁窗体); 内部关闭时放行
            if win["closing"]:
                return
            e.Cancel = True
            try:
                api.tab_close(tid)
            except Exception:
                pass

        form.FormClosing += on_closing
        return form

    def _build_edges(win):
        """无边框边缘缩放热区(WebView2 铺满客户区, THICKFRAME 收不到鼠标)。"""
        form = win["form"]
        try:
            ew = int(4 * scale)
            cw, ch = form.ClientSize.Width, form.ClientSize.Height

            def edge(ht, loc, size, anchor, cursor):
                p = WinForms.Panel()
                # bug10: 热区压在页面边缘 4px 上 —— 固定用窗体深色底(不再跟随标签栏配色,
                # 浅色主题下会变成一圈浅色边框, 用户报"播放界面有白色边框")
                p.BackColor = _EDGE
                p.Location = loc
                p.Size = size
                p.Anchor = anchor
                p.Cursor = cursor

                def down(_s, a):
                    if a.Button == WinForms.MouseButtons.Left:
                        _nc_drag(form, ht)
                p.MouseDown += down
                form.Controls.Add(p)
                p.BringToFront()
                win["chrome"].append(p)

            # 右/下热区外扩 2px: 高 DPI 下逻辑→物理像素有舍入差, 精确贴边会露 1px 白线
            edge(10, Point(0, 0), Size(ew, ch),
                 WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Bottom |
                 WinForms.AnchorStyles.Left, WinForms.Cursors.SizeWE)
            edge(11, Point(cw - ew - 2, 0), Size(ew + 2, ch),
                 WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Bottom |
                 WinForms.AnchorStyles.Right, WinForms.Cursors.SizeWE)
            edge(15, Point(0, ch - ew - 2), Size(cw + 2, ew + 2),
                 WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Left |
                 WinForms.AnchorStyles.Right, WinForms.Cursors.SizeNS)
        except Exception:
            pass

    # ---------------- 对外 API ----------------
    def open(tab, pages_=None):
        """把 tab 的控件迁进一个新的悬浮窗(已存在则聚焦)。"""
        tid = tab["id"]
        title = tab.get("title") or "页面"

        def go():
            try:
                with lock:
                    ex = wins.get(tid)
                if ex is not None:
                    try:
                        ex["form"].Activate()
                    except Exception:
                        pass
                    return
                form = _make_win(tid, title)
                _place(form)
                form.Show()
                _build_edges(win_of(tid))
                if pages_ is not None:
                    pages_.send_to_window(tid, form, bar_h,
                                          after=lambda: _raise_chrome(win_of(tid)))
                try:
                    form.Activate()
                except Exception:
                    pass
            except Exception:
                import traceback
                traceback.print_exc()
        _ui(go)

    def win_of(tid):
        with lock:
            return wins.get(tid)

    def dock(tid):
        """控件迁回主窗口标签页并销毁悬浮窗(控件保留, 由 ShellApi 重新显示)。"""
        with lock:
            win = wins.pop(tid, None)
        _hint(False, win)
        if win is None:
            return False
        win["closing"] = True
        form = win["form"]

        def go():
            try:
                pages.dock_to_main(tid)
            except Exception:
                import traceback
                traceback.print_exc()
            try:
                form.Close()
            except Exception:
                pass
        _ui(go)
        return True

    def close(tid):
        """摘出控件(不销毁, 交控件池 dispose)并销毁悬浮窗。"""
        with lock:
            win = wins.pop(tid, None)
        _hint(False, win)
        if win is None:
            return False
        win["closing"] = True
        form = win["form"]

        def go():
            try:
                c = pages.control(tid)
                if c is not None and c.Parent is not None:
                    c.Parent.Controls.Remove(c)
            except Exception:
                pass
            try:
                form.Close()
            except Exception:
                pass
        _ui(go)
        return True

    def close_all():
        """关闭全部悬浮窗(主窗口退出时调用); 控件随后由控件池统一销毁。"""
        with lock:
            tids = list(wins.keys())
        for t in tids:
            close(t)

    def set_title(tid, title):
        win = win_of(tid)
        if win is None:
            return
        win["title"] = title or "页面"

        def go():
            try:
                win["form"].Text = win["title"]
                win["bar"].Invalidate()
            except Exception:
                pass
        _ui(go)

    def focus(tid):
        win = win_of(tid)
        if win is None:
            return False

        def go():
            try:
                win["form"].Activate()
                win["form"].BringToFront()
            except Exception:
                pass
        _ui(go)
        return True

    def exists(tid):
        with lock:
            return tid in wins

    def set_bg(raw):
        """消费主界面推送的标签栏配色方案(ui_bg_scheme), 悬浮窗同步吃色。"""
        try:
            import json as _json
            sch = _json.loads(raw) if isinstance(raw, str) and raw.strip() else None
            if not isinstance(sch, dict):
                return

            def _c(key, fallback):
                v = str(sch.get(key) or "")
                if re.match(r"^#[0-9a-fA-F]{6}$", v):
                    return Color.FromArgb(int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
                return fallback

            st["bar"] = _c("bar", st["bar"])
            st["fg_on"] = _c("fgon", st["fg_on"])
            st["fg_off"] = _c("fgoff", st["fg_off"])
            st["hover"] = _c("hover", st["hover"])
            st["sep"] = _c("sep", st["sep"])

            def apply():
                for win in list(wins.values()):
                    try:
                        win["form"].BackColor = st["bar"]
                        win["bar"].BackColor = st["bar"]
                        for p in win["chrome"]:
                            try:
                                p.BackColor = _EDGE   # bug10: 贴边热区不吃主题配色
                            except Exception:
                                pass
                        win["bar"].Invalidate()
                    except Exception:
                        pass
            _ui(apply)
        except Exception:
            pass

    def set_accent(raw):
        """强调色(与标签栏同源 float_ball_accent): 拖拽落点提示用。"""
        try:
            v = str(raw or "")
            if not re.match(r"^#[0-9a-fA-F]{6}$", v):
                return
            c = Color.FromArgb(int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
            if st["accent"].ToArgb() == c.ToArgb():
                return
            st["accent"] = c

            def apply():
                for win in list(wins.values()):
                    try:
                        win["bar"].Invalidate()
                    except Exception:
                        pass
            _ui(apply)
        except Exception:
            pass

    def bg_watch():
        import time as _t
        last, last_ac = None, None
        while True:
            try:
                if bg_getter:
                    cur = bg_getter()
                    if cur != last:
                        last = cur
                        set_bg(cur)
                if accent_getter:
                    ac = accent_getter()
                    if ac != last_ac:
                        last_ac = ac
                        set_accent(ac)
            except Exception:
                pass
            _t.sleep(2)

    if bg_getter or accent_getter:
        threading.Thread(target=bg_watch, daemon=True, name="msx-float-bg").start()

    class _Mgr:
        """悬浮窗管理器(ShellApi 持有; 方法可从任意线程调用)。"""
        bar_h = None

    m = _Mgr()
    m.open = open
    m.dock = dock
    m.close = close
    m.close_all = close_all
    m.set_title = set_title
    m.focus = focus
    m.exists = exists
    m.set_bg = set_bg
    m.bar_h = bar_h
    return m
