# -*- coding: utf-8 -*-
"""洞鉴虚明 · 桌面窗口壳(内置浏览器·标签页版, v0.2.9 起)。

把服务装进自带的应用窗口: pywebview 驱动系统自带的 WebView2(Edge Chromium 内核),
- 双击启动 → 弹出应用主窗口, 不再依赖外部浏览器;
- 点 feed 卡片/消息/账号等任何视频·帖子链接 → 主窗口内**新增一个内置浏览器标签页**
  (窗口顶部原生"浏览器式"标签栏, 信息流为固定首页标签, 不产生任何新窗口);
- 未装 WebView2 运行时 → 先跑随包的微软官方引导安装包自动装上(一次UAC授权, 需联网);
  缺 pywebview 库或安装失败/被拒 → 自动回退外部浏览器模式(旧行为, 弹提示);
- `python app_window.py --server` → 纯服务模式(等同 python app.py --auto-open)。

实现说明: pywebview 一个窗口只有一个 WebView, 标签页 = Python 端维护的标签表 +
切换时导航同一 WebView; 标签栏是窗口顶部的原生部件(见 shell_tabstrip.py), 不向
页面注入任何元素。已知取舍: 切换标签会重新加载页面(视频播放不跨标签续播);
站内链接在当前标签内正常跳转。

开源依赖: pywebview (MIT, github.com/r0x0r/pywebview), Windows 走 WebView2 运行时。
"""
import os
import re
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import app  # noqa: E402  本地服务(纯标准库)

APP_NAME = "洞鉴虚明"
APP_VER = app.APP_VER

# ── 整屏截屏黑屏根治(浏览器级, v0.3.1.7dsk) ──────────────────────────────────
# 症状: 微信/QQ/PrntScr 等 GDI BitBlt 截屏, 播放器区域黑屏(偶发)。
# 根因: Chromium 会把视频(以及不透明轴对齐无滤镜的简单四边形, 如超分画布)提升为
# DirectComposition 硬件 overlay 平面——屏幕上可见, 但 GDI 截屏取不到 overlay,
# 截到的就是下面的黑色播放器背景。v0.3.1.6 已在页内给画布加 0.5px 圆角破坏资格,
# 但 SR 拆管窗口期"可见的原视频"仍会被提升(v0.3.1.6 把视频改回可见后新开的口子)。
# 这里从引擎层面禁掉视频 overlay 提升——所有 WebView2 控件(主控件+标签页)一并生效。
# 代价: 视频合成多走一点 GPU 主通道, 省电收益略降, 桌面应用可接受。
# setdefault: 用户若自己设了该环境变量则以用户的为准。
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                      "--disable-direct-composition-video-overlays")
MAIN_W, MAIN_H = 1440, 920
MAIN_MIN = (1080, 700)
BG_DARK = "#0f1115"   # 与 web/style.css 暗色 --bg 一致, 避免窗口加载瞬间白闪

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _norm_nav_url(raw):
    """地址栏输入 → 可导航 URL; 不合法返回 None。
    无协议头自动补 https://; 只放行 http(s) 且主机名像主机(有点分/localhost)。"""
    u = (raw or "").strip()
    if not u:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
        u = "https://" + u
    if not _URL_RE.match(u):
        return None   # file:/javascript:/data: 等一律拒绝(安全约束同 open_in_app)
    try:
        p = urlparse(u)
    except ValueError:
        return None
    host = p.hostname or ""
    if not host or " " in host:
        return None
    if host != "localhost" and "." not in host:
        return None
    return u
_WV2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"   # WebView2 Evergreen 运行时
_WV2_BOOTSTRAP = ("WebView2Runtime", "MicrosoftEdgeWebview2Setup.exe")   # 微软官方引导包(随包分发)


def webview2_available():
    """检测系统 WebView2 运行时: 注册表 pv 值, 或环境变量指向的可用内核
    (Edge 浏览器兜底, 见 find_edge_browser_dir)。检测不了/没装都按缺失。"""
    if os.name != "nt":
        return False
    env_dir = os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER") or ""
    if env_dir and (os.path.isfile(os.path.join(env_dir, "msedgewebview2.exe"))
                    or os.path.isfile(os.path.join(env_dir, "msedge.exe"))):
        return True
    try:
        import winreg
    except ImportError:
        return False
    for root, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\%s" % _WV2_CLIENT_ID),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\%s" % _WV2_CLIENT_ID),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\%s" % _WV2_CLIENT_ID),
    ):
        try:
            with winreg.OpenKey(root, path) as k:
                pv = str(winreg.QueryValueEx(k, "pv")[0]).strip()
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def find_edge_browser_dir():
    """找已安装的 Edge 浏览器内核目录(取最高版本, 含 msedge.exe)。
    WebView2 加载器官方支持直接用 Edge 浏览器二进制充当运行时 —— 运行时装不上
    (EdgeUpdate 机制损坏)的机器借此免安装获得完整 WebView2 能力。找不到返回 None。"""
    if os.name != "nt":
        return None
    roots = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(var)
        if base:
            roots.append(os.path.join(base, "Microsoft", "Edge", "Application"))
    best, best_ver = None, ()
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", name):
                continue
            p = os.path.join(root, name)
            if os.path.isfile(os.path.join(p, "msedge.exe")):
                ver = tuple(int(x) for x in name.split("."))
                if ver > best_ver:
                    best, best_ver = p, ver
    return best


def _bundled_resource(*parts):
    """随包资源定位: PyInstaller onefile 在 _MEIPASS 解包目录; 源码运行在脚本目录。"""
    rel = os.path.join(*parts)
    for base in (getattr(sys, "_MEIPASS", ""), HERE):
        if base:
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return p
    return None


def _run_elevated_wait(args, timeout_s=600, verb="runas"):
    """ShellExecute 拉起外部程序并等待结束, 返回退出码(负数=启动失败/超时)。
    verb="runas" 强制走 UAC 提权 —— 引导安装包清单是 asInvoker, 用 subprocess 直接
    拉起会以普通权限静默失败(全机安装需要管理员), 连 UAC 都不弹(线上实测)。"""
    import ctypes
    from ctypes import wintypes

    class _SEEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", wintypes.LPVOID),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC = 0x40, 0x100
    sei = _SEEI()
    sei.cbSize = ctypes.sizeof(_SEEI)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    sei.lpVerb = verb
    sei.lpFile = args[0]
    sei.lpParameters = " ".join(args[1:]) if len(args) > 1 else None
    sei.nShow = 1   # SW_SHOWNORMAL(/silent 无界面, 不弹窗)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return -1   # 拒绝提权/被策略拦截
    try:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            code = wintypes.DWORD(0)
            if ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 500) == 0:
                ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
                return code.value
        return -2   # 超时
    finally:
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)


def install_webview2_runtime():
    """系统缺 WebView2 运行时 → 跑随包分发的微软官方 Evergreen 引导安装包。
    verb=runas 强制提权(UAC 弹窗), 全机安装必须管理员; 成功后复检返回 True。
    取消授权/断网/被精简系统拦服务 → False, 调用方走外部浏览器兜底(旧行为)。"""
    src = _bundled_resource(*_WV2_BOOTSTRAP)
    if not src:
        return False
    _msg_box("首次使用需要安装 WebView2 运行时（微软官方组件，约需 1-2 分钟）。\n\n"
             "点「确定」开始；若弹出「用户账户控制」窗口请点「是」。\n"
             "需要联网；跳过将以外部浏览器模式打开。")
    code = _run_elevated_wait([src, "/silent", "/install"], timeout_s=600)
    if not (code == 0 and webview2_available()):
        # 实测故障: 精简系统 %TEMP% 缺失/不可写 → 微软安装器报"无法创建临时目录"(0x80040902)。
        # 把 TEMP/TMP 指到 Windows\Temp(提权子进程可写)重试一次; 提权子进程继承本进程环境。
        fallback = os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Windows", "Temp")
        old_env = (os.environ.get("TEMP"), os.environ.get("TMP"))
        try:
            if os.path.isdir(fallback):
                os.environ["TEMP"] = fallback
                os.environ["TMP"] = fallback
                code = _run_elevated_wait([src, "/silent", "/install"], timeout_s=600)
        finally:
            if old_env[0] is not None:
                os.environ["TEMP"] = old_env[0]
            if old_env[1] is not None:
                os.environ["TMP"] = old_env[1]
    if not (code == 0 and webview2_available()):
        # 仍失败 → 交互式安装(弹微软官方界面, 用户能看到真实原因)
        code = _run_elevated_wait([src], timeout_s=900)
    if code == 0 and webview2_available():
        return True
    _msg_box("WebView2 运行时安装未完成（错误码 %s）。\n\n"
             "常见原因: 系统临时目录(%%TEMP%%)不可用、精简系统禁用 Edge 更新服务、安全软件拦截。\n"
             "替代方案: 安装 Microsoft Edge 浏览器后重开本应用（应用会自动借用其内核，\n"
             "无需安装 WebView2 运行时）；本次将以外部浏览器模式打开，下次启动会再次尝试。" % code)
    return False


def have_pywebview():
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def _msg_box(text):
    """无控制台场景(frozen windowed)也能弹的提示框。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, 0x40)   # MB_ICONINFORMATION
    except Exception:
        pass


class ShellApi:
    """暴露给页面的 JS 桥: window.pywebview.api.* —— 兼内置浏览器标签页管理器。

    单窗口单 WebView: 标签页 = Python 端标签表, 切换 = 导航同一 WebView;
    标签条由 Python 在每次页面加载后注入渲染(应用页与远程站都显示)。

    安全: open_in_app/open_external 只接受 http(s):// URL —— 页面侧无法借此
    触发本地文件或命令。
    """

    HOME_ID = 0   # 信息流(应用本体)固定为 0 号标签, 不可关

    def __init__(self):
        self._win = None          # 主窗口, attach() 绑定
        self._app_url = None
        self._pages = None        # WebView2 控件池, bind_pages() 注入
        self._visible_id = self.HOME_ID
        self._strip_update = None  # 原生标签栏重绘回调, bind_strip() 注入
        self._navbar_update = None  # 导航工具栏重绘回调, bind_navbar() 注入
        self._lock = threading.Lock()
        self._tabs = [{"id": self.HOME_ID, "url": None, "title": "信息流",
                       "pinned": True, "opened": True}]
        self._active = self.HOME_ID
        self._next_id = 1

    def attach(self, win, app_url):
        """主窗口创建后绑定窗口引用(供控件池 marshal 与窗口按钮使用)。"""
        self._win = win
        self._app_url = app_url
        self._tabs[0]["url"] = app_url

    # ---- 桥: 前端探测(存在即说明跑在桌面壳里) ----
    def shell_info(self):
        return {"name": "desktop-shell", "version": APP_VER.lstrip("v"),
                "embedded_browser": True, "tabs": True}

    # ---- 桥: 打开/切换/关闭标签页(app.js openExternal 走 open_in_app) ----
    def open_in_app(self, url, title=""):
        url = (url or "").strip()
        if not _URL_RE.match(url):
            return "bad-url"
        with self._lock:
            for t in self._tabs:
                if t["url"] == url:   # 同一链接只占一个标签, 重复点=切过去(不重载)
                    self._active = t["id"]
                    tab = t
                    break
            else:
                tab = {"id": self._next_id, "url": url,
                       "title": (title or url)[:24], "pinned": False, "opened": False}
                self._tabs.append(tab)
                self._active = self._next_id
                self._next_id += 1
        self._activate(tab)
        self._render()   # 即时反馈: 标签栏立刻高亮新标签(不等页面加载完)
        return "ok"

    def tab_switch(self, tid=None):
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return "bad-id"
        with self._lock:
            t = next((x for x in self._tabs if x["id"] == tid), None)
            if t is None:
                return "gone"
            self._active = tid
        self._activate(t)
        self._render()
        return "ok"

    def tab_close(self, tid=None):
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return "bad-id"
        with self._lock:
            idx = next((i for i, x in enumerate(self._tabs) if x["id"] == tid), None)
            if idx is None:
                return "gone"
            if self._tabs[idx]["pinned"]:
                return "pinned"
            dead = self._tabs.pop(idx)
            if self._active == tid:   # 关的是当前页 → 激活前一个, 没有则回信息流
                self._active = self._tabs[max(0, idx - 1)]["id"]
            nxt = next(x for x in self._tabs if x["id"] == self._active)
        self._activate(nxt)          # 先显示邻居, 再销毁旧控件(避免黑屏)
        if self._pages is not None:
            self._pages.dispose(tid)
            # 销毁兜底: WebView2(DirectComposition)销毁偶发打断窗口合成层, 内容区整块
            # 变黑而标签栏正常(用户截图) —— 强制再补一次邻居可见+焦点, 让其重新present。
            self._pages.show(self._active)
        self._render()
        return "ok"

    def open_external(self, url):
        """用户显式要求用系统浏览器时(标签条 ⧉ 按钮)。"""
        url = (url or "").strip()
        if not _URL_RE.match(url):
            return "bad-url"
        webbrowser.open(url)
        return "ok"

    def on_side_button(self, back=True):
        """鼠标侧键(后退/前进): 当前标签页内回退/前进; 后退到本标签最早的页(无历史)
        时整个标签视为一次"返回" —— 关掉它回到来源标签/信息流。信息流是应用本体,
        没有"返回"概念, 忽略。仅在 UI 线程调用(由 _install_mouse_side_buttons 触发)。"""
        pages = self._pages
        if pages is None:
            return
        with self._lock:
            t = next((x for x in self._tabs if x["id"] == self._active), None)
            tid = t["id"] if t else None
            pinned = bool(t and t["pinned"])
        if tid is None or pinned:
            return
        if pages.navigate_history(tid, back):
            return
        if not back:
            return   # 前进无历史: 无事可做
        self.tab_close(tid)

    # ---- 导航工具栏(navbar 按钮/地址栏走这套; 语义与鼠标侧键不同 —— 首页无历史
    #      时按钮禁用/无事发生, 绝不"关标签") ----
    def _active_id(self):
        with self._lock:
            return self._active

    def nav_back(self):
        pages = self._pages
        if pages is not None:
            pages.navigate_history(self._active_id(), True)
        return "ok"

    def nav_forward(self):
        pages = self._pages
        if pages is not None:
            pages.navigate_history(self._active_id(), False)
        return "ok"

    def nav_reload(self):
        pages = self._pages
        if pages is not None:
            pages.reload(self._active_id())
        return "ok"

    def nav_goto(self, url):
        """地址栏回车: 当前标签页内导航(不新开标签, 与浏览器一致)。"""
        u = _norm_nav_url(url)
        if u is None:
            return "bad-url"
        pages = self._pages
        if pages is None:
            return "no-pages"
        tid = self._active_id()
        pages.goto(tid, u)
        pages.show(tid)   # 焦点交回页面(浏览器习惯: 导航后地址栏失焦)
        return "ok"

    # ---- 内部 ----
    def _activate(self, tab):
        """显示 tab(无控件则异步创建), 隐藏当前控件 —— 纯可见性切换, 页面零重载。"""
        pages = self._pages
        if pages is None:
            return
        with self._lock:
            prev_id = self._visible_id
            self._visible_id = tab["id"]
        if not tab["pinned"] and not tab["opened"]:
            tab["opened"] = True
            pages.create(tab["url"], tab["id"])
        if prev_id != tab["id"]:
            pages.hide(prev_id)
        pages.show(tab["id"])
        pages.push_nav(tab["id"])   # 切标签后补推导航状态(防地址栏显示旧 URL)

    def update_title(self, tab_key, title):
        """站点控件的 DocumentTitleChanged 回调 → 刷新标签标题。"""
        title = str(title or "").strip()[:24]
        if not title:
            return
        with self._lock:
            t = next((x for x in self._tabs if x["id"] == tab_key), None)
            if t is not None and not t["pinned"]:
                t["title"] = title
        self._render()

    def bind_pages(self, pages):
        """注入 WebView2 控件池(shell_pages.create_pool)并显示首页标签。"""
        self._pages = pages
        self._activate(self._tabs[0])
        self._render()

    def bind_strip(self, update_fn):
        """注入原生标签栏重绘回调(shell_tabstrip.install_strip 的返回值)。"""
        self._strip_update = update_fn
        self._render()

    def bind_navbar(self, update_fn):
        """注入导航工具栏重绘回调(shell_navbar.install_navbar 的返回值)。"""
        self._navbar_update = update_fn
        self._push_nav("", False, False)   # 初值: 首页是本地服务, 地址栏留空

    def _on_nav(self, tab_key, url, can_back, can_fwd):
        """控件池的导航状态回调(HistoryChanged/SourceChanged/push_nav)。
        只反映激活标签 —— 后台标签的导航不刷地址栏。"""
        with self._lock:
            active = self._active
            pinned = next((x["pinned"] for x in self._tabs if x["id"] == active), True)
        if tab_key != active:
            return
        # 信息流是本地服务(127.0.0.1), 网址没有意义 —— 地址栏留空(用户要求)
        self._push_nav("" if pinned else url, can_back, can_fwd)

    def _push_nav(self, url, can_back, can_fwd):
        fn = self._navbar_update
        if fn is None:
            return
        try:
            fn(str(url or ""), bool(can_back), bool(can_fwd))
        except Exception:
            pass

    def _render(self):
        """把标签表同步到原生标签栏(跨线程安全, 由 strip 侧 marshal 到 UI 线程)。"""
        fn = self._strip_update
        if fn is None:
            return
        try:
            with self._lock:
                tabs = [{"id": t["id"], "title": t["title"] or "页面",
                         "closable": not t["pinned"]} for t in self._tabs]
                active = self._active
            fn(tabs, active)
        except Exception:
            pass


def _start_server_thread():
    """起服务(或复用已运行的实例)。返回 (server_or_None, port)。"""
    server, port = app.run_server(auto_open=False)
    if server is not None:
        threading.Thread(target=server.serve_forever, daemon=True,
                         name="http-server").start()
    return server, port


def _serve_forever_cli(server):
    """--server 模式: 与 python app.py 一致的阻塞语义(Ctrl+C 退出并收尾)。"""
    try:
        server.serve_forever()
    finally:
        app.shutdown(server)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    e2e_url = None
    if "--e2e-open" in argv:   # 测试钩子: 启动后自动经桥开一个内置浏览器标签页
        try:
            e2e_url = argv[argv.index("--e2e-open") + 1]
        except IndexError:
            e2e_url = None
    # 悬浮球模式: frozen exe 再入自身时走这里; 源码模式 float_ball_spawn 直接调 app.py
    if "--float-ball" in argv:
        try:
            _port = int(argv[argv.index("--float-ball") + 1])
        except (ValueError, IndexError):
            _port = 8000
        try:
            app.run_float_ball(_port)
        except Exception:
            import traceback
            try:
                with open(os.path.join(app.DATA_DIR, "float_ball_err.log"), "w",
                          encoding="utf-8") as _f:
                    traceback.print_exc(file=_f)
            except Exception:
                pass
        return 0
    if "--server" in argv:   # 纯服务模式(不建窗口)
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--server"]
        server, port = app.run_server(
            auto_open=("--auto-open" in sys.argv) or bool(getattr(sys, "frozen", False)))
        if server is None:
            return 0 if port is not None else 1
        _serve_forever_cli(server)
        return 0
    return shell_main(e2e_url=e2e_url)


def _install_mouse_side_buttons(api, win):
    """低级鼠标钩子(WH_MOUSE_LL)拦截本窗口内的侧键(后退/前进), 不交给 WebView2
    原生处理。

    原生路径: WebView2 收到 XButton 后在浏览器进程内同步执行 GoBack, 与本壳的
    无边框窗口 + 控件可见性切换叠加时有概率整窗卡死(用户实测)。侧键语义改由
    ShellApi.on_side_button 在 UI 线程实现: 标签页内回退, 到头关标签回信息流。
    钩子按光标落点查根窗口, 只吞本窗口的侧键, 不影响其他应用; 独立线程装钩并
    跑消息泵 —— 低级钩子所在线程不泵消息会被系统按超时静默摘除。pythonnet 3
    无法子类化 IMessageFilter(WinForms 消息过滤器方案不可行), 故走 ctypes。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WH_MOUSE_LL = 14
    WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK = 0x020B, 0x020C, 0x020D
    GA_ROOT = 2

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    LOWPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                 ctypes.c_size_t, ctypes.c_ssize_t)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
    root = int(win.native.Handle.ToInt64())   # pythonnet IntPtr 无 int() 转换

    def _in_our_window(pt):
        try:
            h = user32.WindowFromPoint(wintypes.POINT(pt.x, pt.y))
            if not h:
                return False
            top = user32.GetAncestor(h, GA_ROOT)
            return int(top or 0) == root or int(h or 0) == root
        except Exception:
            return False

    def _proc(n_code, w_param, l_param):
        try:
            if n_code >= 0 and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP,
                                           WM_XBUTTONDBLCLK):
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if _in_our_window(info.pt):
                    if w_param == WM_XBUTTONDOWN:
                        # mouseData 高16位: 1=XBUTTON1(后退) 2=XBUTTON2(前进)
                        back = ((info.mouseData >> 16) & 0xFFFF) == 1
                        try:
                            import System.Windows.Forms as WinForms
                            form = api._win.native
                            form.BeginInvoke(WinForms.MethodInvoker(
                                lambda: api.on_side_button(back=back)))
                        except Exception:
                            pass
                    return 1   # 吞掉: WebView2/页面收不到这套按键
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    proc_ref = LOWPROC(_proc)   # 持引用防 GC(钩子回调悬空会崩进程)

    def _run():
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc_ref, None, 0)
        if not hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

    threading.Thread(target=_run, daemon=True,
                     name="msx-side-btn-hook").start()


def shell_main(e2e_url=None):
    """窗口壳主流程: 起/复用服务 → WebView2 检测 → 主窗口(含标签页) → 全关后收尾。

    e2e_url 仅测试用(_e2e_shell.py): 主窗口就绪后自动走一次 open_in_app 桥调用,
    与用户点击卡片进入内置浏览器标签页的代码路径完全一致, 不碰真实鼠标。
    """
    server, port = _start_server_thread()
    if port is None:
        _msg_box("端口 8000/8010/8020 均被占用，无法启动。")
        return 1
    url = "http://127.0.0.1:%d" % port
    py_ok = have_pywebview()
    if py_ok and not webview2_available():
        # 缺运行时: 优先借用已装的 Edge 浏览器内核(免安装, 零系统改动);
        # 真没有 Edge 才跑随包的官方引导安装包(一次授权, 约1-2分钟)
        edge_dir = find_edge_browser_dir()
        if edge_dir:
            os.environ["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = edge_dir
            print("WebView2 运行时缺失 → 借用 Edge 浏览器内核: %s" % edge_dir)
        else:
            install_webview2_runtime()
    if not py_ok or not webview2_available():
        # 降级: 保留服务的旧行为(外部浏览器), 绝不白屏
        print("未检测到内置浏览器组件(WebView2 运行时), 回退外部浏览器模式: %s" % url)
        _msg_box("未检测到 WebView2 运行时，本次将以外部浏览器模式打开。\n\n"
                 "如需应用窗口与内置浏览器：安装 Microsoft Edge 浏览器后重开本应用\n"
                 "（应用会自动借用其内核，无需安装 WebView2 运行时）；\n"
                 "或到微软官网下载 WebView2 Runtime Evergreen 安装包。")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown(server)
        return 0
    import webview
    # 站点内 target=_blank/弹新页 → 当前标签页内导航, 绝不放去系统浏览器、绝不弹新窗口
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    api = ShellApi()
    win = webview.create_window("%s %s" % (APP_NAME, APP_VER), url,
                                width=MAIN_W, height=MAIN_H, min_size=MAIN_MIN,
                                background_color=BG_DARK, js_api=api,
                                frameless=True, easy_drag=False)   # easy_drag=False: 只允许标签栏拖动, 网页区域拖拽不再移动窗口
    api.attach(win, url)

    def gui_ready():
        # GUI 就绪(在 pywebview 的后台线程): 装控件池与标签栏, 再跑测试钩子
        try:
            from shell_pages import create_pool
            # 站点页面深浅跟随主界面: 读正式设置键(applyTheme 每次切主题实时落库),
            # 旧存档无 ui_theme 时回退悬浮球镜像键
            def _current_theme():
                v = app.sget("ui_theme")
                if v not in ("light", "dark"):
                    v = "light" if app.sget("float_ball_light") == "1" else "dark"
                return v
            pages = create_pool(
                win, api.update_title, lambda _k, u: api.open_in_app(u),
                theme_getter=_current_theme, port=port, on_nav=api._on_nav)
            pages.init()          # 等窗体就绪 + WS_THICKFRAME 无边框缩放补丁
            api.bind_pages(pages)
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            from shell_tabstrip import install_strip
            strip_update = install_strip(
                win, api, accent_getter=lambda: app.sget("float_ball_accent"),
                bg_getter=lambda: app.sget("ui_bg_scheme"))
            api.bind_strip(strip_update)
        except Exception:
            import traceback
            traceback.print_exc()
            strip_update = None
        try:
            from shell_navbar import install_navbar
            # top=标签栏高度(install_strip 暴露): 工具栏紧贴标签栏下沿
            nav_update = install_navbar(
                win, api, top=getattr(strip_update, "bar_h", 32) if strip_update else 32,
                bg_getter=lambda: app.sget("ui_bg_scheme"))
            api.bind_navbar(nav_update)
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            _install_mouse_side_buttons(api, win)
        except Exception:
            import traceback
            traceback.print_exc()
        if e2e_url:
            time.sleep(8)   # 等主窗口与服务就绪
            api.open_in_app(e2e_url, "E2E 视频标签页")

            def dump_controls():
                try:
                    import System.Windows.Forms as WinForms
                    f = win.native
                    print("[dump] client=%s" % f.ClientSize, flush=True)
                    for i in range(f.Controls.Count):
                        c = f.Controls[i]
                        try:
                            print("[dump] z=%d %s vis=%s loc=%s size=%s dock=%s" % (
                                i, c.GetType().Name, c.Visible, c.Location, c.Size,
                                c.Dock), flush=True)
                        except Exception as _e:
                            print("[dump] z=%d err %r" % (i, _e), flush=True)
                except Exception:
                    import traceback
                    traceback.print_exc()
            time.sleep(6)
            import threading as _th
            _th.Thread(target=dump_controls, daemon=True).start()

    # 非无痕: 内置浏览器标签页里的登录态(B站等)跨重启保留, 数据落用户目录
    webview.start(func=gui_ready, private_mode=False,
                  debug=os.environ.get("MSX_DEBUG") == "1")
    # start() 返回 = 窗口已关闭 → 收尾退出(收球/停线程池/关服务)
    app.shutdown(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
