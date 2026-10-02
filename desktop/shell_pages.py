# -*- coding: utf-8 -*-
"""内置浏览器标签页的 WebView2 控件池(仅供 app_window.py 窗口壳使用)。

每个标签页一个独立的 WebView2 控件(共享同一浏览器进程与用户数据目录 → 登录态
互通), 切换标签 = 控件可见性切换 —— 页面状态原样保留: 首页永不重载、视频切走
再切回继续播。关闭标签 = 控件 Dispose。

所有控件操作都在 UI 线程执行(内部经 form.BeginInvoke marshal), 管理器方法可从
任意线程调用。pythonnet 注意: 控件必须在 UI 线程创建(跨线程建父子会抛
ArgumentException); Anchor 必须显式四边(主控件 Dock=Fill 时其 Anchor 值不可拷贝)。
"""
import threading
import time


def create_pool(win, on_title, on_newtab):
    """创建控件池。须在窗口壳启动后调用(任意线程, 内部自行 marshal)。

    on_title(tab_key, title) / on_newtab(tab_key, url): 站点控件的
    DocumentTitleChanged / NewWindowRequested(UI 线程)回调。
    返回管理器: init/create/show/hide/dispose —— 均以 tab_key 为键
    (key 0 = 信息流主控件); 控件对象由池内部持有。
    """
    import System.Windows.Forms as WinForms  # noqa: F401  提前加载 CLR(pythonnet)
    box = {"form": None}

    def _ui(fn):
        """marshal 到 UI 线程(窗体未绑定时直接执行, 供测试注入)。"""
        form = box.get("form")
        if form is None:
            fn()
            return

        def go():
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
        try:
            if form.InvokeRequired:
                form.BeginInvoke(WinForms.MethodInvoker(go))
            else:
                go()
        except Exception:
            import traceback
            traceback.print_exc()

    class _Pool:
        ctrls = {0: None}   # tab_key -> WebView2 控件(0 = 信息流主控件)

        def init(self):
            """等原生窗体就绪, 记录主控件, 打无边框缩放补丁(WS_THICKFRAME)。"""
            end = time.time() + 30
            form = None
            while time.time() < end:
                f = win.native
                if f is not None and f.IsHandleCreated:
                    form = f
                    break
                time.sleep(0.2)
            if form is None:
                raise RuntimeError("native window form not ready")
            box["form"] = form

            def init_ui():
                try:
                    import ctypes
                    u32 = ctypes.windll.user32
                    hwnd = form.Handle.ToInt32()
                    GWL_STYLE = -16
                    u32.SetWindowLongW(hwnd, GWL_STYLE,
                                       u32.GetWindowLongW(hwnd, GWL_STYLE)
                                       | 0x00040000 | 0x00010000 | 0x00020000)
                    u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                     0x0020 | 0x0001 | 0x0002 | 0x0004)
                    _Pool.ctrls[0] = form.webview
                except Exception:
                    import traceback
                    traceback.print_exc()
            _ui(init_ui)

        def create(self, url, tab_key):
            """新标签页控件(异步初始化; 标题/新页事件回调 app_window)。"""

            def go():
                try:
                    from Microsoft.Web.WebView2.WinForms import (
                        WebView2, CoreWebView2CreationProperties)
                    from System.Drawing import Color
                    from System import Uri
                    form = box["form"]
                    home = _Pool.ctrls[0]
                    cp = home.CreationProperties
                    props = CoreWebView2CreationProperties()
                    props.UserDataFolder = cp.UserDataFolder
                    props.AdditionalBrowserArguments = cp.AdditionalBrowserArguments
                    c = WebView2()
                    c.CreationProperties = props
                    try:
                        c.DefaultBackgroundColor = Color.FromArgb(255, 15, 17, 21)
                    except Exception:
                        pass
                    c.Location = home.Location
                    c.Size = home.Size
                    c.Anchor = (WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Bottom |
                                WinForms.AnchorStyles.Left | WinForms.AnchorStyles.Right)
                    c.Visible = False

                    def on_ready(_s, _a):
                        core = c.CoreWebView2
                        core.DocumentTitleChanged += lambda _s2, _a2: on_title(
                            tab_key, core.DocumentTitle)

                        def on_newwin(_s2, a2):
                            a2.Handled = True
                            on_newtab(tab_key, str(a2.get_Uri()))
                        core.NewWindowRequested += on_newwin
                    c.CoreWebView2InitializationCompleted += on_ready
                    form.Controls.Add(c)
                    _Pool.ctrls[tab_key] = c
                    c.EnsureCoreWebView2Async(None)
                    c.Source = Uri(url)
                except Exception:
                    import traceback
                    traceback.print_exc()
            _ui(go)

        def _ctrl(self, key):
            return _Pool.ctrls.get(key)

        def show(self, key):
            def go():
                try:
                    c = self._ctrl(key)
                    if c is not None:
                        c.Visible = True
                        c.Focus()
                except Exception:
                    pass
            _ui(go)

        def hide(self, key):
            def go():
                try:
                    c = self._ctrl(key)
                    if c is not None:
                        c.Visible = False
                except Exception:
                    pass
            _ui(go)

        def dispose(self, key):
            def go():
                try:
                    c = _Pool.ctrls.pop(key, None)
                    if c is not None:
                        c.Visible = False
                        box["form"].Controls.Remove(c)
                        c.Dispose()
                except Exception:
                    pass
            _ui(go)

    return _Pool()
