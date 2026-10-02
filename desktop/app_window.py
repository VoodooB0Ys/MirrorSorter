# -*- coding: utf-8 -*-
"""洞鉴虚明 · 桌面窗口壳(内置浏览器·标签页版, v0.2.9 起)。

把服务装进自带的应用窗口: pywebview 驱动系统自带的 WebView2(Edge Chromium 内核),
- 双击启动 → 弹出应用主窗口, 不再依赖外部浏览器;
- 点 feed 卡片/消息/账号等任何视频·帖子链接 → 主窗口内**新增一个内置浏览器标签页**
  (右上角悬浮标签条可切换/关闭, 不产生任何新窗口);
- 未装 WebView2 运行时或缺 pywebview 库 → 自动回退外部浏览器模式(旧行为, 弹提示);
- `python app_window.py --server` → 纯服务模式(等同 python app.py --auto-open)。

实现说明: pywebview 一个窗口只有一个 WebView, 标签页 = Python 端维护的标签表 +
切换时导航同一 WebView, 注入标签条随每次页面加载重新渲染。已知取舍: 切换标签
会重新加载页面(视频播放不跨标签续播); 站内链接在当前标签内正常跳转。

开源依赖: pywebview (MIT, github.com/r0x0r/pywebview), Windows 走 WebView2 运行时。
"""
import json
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
ACCENT = "251,114,153"   # 应用主题强调色(B站粉), 标签条选中态用

# 内置浏览器标签条: 由 Python 在每次页面加载后注入并传入标签状态渲染。
# - 底部居中悬浮胶囊(站点顶栏/底栏大多有内容, 底部中央冲突最小), 常态 85% 不透明;
# - 进入全屏(看视频)自动隐藏, 退出恢复;
# - 切换/关闭走桥(tab_switch/tab_close), 捕获阶段点击防站点脚本吞事件;
# - ⟳ 刷新当前页, ⧉ 用系统浏览器打开当前页。
TABBAR_JS = r"""
window.__MSX_render = function(state){
  var old = document.getElementById('__msx_tabbar');
  if (old) { old.remove(); }
  function api(){ return (window.pywebview && window.pywebview.api) || null; }
  var bar = document.createElement('div');
  bar.id = '__msx_tabbar';
  bar.style.cssText = 'position:fixed;bottom:14px;left:50%;transform:translateX(-50%);'
    + 'z-index:2147483647;display:flex;align-items:center;gap:5px;max-width:86vw;'
    + 'font:12px/1 system-ui,"Microsoft YaHei",sans-serif;opacity:.85;'
    + 'transition:opacity .25s;user-select:none;-webkit-user-select:none;'
    + 'padding:4px;border-radius:12px;background:rgba(10,12,18,.55);'
    + 'box-shadow:0 6px 24px rgba(0,0,0,.35)';
  bar.addEventListener('mouseenter', function(){ bar.style.opacity = '1'; });
  bar.addEventListener('mouseleave', function(){ bar.style.opacity = '.85'; });
  function fs(){ return !!document.fullscreenElement; }
  document.addEventListener('fullscreenchange', function(){
    bar.style.display = fs() ? 'none' : 'flex';
  });
  if (fs()) { bar.style.display = 'none'; }
  function pill(label, tip, fn, active, closable, tid){
    var p = document.createElement('div');
    p.title = tip;
    p.style.cssText = 'display:flex;align-items:center;gap:5px;padding:7px 10px;'
      + 'border-radius:9px;cursor:pointer;white-space:nowrap;max-width:200px;'
      + 'overflow:hidden;background:' + (active ? 'rgba(%ACCENT%,.95)' : 'rgba(40,46,60,.92)')
      + ';color:#fff';
    var t = document.createElement('span');
    t.textContent = label;
    t.style.cssText = 'overflow:hidden;text-overflow:ellipsis';
    p.appendChild(t);
    if (closable) {
      var x = document.createElement('span');
      x.textContent = '\u2715';
      x.title = '\u5173\u95ed\u6807\u7b7e\u9875';
      x.style.cssText = 'opacity:.55;padding:0 1px';
      x.addEventListener('click', function(ev){
        ev.stopPropagation(); var a = api(); if (a) a.tab_close(tid);
      }, true);
      p.appendChild(x);
    }
    p.addEventListener('click', function(ev){
      ev.preventDefault(); ev.stopPropagation(); fn();
    }, true);
    bar.appendChild(p);
  }
  (state.tabs || []).forEach(function(tb){
    pill(tb.title || '\u9875\u9762', tb.title || '', function(){
      var a = api(); if (a) a.tab_switch(tb.id);
    }, tb.id === state.active, !tb.pinned, tb.id);
  });
  pill('\u27f3', '\u5237\u65b0\u5f53\u524d\u9875', function(){ location.reload(); });
  pill('\u29c9', '\u7528\u7cfb\u7edf\u6d4f\u89c8\u5668\u6253\u5f00\u5f53\u524d\u9875', function(){
    var a = api(); if (a) a.open_external(location.href);
  });
  (document.body || document.documentElement).appendChild(bar);
};
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
        self._lock = threading.Lock()
        self._tabs = [{"id": self.HOME_ID, "url": None, "title": "信息流", "pinned": True}]
        self._active = self.HOME_ID
        self._next_id = 1

    def attach(self, win, app_url):
        """主窗口创建后绑定; loaded 后同步标签状态并渲染标签条。"""
        self._win = win
        self._app_url = app_url
        self._tabs[0]["url"] = app_url
        win.events.loaded += lambda *_a: self._on_loaded()
        self._render()

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
                if t["url"] == url:   # 同一链接只占一个标签, 重复点=切过去
                    self._active = t["id"]
                    break
            else:
                self._tabs.append({"id": self._next_id, "url": url,
                                   "title": (title or url)[:24], "pinned": False})
                self._active = self._next_id
                self._next_id += 1
        return self._navigate(url)

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
            url = t["url"] or self._app_url
        return self._navigate(url)

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
            self._tabs.pop(idx)
            if self._active == tid:   # 关的是当前页 → 激活前一个, 没有则回信息流
                self._active = self._tabs[max(0, idx - 1)]["id"]
            nxt = next(x for x in self._tabs if x["id"] == self._active)
            url = nxt["url"] or self._app_url
        self._render()
        return self._navigate(url)

    def open_external(self, url):
        """用户显式要求用系统浏览器时(标签条 ⧉ 按钮)。"""
        url = (url or "").strip()
        if not _URL_RE.match(url):
            return "bad-url"
        webbrowser.open(url)
        return "ok"

    # ---- 内部 ----
    def _navigate(self, url):
        if self._win is None:
            return "no-window"
        try:
            self._win.load_url(url)
            return "ok"
        except Exception:
            return "fail"

    def _on_loaded(self):
        # 放独立线程: 避免在事件回调线程里同步调 evaluate_js 与 UI 线程互等
        threading.Thread(target=self._sync_after_load, daemon=True,
                         name="msx-tabs-sync").start()

    def _sync_after_load(self):
        w = self._win
        if w is None:
            return
        try:
            time.sleep(0.2)   # 等文档可用, 取页面标题
            url = w.get_current_url() or self._app_url
            with self._lock:
                tab = next((x for x in self._tabs if x["id"] == self._active), None)
                keep_home = tab is None or tab["id"] == self.HOME_ID
            title = w.evaluate_js("document.title")
            if not keep_home and tab is not None:
                if url:
                    tab["url"] = url   # 站内跳转后记住最新地址, 切回标签时恢复
                if title:
                    tab["title"] = str(title).strip()[:24]
            self._render()
        except Exception:
            pass

    def _render(self):
        w = self._win
        if w is None:
            return
        try:
            with self._lock:
                payload = json.dumps(
                    {"active": self._active,
                     "tabs": [{"id": t["id"], "title": t["title"] or "页面", "pinned": t["pinned"]}
                              for t in self._tabs]},
                    ensure_ascii=False)
            w.evaluate_js(TABBAR_JS.replace("%ACCENT%", ACCENT)
                          + "\n__MSX_render(%s);" % payload)
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
                                background_color=BG_DARK, js_api=api)
    api.attach(win, url)

    def e2e_hook():
        if not e2e_url:
            return
        time.sleep(8)   # 等主窗口与服务就绪
        api.open_in_app(e2e_url, "E2E 视频标签页")

    # 非无痕: 内置浏览器标签页里的登录态(B站等)跨重启保留, 数据落用户目录
    webview.start(func=e2e_hook, private_mode=False,
                  debug=os.environ.get("MSX_DEBUG") == "1")
    # start() 返回 = 窗口已关闭 → 收尾退出(收球/停线程池/关服务)
    app.shutdown(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
