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


# 智能深色: 页面背景已是深色(如 PreferredColorScheme 生效的B站夜间) → 不反色;
# 浅色页面(贴吧等不跟随系统的站) → 反色+色相旋转, 媒体类元素反转回原色
_SMART_JS = r"""
(function(){
  function lum(rgb){
    var m = String(rgb).match(/[\d.]+/g);
    if (!m) return 255;
    if (m.length > 3 && parseFloat(m[3]) === 0) return -1;
    return 0.299*m[0] + 0.587*m[1] + 0.114*m[2];
  }
  var b = lum(getComputedStyle(document.body).backgroundColor);
  var h = lum(getComputedStyle(document.documentElement).backgroundColor);
  var base = (b >= 0 ? b : (h >= 0 ? h : 255));
  var old = document.getElementById('__msx_dark');
  if (base < 100) {
    if (old) old.remove();
    document.documentElement.style.removeProperty('filter');
    return;
  }
  if (old) return;
  var st = document.createElement('style');
  st.id = '__msx_dark';
  st.textContent = 'html{filter:invert(0.90) hue-rotate(180deg);background:#fff}'
    + 'img,video,picture,canvas,svg,iframe,embed,object'
    + '{filter:invert(1) hue-rotate(180deg)}';
  (document.head || document.documentElement).appendChild(st);
})()
"""
_LIGHT_JS = """
(function(){
  var old = document.getElementById('__msx_dark');
  if (old) old.remove();
  document.documentElement.style.filter = '';
})()
"""


def create_pool(win, on_title, on_newtab, theme_getter=None):
    """创建控件池。须在窗口壳启动后调用(任意线程, 内部自行 marshal)。

    on_title(tab_key, title) / on_newtab(tab_key, url): 站点控件的
    DocumentTitleChanged / NewWindowRequested(UI 线程)回调。
    返回管理器: init/create/show/hide/dispose —— 均以 tab_key 为键
    (key 0 = 信息流主控件); 控件对象由池内部持有。
    """
    import System.Windows.Forms as WinForms  # noqa: F401  提前加载 CLR(pythonnet)
    box = {"form": None, "theme": None, "stopping": False}

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

        def _is_dark(self):
            try:
                return (theme_getter() or "dark") != "light"
            except Exception:
                return True

        def _apply_theme(self, c, dark):
            """给站点页面注入/移除自动深色(反色+色相旋转, 媒体类元素反转回原色)。"""
            def go():
                try:
                    core = c.CoreWebView2
                    if core is None:
                        return
                    try:   # 站点自身支持 prefers-color-scheme 的跟随(如已适配暗色的站点)
                        from Microsoft.Web.WebView2.Core import CoreWebView2PreferredColorScheme
                        core.Profile.PreferredColorScheme = CoreWebView2PreferredColorScheme(
                            2 if dark else 1)   # Dark/Light
                    except Exception:
                        pass
                    core.ExecuteScriptAsync(_DARK_JS if dark else _LIGHT_JS)
                except Exception:
                    import traceback
                    print("[theme] ERROR:", flush=True)
                    traceback.print_exc()
            _ui(go)

        def _theme_watch(self):
            """轮询主题(主页切黑夜/白天会实时写库) → 对所有站点页面重新应用。"""
            import time as _t
            while not box["stopping"]:
                try:
                    dark = self._is_dark()
                    if dark != box["theme"]:
                        box["theme"] = dark
                        for k, c in list(_Pool.ctrls.items()):
                            if k != 0 and c is not None:
                                self._apply_theme(c, dark)
                except Exception:
                    pass
                _t.sleep(3)

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
            box["theme"] = self._is_dark()
            threading.Thread(target=self._theme_watch, daemon=True,
                             name="msx-theme-watch").start()

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
                    try:   # THICKFRAME 会让 DWM 画出一条浅色边框线(Win10 不可调色) → 关掉
                        #       非客户区渲染; 边缘缩放热区仍在
                        val = ctypes.c_int(2)   # DWMWA_NCRENDERING_POLICY = DISABLED
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, 2, ctypes.byref(val), ctypes.sizeof(val))
                    except Exception:
                        pass
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

                        def on_nav_done(_s2, _a2):   # 每次整页加载完按当前主题补注入
                            self._apply_theme(c, self._is_dark())
                        core.NavigationCompleted += on_nav_done
                        self._apply_theme(c, self._is_dark())
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
                    import traceback
                    traceback.print_exc()
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
