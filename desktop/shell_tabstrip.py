# -*- coding: utf-8 -*-
"""窗口顶部原生"浏览器式"标签栏(WinForms 自绘, 仅供 app_window.py 窗口壳使用)。

原理: pywebview 的 WinForms 窗体(window.native)里,
- 标签栏 = Dock=Top 的 Panel, Paint 事件自绘标签(圆角/标题/✕), MouseClick 命中测试;
- WebView2 控件从 Dock=Fill 改为手动定位在标签栏下方(Anchor 四边), 窗口缩放自动跟随。
标签栏属于窗口 chrome, 不向页面注入任何元素 —— 任何网站都不被遮挡、行为不变。

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
    st = {"tabs": [], "active": 0}
    lock = threading.Lock()

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
        tw = min(int(200 * scale), max(int(120 * scale), (width - margin * 2 - gap * (n - 1)) // n))
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

    def on_mouse_click(_sender, args):
        boxes = _layout(strip.Width)
        for i, t in enumerate(st["tabs"]):
            if i >= len(boxes):
                break
            x, tw = boxes[i]
            y, h = int(4 * scale), tab_h
            if x <= args.X <= x + tw and y <= args.Y <= y + h:
                if t.get("closable") and args.X > x + tw - int(24 * scale):
                    api.tab_close(t["id"])
                else:
                    api.tab_switch(t["id"])
                return

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
