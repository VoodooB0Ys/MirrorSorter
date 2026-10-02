# -*- coding: utf-8 -*-
"""窗口顶部原生"浏览器式"标签栏(WinForms 自绘, 仅供 app_window.py 窗口壳使用)。

原理: pywebview 的 WinForms 窗体(window.native)里,
- 标签栏 = Dock=Top 的 Panel, Paint 事件自绘标签(圆角/标题/✕), MouseClick 命中测试;
- WebView2 控件从 Dock=Fill 改为手动定位在标签栏下方(Anchor 四边), 窗口缩放自动跟随。
标签栏属于窗口 chrome, 不向页面注入任何元素 —— 任何网站都不被遮挡、行为不变。
窗口为无边框(frameless): 本栏兼任标题栏 —— 空白处可拖动窗口/双击最大化,
右端有 ─ □ ✕ 窗口按钮; 无边框缩放由 shell_pages.init 的 WS_THICKFRAME 补丁恢复。

注意: pythonnet 对 .NET 虚方法重写(OnPaint 等)不可靠, 必须用 Paint/MouseClick 事件订阅。
"""
import threading


def install_strip(win, api, bg_rgb=(15, 17, 21)):
    """在窗口顶部安装原生标签栏并下移 WebView2 内容区。须在 GUI 就绪后调用(GUI 线程之外
    调用安全, 内部已 marshal)。返回 update(tabs, active) —— 传入
    [{"id":..,"title":..,"closable":..}] 与激活 id 即重绘。"""
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing import (Color, Font, SolidBrush, StringFormat,
                                StringAlignment, StringFormatFlags,
                                StringTrimming, RectangleF, Point, Size)
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
    import System.Windows.Forms as WinForms

    form = win.native
    scale = float(getattr(form, "_scale", 1.0) or 1.0)
    bar_h = max(34, int(44 * scale))
    tab_h = bar_h - 8
    font = Font("Microsoft YaHei", 9.0 * scale)
    ACCENT = Color.FromArgb(251, 114, 153)      # 应用主题粉
    TAB_BG = Color.FromArgb(44, 50, 64)
    BG = Color.FromArgb(*bg_rgb)
    FG_ON = Color.White
    FG_OFF = Color.FromArgb(168, 176, 190)
    st = {"tabs": [], "active": 0, "hover_btn": None}
    lock = threading.Lock()
    form = win.native

    strip = WinForms.Panel()
    strip.DoubleBuffered = True
    strip.Height = bar_h
    strip.Dock = WinForms.DockStyle.Top
    strip.BackColor = BG
    strip.Cursor = WinForms.Cursors.Default

    sf = StringFormat()
    sf.Alignment = StringAlignment.Near
    sf.LineAlignment = StringAlignment.Center
    sf.FormatFlags = StringFormatFlags.NoWrap
    sf.Trimming = StringTrimming.EllipsisCharacter

    def _layout(width):
        n = len(st["tabs"])
        if not n:
            return []
        gap, margin = int(4 * scale), int(6 * scale)
        btn_zone = BTN_W() * 3 + int(2 * scale)
        tw = min(int(200 * scale),
                 max(int(110 * scale), (width - margin - btn_zone - gap * (n - 1)) // n))
        out, x = [], margin
        for _ in range(n):
            out.append((x, tw))
            x += tw + gap
        return out

    def _rounded_path(x, y, w, h, rad):
        p = GraphicsPath()
        d = rad * 2
        p.AddArc(x, y, d, d, 180, 90)
        p.AddArc(x + w - d, y, d, d, 270, 90)
        p.AddArc(x + w - d, y + h - d, d, d, 0, 90)
        p.AddArc(x, y + h - d, d, d, 90, 90)
        p.CloseFigure()
        return p

    def on_paint(_sender, e):
        g = e.Graphics
        g.SmoothingMode = SmoothingMode.AntiAlias
        boxes = _layout(strip.Width)
        for i, t in enumerate(st["tabs"]):
            if i >= len(boxes):
                break
            x, tw = boxes[i]
            y, h = int(4 * scale), tab_h
            act = (t["id"] == st["active"])
            g.FillPath(SolidBrush(ACCENT if act else TAB_BG),
                       _rounded_path(x, y, tw, h, int(8 * scale)))
            fg = FG_ON if act else FG_OFF
            close_w = int(22 * scale) if t.get("closable") else int(8 * scale)
            rect = RectangleF(float(x + int(10 * scale)), float(y),
                              float(tw - close_w - int(10 * scale)), float(h))
            g.DrawString(t.get("title") or "页面", font, SolidBrush(fg), rect, sf)
            if t.get("closable"):
                cx = x + tw - close_w
                g.DrawString("\u2715", font, SolidBrush(fg),
                             RectangleF(float(cx), float(y), float(close_w), float(h)), sf)
        # 右端窗口按钮: ─ □ ✕ —— 无边框窗口的标题栏按钮, 白色符号 + 分隔线, 始终清晰可见
        from System.Drawing import Pen
        bw = BTN_W()
        zx = strip.Width - bw * 3
        g.DrawLine(Pen(Color.FromArgb(70, 76, 92), 1.0),
                   float(zx), float(int(5 * scale)), float(zx), float(bar_h - int(5 * scale)))
        sf_c = StringFormat()
        sf_c.Alignment = StringAlignment.Center
        sf_c.LineAlignment = StringAlignment.Center
        for i, (name, sym) in enumerate((("min", "\u2500"), ("max", "\u25a1"), ("close", "\u2715"))):
            bx = zx + bw * i
            hov = (st["hover_btn"] == name)
            if name == "close" and hov:
                g.FillRectangle(SolidBrush(Color.FromArgb(232, 17, 35)),
                                float(bx), 0.0, float(bw), float(bar_h))
            elif hov:
                g.FillRectangle(SolidBrush(Color.FromArgb(62, 68, 84)),
                                float(bx), 0.0, float(bw), float(bar_h))
            g.DrawString(sym, font, SolidBrush(Color.FromArgb(236, 240, 248)),
                         RectangleF(float(bx), 0.0, float(bw), float(bar_h)), sf_c)

    BTN_W = lambda: int(46 * scale)   # ─ □ ✕ 每个按钮宽(与系统标题栏按钮同规格)

    def _btn_zone(x):
        return x >= strip.Width - BTN_W() * 3

    def _btn_at(x):
        """返回 0=─ 1=□ 2=✕ 或 None。"""
        if not _btn_zone(x):
            return None
        i = (x - (strip.Width - BTN_W() * 3)) // BTN_W()
        return ("min", "max", "close")[int(i)]

    def _toggle_max():
        import System.Windows.Forms as WinForms
        try:
            form.MaximizedBounds = WinForms.Screen.FromControl(form).WorkingArea
            form.WindowState = (WinForms.FormWindowState.Normal
                                if form.WindowState == WinForms.FormWindowState.Maximized
                                else WinForms.FormWindowState.Maximized)
        except Exception:
            pass

    def _hit_tab(x, yv):
        boxes = _layout(strip.Width)
        for i, t in enumerate(st["tabs"]):
            if i >= len(boxes):
                break
            bx, tw = boxes[i]
            by, bh = int(4 * scale), tab_h
            if bx <= x <= bx + tw and by <= yv <= by + bh:
                return st["tabs"][i]
        return None

    def on_mouse_down(_sender, args):
        import ctypes
        if args.Button != WinForms.MouseButtons.Left:
            return
        if _btn_at(args.X) is not None or _hit_tab(args.X, args.Y) is not None:
            return
        if args.Clicks == 2:   # 双击空白区: 最大化/还原
            _toggle_max()
            return
        try:   # 空白区按下 → 拖动窗口(标准 NC 拖拽技巧)
            u32 = ctypes.windll.user32
            hwnd = form.Handle.ToInt32()
            u32.ReleaseCapture()
            u32.SendMessageW(hwnd, 0xA1, 0x2, 0)   # WM_NCLBUTTONDOWN, HTCAPTION
        except Exception:
            pass

    def on_mouse_move(_sender, args):
        name = _btn_at(args.X)
        if name != st["hover_btn"]:
            st["hover_btn"] = name
            strip.Invalidate()

    def on_mouse_leave(_sender, _args):
        if st["hover_btn"]:
            st["hover_btn"] = None
            strip.Invalidate()

    def on_window_btn(name):
        import System.Windows.Forms as WinForms
        try:
            if name == "min":
                form.WindowState = WinForms.FormWindowState.Minimized
            elif name == "max":
                _toggle_max()
            elif name == "close":
                form.Close()
        except Exception:
            pass

    def on_mouse_click(_sender, args):
        name = _btn_at(args.X)
        if name is not None:
            on_window_btn(name)
            return
        t = _hit_tab(args.X, args.Y)
        if t is not None:
            boxes = _layout(strip.Width)
            for i, tt in enumerate(st["tabs"]):
                if tt is t and i < len(boxes):
                    x, tw = boxes[i]
                    if t.get("closable") and args.X > x + tw - int(24 * scale):
                        api.tab_close(t["id"])
                    else:
                        api.tab_switch(t["id"])
                    return

    strip.MouseDown += on_mouse_down
    strip.MouseMove += on_mouse_move
    strip.MouseLeave += on_mouse_leave
    strip.Paint += on_paint
    strip.MouseClick += on_mouse_click
    strip.Resize += lambda _s, _e: strip.Invalidate()

    def _install():
        try:
            wv = form.webview
            wv.Dock = getattr(WinForms.DockStyle, "None")
            form.Controls.Add(strip)
            strip.BringToFront()
            wv.Location = Point(0, bar_h)
            wv.Size = Size(form.ClientSize.Width, form.ClientSize.Height - bar_h)
            wv.Anchor = (WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Bottom |
                         WinForms.AnchorStyles.Left | WinForms.AnchorStyles.Right)
        except Exception:
            import traceback
            traceback.print_exc()

    if form.InvokeRequired:
        form.BeginInvoke(WinForms.MethodInvoker(_install))
    else:
        _install()

    def update(tabs, active):
        with lock:
            st["tabs"] = list(tabs)
            st["active"] = active

        def _repaint():
            try:
                strip.Invalidate()
            except Exception:
                pass
        if strip.InvokeRequired:
            strip.BeginInvoke(WinForms.MethodInvoker(_repaint))
        else:
            _repaint()

    return update
