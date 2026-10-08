# -*- coding: utf-8 -*-
"""窗口顶部标签栏下方的"浏览器式"导航工具栏(仅供 app_window.py 窗口壳使用)。

布局(自上而下): shell_tabstrip 标签栏 → 本工具栏 → WebView2 内容区。
- 左端三个自绘图标按钮: ← 后退 / → 前进 / ⟳ 刷新(无历史=禁用灰, 悬停高亮);
- 其余宽度是原生 TextBox 地址栏: 实时显示当前激活标签页 URL, 可编辑 ——
  回车导航(无协议头自动补 https://, 非 http(s) 拒绝并还原), Esc/失焦还原显示值。
本栏属于窗口 chrome, 不遮挡任何网页内容; 按下不触发窗口拖动(与标签栏不同)。
配色与标签栏共用 ui_bg_scheme 推送(app.js bgStripScheme), 独立线程轮询跟随。

注意(与 shell_tabstrip 同一套踩坑纪律):
- pythonnet 虚方法重写不可靠 → 全部走 Paint/MouseClick/KeyDown 事件订阅;
- Pen/Brush/Font 等在函数顶部统一 import/创建, 不得在绘制分支中途 import;
- 所有 update 经 BeginInvoke marshal 到 UI 线程。
"""
import re
import threading

NAV_H = 34   # 逻辑高度(px); DPI 换算经 nav_h(scale)


def nav_h(scale=1.0):
    return max(30, int(NAV_H * scale))


def install_navbar(win, api, top=0, bg_getter=None):
    """在窗口 y=top 处安装导航工具栏(须在标签栏 install_strip 之后调用)。
    返回 update(url, can_back, can_fwd, loading=False) —— ShellApi 经它推送状态。
    """
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing import (Color, Font, Pen, RectangleF, SolidBrush,
                                Point, Size, StringFormat, StringAlignment,
                                StringFormatFlags, StringTrimming)
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
    import System.Windows.Forms as WinForms

    form = win.native
    scale = float(getattr(form, "_scale", 1.0) or 1.0)
    h = nav_h(scale)
    st = {
        "url": "", "back": False, "fwd": False, "loading": False,
        "hover": None,        # "back" / "fwd" / "reload"
        "edit": False,        # 地址栏正在编辑(聚焦) —— 期间不覆盖显示值
        # 配色: 与标签栏同一套 ui_bg_scheme(watcher 轮询推送)
        "bar": Color.FromArgb(10, 12, 16),
        "hover_bg": Color.FromArgb(30, 34, 44),
        "fg": Color.FromArgb(226, 230, 238),
        "fg_dim": Color.FromArgb(96, 102, 114),
        "tb_bg": Color.FromArgb(22, 25, 32),
        "tb_fg": Color.FromArgb(226, 230, 238),
        "tb_border": Color.FromArgb(52, 57, 68),
    }

    # 图标字体: 优先 Segoe MDL2 Assets(Win10+ 自带), 缺则退回雅黑符号
    try:
        from System.Drawing import FontFamily
        FontFamily("Segoe MDL2 Assets")
        icon_font = Font("Segoe MDL2 Assets", 10.0 * scale)
        glyphs = {"back": "\uE72B", "fwd": "\uE72A", "reload": "\uE72C",
                  "lock": "\uE72E"}
    except Exception:
        icon_font = Font("Microsoft YaHei", 10.0 * scale)
        glyphs = {"back": "\u2190", "fwd": "\u2192", "reload": "\u21BB",
                  "lock": ""}
    tb_font = Font("Microsoft YaHei", 8.5 * scale)

    BTN_W, BTN_GAP, MARGIN = (lambda: (int(32 * scale), int(2 * scale), int(6 * scale)))()

    bar = WinForms.Panel()
    bar.DoubleBuffered = True
    bar.Location = Point(0, int(top))
    bar.Size = Size(form.ClientSize.Width, h)
    bar.Anchor = (WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left |
                  WinForms.AnchorStyles.Right)
    bar.BackColor = st["bar"]

    # ---- 布局几何 ----
    def _btn_rects():
        x, bw, gap = MARGIN, BTN_W, BTN_GAP
        return {n: (x + i * (bw + gap), 0, bw, h)
                for i, n in enumerate(("back", "fwd", "reload"))}

    def _tb_rect():
        rects = _btn_rects()
        end = rects["reload"][0] + rects["reload"][2]
        tx = end + (int(22 * scale) if glyphs.get("lock") else int(8 * scale))
        return (tx, int(5 * scale),
                bar.Width - tx - int(8 * scale), h - int(10 * scale))

    # ---- 地址栏(原生 TextBox: 输入法/光标/全选白送) ----
    tb = WinForms.TextBox()
    tb.BorderStyle = getattr(WinForms.BorderStyle, "None")   # 边框自绘(系统色在深色下发白)
    tb.Font = tb_font
    tb.BackColor = st["tb_bg"]
    tb.ForeColor = st["tb_fg"]
    tb.TabStop = True

    def _place_tb():
        r = _tb_rect()
        tb.Location = Point(r[0], r[1])
        tb.Size = Size(r[2], r[3])

    _place_tb()

    sf = StringFormat()
    sf.Alignment = StringAlignment.Center
    sf.LineAlignment = StringAlignment.Center
    sf.FormatFlags = StringFormatFlags.NoWrap
    sf.Trimming = StringTrimming.EllipsisCharacter

    def _rounded_path(x, y, w, h_, rad):
        p = GraphicsPath()
        d = rad * 2
        p.AddArc(x, y, d, d, 180, 90)
        p.AddArc(x + w - d, y, d, d, 270, 90)
        p.AddArc(x + w - d, y + h_ - d, d, d, 0, 90)
        p.AddArc(x, y + h_ - d, d, d, 90, 90)
        p.CloseFigure()
        return p

    def on_paint(_sender, e):
        g = e.Graphics
        g.SmoothingMode = SmoothingMode.AntiAlias
        g.Clear(st["bar"])
        rects = _btn_rects()
        for name in ("back", "fwd", "reload"):
            bx, by, bw, bh = rects[name]
            enabled = {"back": st["back"], "fwd": st["fwd"],
                       "reload": True}[name]
            hov = (st["hover"] == name and enabled)
            if hov:
                g.FillPath(SolidBrush(st["hover_bg"]),
                           _rounded_path(bx, int(3 * scale), bw, bh - int(6 * scale),
                                         int(5 * scale)))
            g.DrawString(glyphs[name], icon_font,
                         SolidBrush(st["fg"] if enabled else st["fg_dim"]),
                         RectangleF(float(bx), float(by), float(bw), float(bh)), sf)
        # 地址栏: 圆角描边 + 左端锁形装饰(有的话)
        r = _tb_rect()
        rad = int(6 * scale)
        g.DrawPath(Pen(st["tb_border"], 1.0),
                   _rounded_path(r[0] - 1, r[1] - 1, r[2] + 2, r[3] + 2, rad))
        if glyphs.get("lock"):
            g.DrawString(glyphs["lock"], icon_font, SolidBrush(st["fg_dim"]),
                         RectangleF(float(r[0] - int(20 * scale)), float(r[1]),
                                    float(int(18 * scale)), float(r[3])), sf)

    # ---- 交互: 按钮悬停/点击(禁用态不响应) ----
    def _btn_at(x, y):
        for name, (bx, by, bw, bh) in _btn_rects().items():
            if bx <= x <= bx + bw and 0 <= y <= bh:
                return name
        return None

    def on_mouse_move(_sender, args):
        name = _btn_at(args.X, args.Y)
        if name and {"back": st["back"], "fwd": st["fwd"], "reload": True}[name] is False:
            name = None   # 禁用按钮不给悬停高亮
        if name != st["hover"]:
            st["hover"] = name
            bar.Invalidate()

    def on_mouse_leave(_sender, _args):
        if st["hover"] is not None:
            st["hover"] = None
            bar.Invalidate()

    def on_click(_sender, args):
        name = _btn_at(args.X, args.Y)
        if name == "back" and st["back"]:
            api.nav_back()
        elif name == "fwd" and st["fwd"]:
            api.nav_forward()
        elif name == "reload":
            api.nav_reload()

    # ---- 地址栏: 回车导航 / Esc 还原 / 失焦还原 / 聚焦全选 ----
    def on_tb_focus(_s=None, _a=None):
        st["edit"] = True
        try:
            tb.SelectAll()
        except Exception:
            pass

    def on_tb_blur(_s=None, _a=None):
        st["edit"] = False
        try:
            tb.Text = st["url"]
        except Exception:
            pass

    def on_tb_key(_s, e):
        code = int(e.KeyCode)
        if code == 13:   # Enter
            st["edit"] = False
            api.nav_goto(str(tb.Text or ""))
            try:
                tb.Text = st["url"]
            except Exception:
                pass
            e.Handled = True
            e.SuppressKeyPress = True
        elif code == 27:   # Esc
            st["edit"] = False
            try:
                tb.Text = st["url"]
            except Exception:
                pass
            e.Handled = True
            e.SuppressKeyPress = True

    tb.GotFocus += on_tb_focus
    tb.LostFocus += on_tb_blur
    tb.KeyDown += on_tb_key
    # 编辑期间点击地址栏不再下传按钮命中; 工具栏 Panel 上按下不拖动窗口(与标签栏不同)

    bar.Paint += on_paint
    bar.MouseMove += on_mouse_move
    bar.MouseLeave += on_mouse_leave
    bar.MouseClick += on_click
    bar.Resize += lambda _s, _e: (_place_tb(), bar.Invalidate())

    def _install():
        try:
            form.Controls.Add(bar)
            form.Controls.Add(tb)
            tb.BringToFront()
        except Exception:
            import traceback
            traceback.print_exc()

    if form.InvokeRequired:
        form.BeginInvoke(WinForms.MethodInvoker(_install))
    else:
        _install()

    def update(url, can_back, can_fwd, loading=False):
        st["url"] = str(url or "")
        st["back"] = bool(can_back)
        st["fwd"] = bool(can_fwd)
        st["loading"] = bool(loading)

        def _apply():
            try:
                if not st["edit"]:   # 编辑期间不覆盖用户输入
                    tb.Text = st["url"]
                bar.Invalidate()
            except Exception:
                pass
        if bar.InvokeRequired:
            bar.BeginInvoke(WinForms.MethodInvoker(_apply))
        else:
            _apply()

    def set_scheme(raw):
        """页面推送的配色方案(与标签栏 set_bg_scheme 同一套 json: bar/active/hover/
        fgon/fgoff/sep), 同步按钮/地址栏配色。"""
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
            st["hover_bg"] = _c("hover", st["hover_bg"])
            st["fg"] = _c("fgon", st["fg"])
            st["fg_dim"] = _c("fgoff", st["fg_dim"])
            st["tb_bg"] = _c("active", st["tb_bg"])
            st["tb_border"] = _c("sep", st["tb_border"])

            def apply():
                try:
                    bar.BackColor = st["bar"]
                    tb.BackColor = st["tb_bg"]
                    tb.ForeColor = st["fg"]
                except Exception:
                    pass
                bar.Invalidate()

            if bar.InvokeRequired:
                bar.BeginInvoke(WinForms.MethodInvoker(apply))
            else:
                apply()
        except Exception:
            pass

    def scheme_watch():
        import time as _t
        last = None
        while True:
            try:
                if bg_getter:
                    cur = bg_getter()
                    if cur != last:
                        last = cur
                        set_scheme(cur)
            except Exception:
                pass
            _t.sleep(2)

    if bg_getter:
        threading.Thread(target=scheme_watch, daemon=True, name="msx-navbar-scheme").start()

    return update
