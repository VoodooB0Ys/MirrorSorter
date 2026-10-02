# -*- coding: utf-8 -*-
"""洞鉴虚明 · 桌面窗口壳(内置浏览器版, v0.2.9 起)。

把服务装进自带的应用窗口: pywebview 驱动系统自带的 WebView2(Edge Chromium 内核),
- 双击启动 → 弹出应用主窗口, 不再依赖外部浏览器;
- 点 feed 卡片/消息/账号等任何视频·帖子链接 → 应用弹出的"内置浏览器"子窗口,
  顶部注入迷你工具条(← 后退 / ⟳ 刷新 / ⧉ 用系统浏览器打开 / ✕ 关闭);
- 未装 WebView2 运行时或缺 pywebview 库 → 自动回退外部浏览器模式(旧行为, 弹提示);
- `python app_window.py --server` → 纯服务模式(等同 python app.py --auto-open)。

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
PLAY_W, PLAY_H = 1180, 780
BG_DARK = "#0f1115"   # 与 web/style.css 暗色 --bg 一致, 避免窗口加载瞬间白闪

# 内置浏览器子窗口注入的迷你工具条。要点:
# - 幂等(已存在直接返回), 整页导航后 loaded 事件会重新注入;
# - 平时 28% 透明度, 悬停变清晰, 不挡正文;
# - 关闭必须走桥(app.close_window): Chromium 内核禁止页面脚本 window.close();
# - z-index 拉满 + 捕获阶段点击, 防站点脚本吞掉按钮事件。
BAR_JS = r"""
(function(){
  var old = document.getElementById('__msx_bar');
  if (old) { old.remove(); }
  var b = document.createElement('div');
  b.id = '__msx_bar';
  b.style.cssText = 'position:fixed;top:10px;right:10px;z-index:2147483647;display:flex;'
    + 'gap:6px;font:13px/1 system-ui,"Microsoft YaHei",sans-serif;opacity:.28;'
    + 'transition:opacity .25s;user-select:none;-webkit-user-select:none';
  b.addEventListener('mouseenter', function(){ b.style.opacity = '1'; });
  b.addEventListener('mouseleave', function(){ b.style.opacity = '.28'; });
  function btn(t, tip, fn){
    var x = document.createElement('button');
    x.textContent = t; x.title = tip;
    x.style.cssText = 'padding:6px 11px;border:0;border-radius:8px;'
      + 'background:rgba(22,26,36,.9);color:#e8ebf2;cursor:pointer;font-size:13px;'
      + 'box-shadow:0 2px 10px rgba(0,0,0,.3)';
    x.addEventListener('click', function(ev){ ev.preventDefault(); ev.stopPropagation(); fn(); }, true);
    b.appendChild(x);
  }
  function api(){ return (window.pywebview && window.pywebview.api) || null; }
  btn('\u2190', '\u540e\u9000', function(){ history.back(); });
  btn('\u27f3', '\u5237\u65b0', function(){ location.reload(); });
  btn('\u29c9', '\u7528\u7cfb\u7edf\u6d4f\u89c8\u5668\u6253\u5f00',
      function(){ var a = api(); if (a) a.open_external(location.href); });
  btn('\u2715', '\u5173\u95ed\u7a97\u53e3',
      function(){ var a = api(); if (a) a.close_window(window.__msx_wid); });
  (document.body || document.documentElement).appendChild(b);
  return 'injected';
})()
"""

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
    """暴露给页面的 JS 桥: window.pywebview.api.* (主窗口与内置子窗口共用)。

    安全: 只接受 http(s):// URL —— 页面侧无法借此开本地文件或执行命令。
    """

    def __init__(self):
        self._wins = {}          # wid -> pywebview Window(内置浏览器子窗口)
        self._n = 0
        self._lock = threading.Lock()

    # ---- 前端探测: 存在即说明跑在桌面壳里, app.js 据此切换跳转通道 ----
    def shell_info(self):
        return {"name": "desktop-shell", "version": APP_VER.lstrip("v"),
                "embedded_browser": True}

    # ---- 核心入口: 在应用内弹出内置浏览器窗口 ----
    def open_in_app(self, url, title=""):
        url = (url or "").strip()
        if not _URL_RE.match(url):
            return "bad-url"
        import webview
        with self._lock:
            self._n += 1
            wid = self._n
        try:
            w = webview.create_window(title or "内置浏览器", url,
                                      width=PLAY_W, height=PLAY_H,
                                      background_color=BG_DARK)
        except Exception:
            return "fail"
        self._wins[wid] = w
        # loaded 在每次整页导航后触发 → 工具条自动跟随; 放独立线程注入,
        # 避免在事件回调线程里同步调 evaluate_js 与 UI 线程互等
        w.events.loaded += lambda *_a: self._inject(w, wid)
        return "ok"

    def _inject(self, w, wid):
        def go():
            try:
                w.evaluate_js("window.__msx_wid=%d;" % wid + BAR_JS)
            except Exception:
                pass
        threading.Thread(target=go, daemon=True, name="msx-bar-inject").start()

    def close_window(self, wid=None):
        try:
            wid = int(wid)
        except (TypeError, ValueError):
            return "bad-wid"
        w = self._wins.pop(wid, None)
        if w is None:
            return "gone"
        try:
            w.destroy()
        except Exception:
            pass
        return "closed"

    def open_external(self, url):
        """用户显式要求用系统浏览器时(工具条 ⧉ 按钮)。"""
        url = (url or "").strip()
        if not _URL_RE.match(url):
            return "bad-url"
        webbrowser.open(url)
        return "ok"


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
    if "--e2e-open" in argv:   # 测试钩子: 启动后自动经桥开一个内置浏览器窗口
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
    """窗口壳主流程: 起/复用服务 → WebView2 检测 → 主窗口 → 全关后收尾。

    e2e_url 仅测试用(_e2e_shell.py): 主窗口就绪后自动走一次 open_in_app 桥调用,
    与用户点击卡片进入内置浏览器的代码路径完全一致, 不碰真实鼠标。
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
    # 站点内 target=_blank/弹新页 → 当前窗口导航, 绝不放去系统浏览器
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    api = ShellApi()
    webview.create_window("%s %s" % (APP_NAME, APP_VER), url,
                          width=MAIN_W, height=MAIN_H, min_size=MAIN_MIN,
                          background_color=BG_DARK, js_api=api)

    def e2e_hook():
        if not e2e_url:
            return
        time.sleep(8)   # 等主窗口与服务就绪
        api.open_in_app(e2e_url, "E2E 视频窗口")

    # 非无痕: 内置浏览器窗口里的登录态(B站等)跨重启保留, 数据落用户目录
    webview.start(func=e2e_hook, private_mode=False,
                  debug=os.environ.get("MSX_DEBUG") == "1")
    # start() 返回 = 所有窗口已关闭 → 收尾退出(收球/停线程池/关服务)
    app.shutdown(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
