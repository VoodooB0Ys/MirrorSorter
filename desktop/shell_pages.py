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


# 自愈式智能深色: 脚本在页面内常驻定时器, 每1.2s重测背景亮度 ——
# 页面已深色(原生夜间/PreferredColorScheme生效) → 保持不动;
# 浅色页面(贴吧等不跟随系统的站) → 反色+色相旋转兜底(媒体类反转回原色);
# 主题晚于首次测量才应用(SPA懒应用)也会被自动追上; 白天模式由 mode 变量直接关断
# 统一主题脚本(自愈: 页面内定时器重测/重设):
# - B站: 用它自己的原生深色主题(cookie theme_style=dark, 官方顶栏开关写的就是这个),
#   效果是真正的原生深色; 白天模式清除该 cookie
# - 其他站点: 测量背景亮度, 浅色页面反色兜底(媒体类元素反转回原色), 已深色不动
_THEME_DARK_JS = r"""
window.__msx_theme_mode = 'dark';
(function(){
  function lum(rgb){
    var m = String(rgb).match(/[\d.]+/g);
    if (!m) return 255;
    if (m.length > 3 && parseFloat(m[3]) === 0) return -1;
    return 0.299*m[0] + 0.587*m[1] + 0.114*m[2];
  }
  function report(s){
    try { fetch("/api/shell_debug?s=" + encodeURIComponent(s), {cache:"no-store"}); } catch(e) {}
  }
  function cleanup(){
    var o = document.getElementById('__msx_dark');
    if (o) o.remove();
    document.documentElement.style.removeProperty('filter');
  }
  function tick(){
    if (window.__msx_theme_mode !== 'dark') { cleanup(); return; }
    var isBili = /(^|\.)bilibili\.com$/.test(location.hostname);
    if (isBili) {
      if (document.cookie.indexOf('theme_style=dark') === -1) {
        document.cookie = 'theme_style=dark; path=/; domain=.bilibili.com; max-age=31536000';
        report('bili-cookie-set');
        var n = +(sessionStorage.getItem('__msx_rl') || 0);
        if (n < 2) { sessionStorage.setItem('__msx_rl', String(n + 1)); location.reload(); return; }
      }
      cleanup();   // B站原生深色, 不做反色
      return;
    }
    var b = lum(getComputedStyle(document.body).backgroundColor);
    var h = lum(getComputedStyle(document.documentElement).backgroundColor);
    var base = (b >= 0 ? b : (h >= 0 ? h : 255));
    var o = document.getElementById('__msx_dark');
    if (base < 100) {
      if (o) o.remove();
      document.documentElement.style.removeProperty('filter');
    } else if (!o) {
      var st = document.createElement('style');
      st.id = '__msx_dark';
      st.textContent = 'html{filter:invert(0.90) hue-rotate(180deg);background:#fff}'
        + 'img,video,picture,canvas,svg,iframe,embed,object'
        + '{filter:invert(1) hue-rotate(180deg)}';
      (document.head || document.documentElement).appendChild(st);
      report('invert-injected ' + location.hostname);
    }
  }
  if (!window.__msx_theme_timer) window.__msx_theme_timer = setInterval(tick, 1500);
  tick();
})()
"""
_LIGHT_JS = r"""
window.__msx_theme_mode = 'light';
(function(){
  var old = document.getElementById('__msx_dark');
  if (old) old.remove();
  document.documentElement.style.removeProperty('filter');
  if (/(^|\.)bilibili\.com$/.test(location.hostname) &&
      document.cookie.indexOf('theme_style=dark') !== -1) {
    document.cookie = 'theme_style=; path=/; domain=.bilibili.com; max-age=0';
    var n = +(sessionStorage.getItem('__msx_rl') || 0);
    if (n < 2) { sessionStorage.setItem('__msx_rl', String(n + 1)); location.reload(); }
  }
})()
"""


# ---- 内置油猴脚本环境: Vue + GM 垫片 + 净化大师 + 用户自定义脚本 ----
# 通道: AddScriptToExecuteOnDocumentCreatedAsync(每次文档创建自动执行, MAIN 上下文,
# 等价于 document-start 的油猴脚本)。扩展 API(AddBrowserExtensionAsync)在部分
# 环境返回"不支持"(0x80070032), 因此不依赖它。
def _read_ext_file(name):
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.join(getattr(sys, "_MEIPASS", "") or "", "extensions"),
              os.path.join(here, "extensions")):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            try:
                return open(p, encoding="utf-8").read()
            except Exception:
                return ""
    return ""


def _user_scripts():
    """用户自定义油猴脚本: 数据目录 userscripts/*.user.js, 丢进去即生效。"""
    try:
        d = os.path.join(app.DATA_DIR, "userscripts")
        out = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith((".user.js", ".js")):
                    try:
                        out.append(open(os.path.join(d, fn), encoding="utf-8").read())
                    except Exception:
                        pass
        return out
    except Exception:
        return []


def _glob_match(pattern, host):
    """简化 @match 通配: *://*.bilibili.com/* 的 host 段 *.bilibili.com 含主域"""
    try:
        hostpat = pattern.split("://", 1)[-1].split("/", 1)[0]
        if hostpat.startswith("*."):
            dome = hostpat[2:]
            return host == dome or host.endswith("." + dome)
        return host == hostpat
    except Exception:
        return True


def _build_inject_script(port):
    """组装文档创建脚本: Vue + GM垫片 + 内置净化大师 + 用户脚本。"""
    vue = _read_ext_file("vue.global.prod.js")
    shim = _read_ext_file("gm-shim.js")
    builtin = _read_ext_file("bilibili-cleaner.user.js")
    if not shim:
        return ""
    parts = [
        "window.__MSX_PORT__ = %d;" % port,
        'try { fetch("http://127.0.0.1:" + window.__MSX_PORT__ +'
        ' "/api/shell_debug?s=userscript-env-injected", {mode: "no-cors"}); } catch (e) {}',
    ]
    if vue:
        parts.append(vue)   # var Vue = ...(全局, 供净化大师使用)
    if shim:
        parts.append(shim)
    if builtin:
        parts.append(
            'if ((function(){var h=location.hostname; return h==="bilibili.com"||'
            'h.endsWith(".bilibili.com")})()) {\n' + builtin + '\n}')
    for us in _user_scripts():
        parts.append('try { (function(){\n' + us + '\n})(); }'
                     ' catch (e) { console.warn("[msx] userscript", e); }')
    return "\n;\n".join(parts)


def create_pool(win, on_title, on_newtab, theme_getter=None, port=8000):
    """创建控件池。须在窗口壳启动后调用(任意线程, 内部自行 marshal)。

    on_title(tab_key, title) / on_newtab(tab_key, url): 站点控件的
    DocumentTitleChanged / NewWindowRequested(UI 线程)回调。
    返回管理器: init/create/show/hide/dispose —— 均以 tab_key 为键
    (key 0 = 信息流主控件); 控件对象由池内部持有。
    """
    import System.Windows.Forms as WinForms  # noqa: F401  提前加载 CLR(pythonnet)
    box = {"form": None, "theme": None, "stopping": False, "port": port}

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
        _bili_reloaded = {}  # id(ctrl) -> 上次重载时的主题(B站深色cookie生效防循环)

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
                    # ---- B站原生深色: 官方深色开关就是 theme_style cookie
                    #      (顶栏头像区的深浅切换写它), 用 CookieManager 直接设置 ----
                    try:
                        cm = core.CookieManager
                        want = "dark" if dark else ""
                        if want:
                            cm.AddOrUpdateCookie(cm.CreateCookie(
                                "theme_style", "dark", ".bilibili.com", "/"))
                            cm.AddOrUpdateCookie(cm.CreateCookie(
                                "b_night_number", "1", ".bilibili.com", "/"))
                        else:
                            cm.DeleteCookies("theme_style", "https://www.bilibili.com")
                            cm.DeleteCookies("b_night_number", "https://www.bilibili.com")
                        src = str(c.Source or "")
                        # 当前页在B站且该标签还没为此主题重载过 → 重载一次让原生深色生效
                        # (防循环: 同一标签同一主题只重载一次)
                        if ("bilibili.com" in src
                                and self._bili_reloaded.get(id(c)) != dark):
                            self._bili_reloaded[id(c)] = dark
                            core.Reload()
                    except Exception:
                        pass   # 关窗时序中控件可能刚好销毁, 静默即可
                    # ---- 其他站点: 智能反色兜底(自愈定时器在脚本内) ----
                    core.ExecuteScriptAsync(_THEME_DARK_JS if dark else _LIGHT_JS)
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
                    from System.Drawing import Color as _C
                    form.BackColor = _C.FromArgb(15, 17, 21)   # 控件缝隙不露窗体白底
                    u32 = ctypes.windll.user32
                    hwnd = form.Handle.ToInt32()
                    # 缩放走自建贴边热区(edge 面板), 不再用 THICKFRAME ——
                    # THICKFRAME 会让 DWM 在 Win10 上画一圈浅色边框线(不可调色), 白线的来源
                    try:   # 关掉 DWM 非客户区渲染
                        val = ctypes.c_int(2)   # DWMWA_NCRENDERING_POLICY = DISABLED
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, 2, ctypes.byref(val), ctypes.sizeof(val))
                    except Exception:
                        pass
                    try:   # Win10 给所有窗口画 1px 系统色边框(浅色主题下发白) → 强制深色边框
                        on = ctypes.c_int(1)   # TRUE
                        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE(新旧两个属性号)
                            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                                hwnd, attr, ctypes.byref(on), ctypes.sizeof(on))
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
                        try:   # 内置油猴环境: 净化大师 + 用户自定义脚本(每次文档创建自动执行)
                            inj = _build_inject_script(box.get("port") or 8000)
                            if inj:
                                core.AddScriptToExecuteOnDocumentCreatedAsync(inj)
                        except Exception:
                            import traceback
                            traceback.print_exc()

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
