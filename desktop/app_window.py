# -*- coding: utf-8 -*-
"""洞鉴虚明 · 桌面窗口壳(内置浏览器·标签页版, v0.2.9 起)。

把服务装进自带的应用窗口: pywebview 驱动系统自带的 WebView2(Edge Chromium 内核),
- 双击启动 → 弹出应用主窗口, 不再依赖外部浏览器;
- 点 feed 卡片/消息/账号等任何视频·帖子链接 → 主窗口内**新增一个内置浏览器标签页**
  (窗口顶部原生"浏览器式"标签栏, 信息流为固定首页标签, 不产生任何新窗口);
- 未装 WebView2 运行时或缺 pywebview 库 → 自动回退外部浏览器模式(旧行为, 弹提示);
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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import app  # noqa: E402  本地服务(纯标准库)

APP_NAME = "洞鉴虚明"
APP_VER = app.APP_VER
MAIN_W, MAIN_H = 1440, 920
MAIN_MIN = (1080, 700)
BG_DARK = "#0f1115"   # 与 web/style.css 暗色 --bg 一致, 避免窗口加载瞬间白闪

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_WV2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"   # WebView2 Evergreen 运行时


def webview2_available():
    """检测系统 WebView2 运行时(注册表 pv 值)。检测不了/没装都按缺失 → 回退外部浏览器。"""
    if os.name != "nt":
        return False
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
        self._render()
        return "ok"

    def open_external(self, url):
        """用户显式要求用系统浏览器时(标签条 ⧉ 按钮)。"""
        url = (url or "").strip()
        if not _URL_RE.match(url):
            return "bad-url"
        webbrowser.open(url)
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
    if not have_pywebview() or not webview2_available():
        # 降级: 保留服务的旧行为(外部浏览器), 绝不白屏
        print("未检测到内置浏览器组件(WebView2 运行时), 回退外部浏览器模式: %s" % url)
        _msg_box("未检测到 WebView2 运行时，本次将以外部浏览器模式打开。\n\n"
                 "如需应用窗口与内置浏览器，请安装 Microsoft Edge WebView2 Runtime\n"
                 "(Win10/11 一般已自带；或到微软官网下载 Evergreen 安装包)。")
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
                theme_getter=_current_theme)
            pages.init()          # 等窗体就绪 + WS_THICKFRAME 无边框缩放补丁
            api.bind_pages(pages)
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            from shell_tabstrip import install_strip
            api.bind_strip(install_strip(
                win, api, accent_getter=lambda: app.sget("float_ball_accent")))
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
