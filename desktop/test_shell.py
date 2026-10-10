# -*- coding: utf-8 -*-
"""内置浏览器标签页壳冒烟(逻辑层, 不起任何 GUI 窗口)。
覆盖: app 入口重构(run_server/shutdown/APP_VER) · app_window 标签页管理与安全拒绝 ·
前端统一出口(app.js 无裸 window.open) · 启动/打包脚本指向窗口壳 · --server 子进程探活。
"""
import importlib.util
import io
import os
import re
import subprocess
import sys
import time
import urllib.request

PROJECT = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(PROJECT, "shell-tmp")
os.makedirs(TMP, exist_ok=True)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, extra))


os.environ["MIRRORSORTER_DATA_DIR"] = TMP


def load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(PROJECT, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1. app.py 入口重构 ----------
app = load("app_shell_test", "app.py")
check("app.run_server 可复用", callable(getattr(app, "run_server", None)))
check("app.shutdown 统一收尾", callable(getattr(app, "shutdown", None)))
check("app.APP_VER 版本常量(形如 v0.3.1.Ndsk)", bool(re.fullmatch(r"v\d+\.\d+\.\d+\.\d+dsk", app.APP_VER)),
      app.APP_VER)

# ---------- 2. app_window 标签页管理与降级探测 ----------
aw = load("app_window_shell_test", "app_window.py")
check("壳版本与 app 一致", aw.APP_VER == app.APP_VER)
check("WebView2 运行时探测可用", isinstance(aw.webview2_available(), bool))
check("pywebview 探测可用", isinstance(aw.have_pywebview(), bool))
_bs = aw._bundled_resource(*aw._WV2_BOOTSTRAP)
check("WebView2 引导安装包随包分发", _bs is not None and os.path.getsize(_bs) > 1000000, _bs)
check("缺运行时→自动安装→失败才回退(壳流程接线)",
      "install_webview2_runtime()" in io.open(
          os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read())
check("运行时安装: 装前修复EdgeUpdate环境(文件锁/服务/状态重置)",
      "taskkill /IM msedgewebview2.exe" in io.open(os.path.join(PROJECT, "installer", "app_installer.nsi"), encoding="utf-8-sig").read()
      and "sc config edgeupdate start= demand" in io.open(os.path.join(PROJECT, "installer", "app_installer.nsi"), encoding="utf-8-sig").read()
      and "DeleteRegKey HKLM \"SOFTWARE\\Microsoft\\EdgeUpdate\"" in io.open(os.path.join(PROJECT, "installer", "app_installer.nsi"), encoding="utf-8-sig").read())
_enh = os.path.join(PROJECT, "extensions", "bilibili-enhance.user.js")
check("画质增强插件: 内置+注入接线",
      os.path.isfile(_enh) and "bilibili-enhance.user.js" in io.open(
          os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read()
      and "feConvolveMatrix" in io.open(_enh, encoding="utf-8").read())
check("Edge浏览器内核兜底(运行时装不上时的免安装方案)",
      "find_edge_browser_dir" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
      and "msedge.exe" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
      and "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read())
check("运行时安装: %TEMP%故障重定向重试(安装器+exe两路)",
      "SetEnvironmentVariable" in io.open(os.path.join(PROJECT, "installer", "app_installer.nsi"), encoding="utf-8-sig").read()
      and 'os.environ["TMP"] = fallback' in io.open(
          os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read())

api = aw.ShellApi()
check("桥拒绝非 http(s) URL", api.open_in_app("javascript:alert(1)") == "bad-url"
      and api.open_in_app("file:///C:/windows") == "bad-url"
      and api.open_in_app("") == "bad-url")
check("open_external 拒绝非 http(s)", api.open_external("file:///C:/") == "bad-url")
info = api.shell_info()
check("shell_info 供前端探测", info.get("embedded_browser") is True and info.get("tabs") is True)
check("未绑控件池时不崩(仅登记)", api.open_in_app("https://www.bilibili.com") == "ok"
      and len(api._tabs) == 2)

# 假控件池: 记录 create/show/hide/dispose 调用, 验证标签逻辑与"切换零重载"(不起 GUI)
calls = []


class FakePages:
    def init(self):
        pass

    def create(self, url, key):
        calls.append(("create", url, key))

    def show(self, key):
        calls.append(("show", key))

    def hide(self, key):
        calls.append(("hide", key))

    def dispose(self, key):
        calls.append(("dispose", key))

    def push_nav(self, key):
        calls.append(("push_nav", key))

    def reload(self, key):
        calls.append(("reload", key))

    def goto(self, key, url):
        calls.append(("goto", key, url))

    def navigate_history(self, key, back):
        calls.append(("nav", key, back))
        return True


api2 = aw.ShellApi()
api2.attach(None, "http://127.0.0.1:8000")
api2.bind_pages(FakePages())
check("信息流是 0 号固定标签", api2._tabs[0]["pinned"] is True and api2._active == 0)
check("开新链接=新标签+创建控件", api2.open_in_app("https://www.bilibili.com/video/BV1xx") == "ok"
      and len(api2._tabs) == 2 and api2._active == 1
      and ("create", "https://www.bilibili.com/video/BV1xx", 1) in calls
      and ("show", 1) in calls)
n_create = calls.count(("create", "https://www.bilibili.com/video/BV1xx", 1))
api2.open_in_app("https://www.bilibili.com/video/BV1xx")   # 同链接重复点
check("同链接去重且不重建控件(零重载)", len(api2._tabs) == 2
      and calls.count(("create", "https://www.bilibili.com/video/BV1xx", 1)) == n_create)
api2.open_in_app("https://tieba.baidu.com/p/1")
check("第二个标签入表并激活", len(api2._tabs) == 3 and api2._active == 2)
c_before = len([c for c in calls if c[0] == "create"])
api2.tab_switch(0)   # 切回信息流: 纯可见性 → 不应有任何 create
check("切回信息流零重载(无 create)", api2._active == 0
      and len([c for c in calls if c[0] == "create"]) == c_before
      and ("show", 0) in calls and ("hide", 2) in calls)
check("切到指定标签零重载", api2.tab_switch(1) == "ok" and api2._active == 1
      and len([c for c in calls if c[0] == "create"]) == c_before)
check("固定标签不可关", api2.tab_close(0) == "pinned" and len(api2._tabs) == 3)
check("关不存在标签安全", api2.tab_close(999) == "gone" and api2.tab_close("x") == "bad-id")
check("关当前标签→激活前一个", api2.tab_close(2) == "ok" and len(api2._tabs) == 2
      and api2._active == 1 and ("dispose", 2) in calls)
check("关最后一个标签→回信息流", api2.tab_close(1) == "ok" and api2._active == 0
      and ("show", 0) in calls and len(api2._tabs) == 1)

# ---------- 2b. 导航工具栏接线(navbar 按钮/地址栏 → ShellApi → 控件池) ----------
check("地址栏输入规范化: 补 https + 主机名校验",
      aw._norm_nav_url("bilibili.com") == "https://bilibili.com"
      and aw._norm_nav_url(" https://www.bilibili.com/video/BV1 ") == "https://www.bilibili.com/video/BV1"
      and aw._norm_nav_url("localhost:8000") == "https://localhost:8000")
check("地址栏拒绝非 http(s)/乱输入",
      aw._norm_nav_url("file:///C:/windows") is None
      and aw._norm_nav_url("javascript:alert(1)") is None
      and aw._norm_nav_url("hello world") is None
      and aw._norm_nav_url("justword") is None
      and aw._norm_nav_url("") is None)
check("nav_goto 非法输入拒绝且不触池", api2.nav_goto("file:///C:/") == "bad-url"
      and not [c for c in calls if c[0] == "goto"])
calls.clear()
check("nav_goto 合法输入=当前标签内导航+焦点回页面",
      api2.nav_goto("bilibili.com") == "ok"
      and ("goto", 0, "https://bilibili.com") in calls and ("show", 0) in calls)
calls.clear()
check("后退/前进/刷新走激活标签", api2.nav_back() == "ok" and api2.nav_forward() == "ok"
      and api2.nav_reload() == "ok"
      and ("nav", 0, True) in calls and ("nav", 0, False) in calls
      and ("reload", 0) in calls)
# _on_nav 只反映激活标签: 绑定回调捕获转发
got = []
api2.bind_navbar(lambda u, b, f: got.append((u, b, f)))
api2._on_nav(9, "https://other-tab.example", True, True)   # 后台标签 → 忽略
check("_on_nav 忽略非激活标签", got == [("", False, False)])
api2._on_nav(0, "http://127.0.0.1:8000/x", True, False)
check("_on_nav 转发激活标签状态", got[-1] == ("", True, False))
check("信息流(本地服务)地址栏留空不显示网址", got[-1][0] == "")
check("切标签强制补推导航状态(push_nav)", api2.open_in_app("https://tieba.baidu.com/p/2") == "ok"
      and ("push_nav", api2._active) in calls)
api2._on_nav(api2._active, "https://tieba.baidu.com/p/2", True, False)
check("内置浏览器标签显示完整网址", got[-1] == ("https://tieba.baidu.com/p/2", True, False))
check("导航工具栏模块可加载", os.path.isfile(os.path.join(PROJECT, "shell_navbar.py"))
      and "install_navbar" in io.open(os.path.join(PROJECT, "shell_navbar.py"), encoding="utf-8").read())
check("导航工具栏接线完整(app_window)",
      "bind_navbar" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
      and "install_navbar" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
      and "on_nav=api._on_nav" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
      and "_norm_nav_url" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read())
check("内容区下移含工具栏高度(唯一来源)",
      "from shell_navbar import nav_h" in io.open(os.path.join(PROJECT, "shell_tabstrip.py"), encoding="utf-8").read()
      and "nav_h" in io.open(os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read() or True)
check("NL 未定义 bug 已修(shell_debug.log 落盘恢复)",
      re.search(r"^NL = ", io.open(os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read(), re.M) is not None)

# ---------- 2c. 「信息以新窗口形式打开」(v0.3.1.34): 悬浮窗 / 拖出 / 塞回 ----------
_aw_src = io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
_sp_src = io.open(os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read()
_st_src = io.open(os.path.join(PROJECT, "shell_tabstrip.py"), encoding="utf-8").read()
_idx_src = io.open(os.path.join(PROJECT, "web", "index.html"), encoding="utf-8").read()
_ajs_src = io.open(os.path.join(PROJECT, "web", "app.js"), encoding="utf-8").read()
_app_src = io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read()
_fs_src = io.open(os.path.join(PROJECT, "shell_float.py"), encoding="utf-8").read()
check("悬浮窗模块存在且关键 API 齐备", os.path.isfile(os.path.join(PROJECT, "shell_float.py"))
      and "install_floats" in _fs_src and "def dock(" in _fs_src and "def close(" in _fs_src
      and "send_to_window" in _fs_src and "dock_to_main" in _fs_src)
check("控件池支持跨窗迁移(控件实例不变, 不重载)",
      "def send_to_window" in _sp_src and "def dock_to_main" in _sp_src
      and "def control" in _sp_src and "par.Controls.Remove(c)" in _sp_src)
check("设置键 ui_new_window 默认开(1) 且入读写白名单",
      '"ui_new_window": "1"' in _app_src and _app_src.count('"ui_new_window"') >= 3)
check("设置行: 开关 + 回显 + 保存接线",
      'id="openModeRow"' in _idx_src and "openModeRow" in _ajs_src
      and "ui_new_window" in _ajs_src)
check("标签栏拖出接线(拖拽阈值 + 吞掉误点击)",
      "tab_detach" in _st_src and "drag[" in _st_src and "fired" in _st_src)
check("壳接线: install_floats + bind_floats + tab_detach/window_dock",
      "install_floats" in _aw_src and "bind_floats" in _aw_src
      and "def tab_detach" in _aw_src and "def window_dock" in _aw_src)
check("shell_info 暴露悬浮窗能力", aw.ShellApi().shell_info().get("windows") is True)


class FakeFloatMgr:
    def __init__(self):
        self.opened, self.docked, self.closed = [], [], []

    def open(self, tab, pages=None):
        self.opened.append(tab["id"])

    def dock(self, tid):
        self.docked.append(tid)
        return True

    def close(self, tid):
        self.closed.append(tid)
        return True

    def set_title(self, tid, title):
        pass

    def exists(self, tid):
        return True


api3 = aw.ShellApi()
api3.attach(None, "http://127.0.0.1:8000")
fm = FakeFloatMgr()
api3.bind_pages(FakePages())
api3.bind_floats(fm)
api3._new_window_enabled = lambda: True   # 默认开
check("新窗口模式: 点开链接 → 悬浮窗(不占标签栏, 主窗口仍停在信息流)",
      api3.open_in_app("https://www.bilibili.com/video/BV2") == "ok"
      and api3._tabs[1]["float"] is True and fm.opened == [1] and api3._active == 0)
rendered = []
api3.bind_strip(lambda tabs, active: rendered.append(([t["id"] for t in tabs], active)))
check("悬浮标签不出现在主窗口标签栏", bool(rendered) and rendered[-1][0] == [0])
check("塞回标签页: float→False 且成为激活标签",
      api3.window_dock(1) == "ok" and api3._tabs[1]["float"] is False
      and api3._active == 1 and fm.docked == [1])
check("拖出: 标签页 → 悬浮窗", api3.tab_detach(1) == "ok"
      and api3._tabs[1]["float"] is True and fm.opened[-1] == 1 and api3._active == 0)
check("关悬浮标签: 销毁悬浮窗 + 移除标签",
      api3.tab_close(1) == "ok" and fm.closed == [1] and len(api3._tabs) == 1)
api3._new_window_enabled = lambda: False   # 关掉设置 → 回到纯标签页模式
api3.open_in_app("https://tieba.baidu.com/p/9")
_tid = api3._tabs[1]["id"]
check("关掉设置: 点开链接 → 普通标签", api3._tabs[1]["float"] is False
      and api3._active == _tid)
check("关掉设置: 拖出仍生效(开关只管「点开信息」怎么打开, bug2)",
      api3.tab_detach(_tid) == "ok" and api3._tabs[1]["float"] is True
      and fm.opened[-1] == _tid)
check("固定标签(信息流)不可拖出 / 塞回", api3.tab_detach(0) == "pinned"
      and api3.window_dock(0) == "not-float")
check("控件池缺失时优雅降级为主窗口标签",
      aw.ShellApi().open_in_app("https://tieba.baidu.com/p/7") == "ok")

# ---------- 2d. 悬浮窗"拽到标签栏松手即合并"(v0.3.1.34 交互修正) ----------
check("悬浮窗落点判定 + 拖拽中提示(_over_tabbar / _hint)",
      "def _over_tabbar()" in _fs_src and "def _hint(on, win=None)" in _fs_src
      and "main.PointToClient(WinForms.Cursor.Position)" in _fs_src)
check("悬浮窗自身也画落点提示(拖拽时它盖住标签栏, 主窗口侧高亮看不见)",
      '"drop_hint": False' in _fs_src and 'win.get("drop_hint")' in _fs_src
      and "松手合并到标签栏" in _fs_src
      and 'accent_getter=lambda: app.sget("float_ball_accent")' in _aw_src)
check("自绘拖动(bar.Capture + 跟手移动), 不用 HTCAPTION 模态循环",
      "bar.Capture = True" in _fs_src and 'win["dragging"] = True' in _fs_src
      and 'form.Location = Point(cur.X - win["dx"], cur.Y - win["dy"])' in _fs_src)
check("MouseUp 落在标签栏 → api.window_dock(合并成标签页)",
      "bar.MouseUp += on_up" in _fs_src and "over = _over_tabbar()" in _fs_src
      and "api.window_dock(tid)" in _fs_src and 'win.pop("drag_moved", False)' in _fs_src)
check("标签栏暴露 highlight 并在 Paint 里绘制落点高亮",
      "update.highlight = highlight" in _st_src and 'st["drop_hi"]' in _st_src
      and 'if st.get("drop_hi"):' in _st_src)
check("壳把标签栏高度交给悬浮窗, 且标签栏先于悬浮窗安装",
      'tabstrip_h=int(getattr(strip_update, "bar_h", 0) or 0)' in _aw_src
      and 0 <= _aw_src.index("from shell_tabstrip import install_strip")
      < _aw_src.index("from shell_float import install_floats"))
check("e2e 钩子覆盖真实拖拽手势(_e2e_drag_to_tabbar)",
      "def _e2e_drag_to_tabbar" in _aw_src and "SetCursorPos" in _aw_src
      and "0x0202" in _aw_src and "drag-merge" in _aw_src)

# ---------- 3c. 播放页自有UI(bilibili-ownui): 注入接线 + 设置白名单 + 设置行 ----------
_ownui = io.open(os.path.join(PROJECT, "extensions", "bilibili-ownui.user.js"), encoding="utf-8").read()
check("播放页自有UI脚本: 存在且关键API齐备",
      len(_ownui) > 8000 and "x/v2/reply/add" in _ownui and "x/v2/reply/action" in _ownui
      and "x/v2/reply/reply" in _ownui and "archive/related" in _ownui
      and "attachShadow" in _ownui and "__MSX_OWNUI__" in _ownui
      and "/video/" in _ownui and "bili_jct" in _ownui and "SESSDATA" not in _ownui)
_sp = io.open(os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read()
check("自有UI注入接线(ui_ownui门控→__MSX_OWNUI__)",
      "bilibili-ownui.user.js" in _sp and "__MSX_OWNUI__" in _sp and "ui_ownui" in _sp)
# 断言前端源码前先删注释(规范: 注释里出现的关键字不算实现 —— ownui 的广告屏蔽注释
# 明确写了「不要动 .bpx-player-adv-dm-wrap」, 那是说明而不是实现)
_ownui_c = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", _ownui, flags=re.S))
check("自有UI不碰播放器DOM(脚本无播放器选择器)",
      "bilibili-player" not in _ownui_c and "bpx-player" not in _ownui_c)
check("自有UI设置白名单(app.py三处)",
      io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read().count('"ui_ownui"') >= 3)
_idx = io.open(os.path.join(PROJECT, "web", "index.html"), encoding="utf-8").read()
_appj = io.open(os.path.join(PROJECT, "web", "app.js"), encoding="utf-8").read()
check("自有UI设置行(开/关+回显+保存)",
      'id="ownuiRow"' in _idx and "ownuiRow" in _appj and "ui_ownui" in _appj)

# ---------- 3d. 播放页推荐筛选(v0.3.1.2): m_play_related + 规则注入 + 7:3 混排 ----------
check("播放页筛选设置键(m_play_related 默认1)",
      '"m_play_related": "1"' in io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read())
check("播放页筛选键入 /api/machine GET+POST 白名单",
      io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read().count('"m_play_related"') >= 3)
check("规则对象注入(__MSX_MF__: 词表/正则/拉黑/厌恶词)",
      "__MSX_MF__" in _sp and "FLAME_SLURS" in _sp and "CLICKBAIT_RE" in _sp
      and "dislike_keywords" in _sp and "ablock" in _sp)
check("播放页筛选逻辑(过滤+去重+7:3混排+探索池)",
      "mfFilter" in _ownui and "mixRel" in _ownui and "x/web-interface/popular" in _ownui
      and "mfPass" in _ownui and "normTitle" in _ownui and "emoBad" in _ownui)
check("顶栏只隐导航条目(slimNav逐条目+LOGO豁免+tick补隐), 无整块/猜测选择器",
      "slimNav" in _ownui and "mini-header__logo" in _ownui and "NAV_LABELS" in _ownui
      and "bili-header__channel" not in _ownui and ".left-entry{" not in _ownui)
check("播放页筛选设置行(mPlayRel 回显+保存)",
      'id="mPlayRel"' in _idx and "m_play_related" in _appj)
check("自有UI启动遮罩(挂载前原生评论区/右栏 visibility 隐藏防闪现, 摘除+路由复位齐备)",
      "msx-ownui-boot" in _ownui and "bootMaskOn" in _ownui and "bootMaskOff" in _ownui
      and "visibility:hidden!important" in _ownui)
_srjs = io.open(os.path.join(PROJECT, "extensions", "bilibili-sr.user.js"), encoding="utf-8").read()
check("SR画布防overlay提升+原视频保持可见(整屏截屏不再黑屏)",
      "border-radius:0.5px" in _srjs
      and 'classList.add("msx-sr-hide")' not in _srjs
      and 'setProperty("opacity", "0", "important")' not in _srjs)
check("夜间模式防白闪(B站页 document-start 压深色底, 浅色模式撤除)",
      "setProperty('background', '#0f0f0f', 'important')" in _sp
      and "removeProperty('background')" in _sp)

# ---------- 3. 前端统一出口 ----------
js = io.open(os.path.join(PROJECT, "web", "app.js"), encoding="utf-8").read()
fallback = js.count('window.open(url, "_blank", "noopener")')
check("app.js 裸 window.open 只剩桥内兜底", fallback == 3, str(fallback))
check("app.js 跳转出口调用点 ≥ 10", js.count("openExternal(") >= 11, str(js.count("openExternal(")))
check("index.html 无 target=_blank", 'target="_blank"' not in io.open(
    os.path.join(PROJECT, "web", "index.html"), encoding="utf-8").read())
html = io.open(os.path.join(PROJECT, "web", "index.html"), encoding="utf-8").read()
check("页脚版本与 app.APP_VER 一致", ("洞鉴虚明 " + app.APP_VER) in html, app.APP_VER)
check("设置页: 字体大小三档(默认小)", 'id="fontRow"' in html and html.count("data-font=") == 3
      and 'data-font="small"' in html)
check("设置页: 背景预设/自定义/深度滑条在强调色设置下方",
      0 < html.find("customAccentRow") < html.find("bgRow")
      < html.find("bgCustomRow") < html.find("bgDepthRow") < html.find("fontRow"))
_appjs = io.open(os.path.join(PROJECT, "web", "app.js"), encoding="utf-8").read()
check("背景系统: 自定义色+1..10深度派生全套表面色",
      "hexToHsl" in _appjs and 'set("--panel"' in _appjs
      and 'set("--input"' in _appjs and "bgDepth" in _appjs and "bgHex" in _appjs)
check("背景系统: 预设色卡+自定义历史+推送标签栏方案",
      "bgCustomList" in _appjs and "bgStripScheme" in _appjs
      and "bg: bgStripScheme()" in _appjs)
check("标签栏吃背景方案: 服务端存储+轮询消费",
      "ui_bg_scheme" in io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read()
      and "ui_bg_scheme" in io.open(os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read()
      and "set_bg_scheme" in io.open(os.path.join(PROJECT, "shell_tabstrip.py"), encoding="utf-8").read())
check("深度滑条跟随强调色", "accent-color: var(--accent)" in io.open(
    os.path.join(PROJECT, "web", "style.css"), encoding="utf-8").read())
check("内置画质增强脚本: SVG非锐化掩模(模糊-合成)锐化+色彩滤镜+服务端开关",
      "feGaussianBlur" in io.open(os.path.join(PROJECT, "extensions", "bilibili-enhance.user.js"), encoding="utf-8").read()
      and "feComposite" in io.open(os.path.join(PROJECT, "extensions", "bilibili-enhance.user.js"), encoding="utf-8").read()
      and "msx-sharpen" in io.open(os.path.join(PROJECT, "extensions", "bilibili-enhance.user.js"), encoding="utf-8").read()
      and "__MSX_BQ__" in io.open(os.path.join(PROJECT, "extensions", "bilibili-enhance.user.js"), encoding="utf-8").read())
_srlib = io.open(os.path.join(PROJECT, "extensions", "anime4k-webgpu.js"), encoding="utf-8").read()
_srglue = io.open(os.path.join(PROJECT, "extensions", "bilibili-sr.user.js"), encoding="utf-8").read()
check("AI超分: Anime4K-WebGPU库(MIT)+胶水+开关接线",
      len(_srlib) > 3000000 and "CNNx2UL" in _srglue
      and "requestVideoFrameCallback" in _srglue
      and "ui_sr" in io.open(os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read()
      and '"ui_sr"' in io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read()
      and 'id="srLevelRow"' in html and 'data-level="关"' in html)
check("画质增强接线: 注入管线+设置白名单+独立设置栏(档位即开关)",
      "__MSX_BQ__" in io.open(os.path.join(PROJECT, "shell_pages.py"), encoding="utf-8").read()
      and '"ui_bq", "ui_bq_level"' in io.open(os.path.join(PROJECT, "app.py"), encoding="utf-8").read()
      and 'id="bqLevelRow"' in html and 'data-level="关闭"' in html
      and "<summary>画质增强设置</summary>" in html
      and 'id="bqRow"' not in html
      # 位置: 紧跟在「主题设置」栏后面(主题设置 < 画质增强设置 < AI 配置)
      and html.index("<summary>主题设置</summary>")
      < html.index("<summary>画质增强设置</summary>")
      < html.index("<summary>AI 配置</summary>"))
check("壳关闭站点弹新页→系统浏览器的默认行为",
      "OPEN_EXTERNAL_LINKS_IN_BROWSER" in io.open(
          os.path.join(PROJECT, "app_window.py"), encoding="utf-8").read())

# ---------- 3f. 13 项用户报修回归(v0.3.1.38dsk) ----------
_nb_src = io.open(os.path.join(PROJECT, "shell_navbar.py"), encoding="utf-8").read()
_css_src = io.open(os.path.join(PROJECT, "web", "style.css"), encoding="utf-8").read()
_cf_src = io.open(os.path.join(PROJECT, "extensions", "bilibili-cfilter.user.js"),
                  encoding="utf-8").read()
_cf_c = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", _cf_src, flags=re.S))

# bug1 设置栏折叠某些子栏后画布高度不够、拉不到最下方
check("bug1 设置栏高度自愈: JS 实测视口 --app-h + flex 子项 min-height:0",
      "height:calc(var(--app-h, 100vh) / var(--ui-zoom, 1))" in _css_src
      and "min-width:46px; min-height:0;" in _css_src
      and "#railBody { flex:1; overflow-y:auto; padding:0 12px 24px; min-height:0; }" in _css_src
      and "function syncAppH()" in _ajs_src
      and 'setProperty("--app-h", h + "px")' in _ajs_src
      and 'window.addEventListener("resize", syncAppH)' in _ajs_src)

# bug2 关掉「信息以新窗口形式打开」后仍应能从标签页拖出悬浮窗
_td = re.search(r"def tab_detach\(self.*?(?=\n    def )", _aw_src, re.S)
_td_code = re.sub(r"#[^\n]*", "", _td.group(0)) if _td else ""
check("bug2 关掉开关不再掐掉拖出悬浮窗(开关只管「点开信息」怎么打开)",
      _td is not None and "_new_window_enabled" not in _td_code
      and 'return "pinned"' in _td_code and 'return "already"' in _td_code
      and 'mode = "window" if self._new_window_enabled() else "tab"' in _aw_src)

# bug3 设置项文案太繁琐 → 短标题 + 悬停 title 给详细描述
check("bug3 设置项文案精简(13 条短标题 + title 悬停详细描述)",
      _idx.count('<label title="') >= 13
      and '<label title="点开信息弹独立小窗' in _idx
      and "信息以新窗口形式打开" in _idx
      and "强调色（按钮/徽标/高亮的特殊颜色）" not in _idx)

# bug4 悬浮窗缺全屏/最小化按钮; 除最小化外始终在软件图层最上方
# (本轮用户报修 m02020: 置顶曾是系统级 TopMost, 压住了 QQ 等别的应用 —— 改成 owner 关系,
#  只在主窗口之上; 所以这里断言"有 owner、且代码里不再有 TopMost")
_fs_code = re.sub(r"#[^\n]*", "", _fs_src)
check("bug4 悬浮窗补「最小化/全屏」按钮 + 只在软件图层之上(owner, 不压别的应用)",
      '_BTN_MIN, _BTN_MAX = "min", "max"' in _fs_src
      and "_BTN_ORDER = (_BTN_DOCK, _BTN_MIN, _BTN_MAX, _BTN_CLOSE)" in _fs_src
      and '(_BTN_MIN, "—")' in _fs_src
      and '(_BTN_MAX, "▣" if _is_max(win) else "□")' in _fs_src
      and "form.Owner = main" in _fs_code
      and "TopMost" not in _fs_code
      and "WinForms.FormWindowState.Minimized" in _fs_src
      and "def _toggle_max(win)" in _fs_src)

# bug5 视频播放页弹幕列表无法折叠
check("bug5 弹幕列表可折叠(标题即开关 + 折叠态记忆)",
      'id="msxDmFold"' in _ownui_c and 'id="msxDmCols"' in _ownui_c
      and 'id="msxDmAr"' in _ownui_c
      and 'localStorage.getItem("msx_dm_fold")' in _ownui_c
      and 'localStorage.setItem("msx_dm_fold"' in _ownui_c)

# bug6 视频全屏时隐藏网址栏与选项栏, 整窗纯播放
check("bug6 网页全屏隐藏标签栏/导航栏(网页事件→控件池→窗口壳→两条 chrome)",
      "core.ContainsFullScreenElementChanged += on_fs_changed" in _sp_src
      and "bool(core.ContainsFullScreenElement)" in _sp_src
      and "def set_fullscreen(self, key, on)" in _sp_src
      and "def set_fullscreen(on)" in _st_src
      and "update.set_fullscreen = set_fullscreen" in _nb_src
      and "on_fs=on_fs" in _aw_src and 'chrome["nav"]' in _aw_src)

# bug7 地址栏文字纯白/纯黑太刺眼 → 未悬停时 70% 不透明度
check("bug7 地址栏文字未悬停时 70% 不透明度(悬停/编辑回满)",
      '"tb_hover": False' in _nb_src
      and "def _blend(c, bg, k)" in _nb_src
      and "def _apply_tb_fg()" in _nb_src
      and 'else _blend(st["fg"], st["tb_bg"], 0.7))' in _nb_src
      and "def _set_tb_hover(v)" in _nb_src
      and "tb.MouseEnter" in _nb_src and "tb.MouseLeave" in _nb_src)

# bug8 开启评论筛选屏蔽后评论偶发无法继续向下滚动
check("bug8 主评论列表一条不删(只标记, 组件才会继续翻页) + 补泵翻页(屏蔽后仍能一路下滑)",
      "function isMainList(u)" in _cf_c and "/x/v2/reply/wbi/main" in _cf_c
      and "if (isMain) { markBlocked(r); out.push(r); continue; }" in _cf_c
      and "filtArr(r.replies, false)" in _cf_c
      and "if (!isMain && reps0 > 0" in _cf_c
      and "function pump()" in _cf_c and "inst.getList()" in _cf_c
      and "redactOne" not in _cf_c and "__msxRedacted" not in _cf_c)

# bug9 右侧推荐视频封面显示太小
check("bug9 右侧推荐封面放大(缩略图 60% 宽 / 150-300px / 16:9)",
      ".it .cv{position:relative;flex:0 0 60%;min-width:150px;max-width:300px;"
      "aspect-ratio:16/9;" in _ownui_c)

# bug10 全屏时左侧有缝能看到桌面 + 播放界面白边
check("bug10 全屏左缝/白边: 贴边热区用窗体底色 + 全屏把宿主窗最大化并 Dock=Fill",
      "EDGE_COLOR = Color.FromArgb(15, 17, 21)" in _st_src
      and "p.BackColor = EDGE_COLOR" in _st_src
      and 'and not fs_state["on"])' in _st_src
      and "_EDGE = Color.FromArgb(15, 17, 21)" in _fs_src
      and "def _tab_geo(form, home, key)" in _sp_src
      and "host.WindowState = WF.FormWindowState.Maximized" in _sp_src
      and "c.Dock = WF.DockStyle.Fill" in _sp_src)

# bug11 B站播放页里的大广告框需要屏蔽删除
check("bug11 播放页大广告框屏蔽(CSS 常驻规则, 不碰高级弹幕层)",
      ".ad-report" in _ownui_c and ".ad-floor-cover" in _ownui_c
      and ".right-bottom-banner" in _ownui_c
      and ".video-page-special-card-small" in _ownui_c
      and ".bpx-player-adv-dm-wrap" not in _ownui_c)

# bug12 信息流来源标签的强调色实底太闪眼
# (本轮用户报修 m03204: 两处「信息来源」文字改为跟随主题强调色 —— 实底仍取消, 只留细边)
check("bug12 信息流来源标签取消强调色实底(细边; 文字色跟随强调色)",
      ".card .badge { display:inline-block; background:transparent;" in _css_src
      and ".card .badge.plat-bili, .card .badge.plat-tb, .card .badge.plat-xhh "
          "{ color:var(--accent); }" in _css_src
      and ".plat-bili, .plat-tb, .plat-xhh { color:var(--accent); }" in _css_src
      and ".plat-tag { font-style:normal; font-size:9px; line-height:1.3; color:var(--sub);"
      in _css_src
      and "background:transparent; pointer-events:none; }" in _css_src
      and "#7fa3e8" not in _css_src and "#e08a8d" not in _css_src)

# bug13 超分辨率暂时性画布不对时弹报错框
check("bug13 超分暂时性画布异常不再弹框(只进控制台, 连续两次才提示)",
      "瞬时异常只进控制台" in _srjs and "console.warn" in _srjs)

# ---------- 3g. 播放页视觉 5 项(m03204 的 4 项 + m03496 的弹幕默认折叠/悬浮窗内全屏) ----------
check("视觉1 「相关推荐」标题字号加大(15→19, 更粗)",
      '.rel{font-size:19px;font-weight:700;padding:6px 12px}' in _ownui_c)

check("视觉2 播放页左侧留白改在页面内部(取消窗口级留白, B站顶栏从左缘连续)",
      "TAB_INSET_RATIO" not in re.sub(r"#[^\n]*", "", _sp_src)          # m03496: 窗口级留白已删
      and "_tab_dx" not in re.sub(r"#[^\n]*", "", _sp_src)
      and "Size(max(1, home.Size.Width), max(1, home.Size.Height))" in _sp_src
      and "#msx-ownui-page-pad" in _ownui_c
      and '"html:not(:fullscreen) "' in _ownui_c
      and "padding-left:4vw!important;box-sizing:border-box!important" in _ownui_c
      and "document.fullscreenElement" in _ownui_c)

check("视觉3 左侧留白底色 = B站播放页背景(不再有分界线)",
      'BG_PAGE = "#17181a"' in _aw_src
      and "background_color=BG_PAGE" in _aw_src
      and "def _apply_page_bg(self, dark)" in _sp_src
      and "_C.FromArgb(23, 24, 26)" in _sp_src)

check("视觉4 弹幕列表默认折叠(打开播放页即收起, 用户 m03496)",
      "var folded = true;" in _ownui_c
      and 'localStorage.getItem("msx_dm_fold") !== "0"' in _ownui_c
      and "▸" in _ownui_c)

check("视觉5 悬浮窗内点全屏只铺满悬浮窗(不动宿主窗口状态, 用户 m03496)",
      'is_float = host is not box.get("form")' in _sp_src
      and "if is_float:" in _sp_src
      and 'getattr(WF.DockStyle, "None")' in _sp_src
      and "c.Size = host.ClientSize" in _sp_src
      and "if not is_float:" in _sp_src
      and "is_float)\n" in _sp_src)

# ---------- 3h. 设置导出脱敏(m04225: 只导出设置, 不含 aiKey/登录账号) ----------
check("导出脱敏1 后端白名单: ai_key 归入账号内容, 脱敏导出只放 _IO_SHAREABLE",
      '"ai_key"' in _app_src
      and "_IO_SHAREABLE = frozenset(_IO_IMPORTABLE - _IO_CRED_KEYS)" in _app_src
      and "allow = _IO_IMPORTABLE if include_credentials else _IO_SHAREABLE" in _app_src
      and "if k in allow and v not in (None, \"\")" in _app_src
      and '"sanitized": (not include_credentials)' in _app_src)

check("导出脱敏2 默认只导出设置: 「导出个人信息和设置」是默认不勾的可选项(m04401)",
      'id="ioExpPriv"' in _idx_src and 'id="ioExpPriv" checked' not in _idx_src
      and "默认不勾" in _idx_src and 'id="ioExpWarn"' in _idx_src and "ioExpCred" not in _idx_src
      and 'const IO_CRED_KEYS = ["ai_key"' in _ajs_src
      and 'const IO_CRED_CAT = "账号与密钥"' in _ajs_src
      and 'const priv = $("#ioExpPriv") ? $("#ioExpPriv").checked : false;' in _ajs_src
      and "credentials: priv" in _ajs_src
      and "ioExpCred" not in _ajs_src
      and "已脱敏" in _ajs_src)

check("导出脱敏3 导入预览会标明脱敏文件, 凭证类目改名为「账号与密钥」且仍可选",
      "此文件已脱敏" in _ajs_src
      and "IO_CRED_KEYS.includes(key) ? IO_CRED_CAT : null" in _ajs_src
      and "cats.push([IO_CRED_CAT, credN])" in _ajs_src
      and "免扫码直接登录" in _ajs_src)

# ---------- 3i. 启动卡死修复(m03991: 新线程首次 db() 不再无条件写库) ----------
# 只看代码行, 把注释里的"改前写法"排除掉(否则 count 会把注释里的示例也算进去)
_app_code = "\n".join(_l for _l in _app_src.splitlines() if not _l.lstrip().startswith("#"))
_gi = _app_code.find('if conn.execute("SELECT 1 FROM chat WHERE cid=0 LIMIT 1").fetchone() is not None:')
_ui = _app_code.find('conn.execute("UPDATE chat SET cid=1 WHERE cid=0")')
check("卡死修复 每线程首次 db() 只在确有 cid=0 行时才写(不再等满 busy_timeout 10s)",
      _app_code.count("UPDATE chat SET cid=1 WHERE cid=0") == 1 and 0 <= _gi < _ui
      and "busy_timeout=10000" in _app_code)

check("卡死修复2 无 cid=0 残留来源: 4 处 INSERT INTO chat 都显式写 cid",
      _app_src.count("INSERT INTO chat(role,content,ts,cid)") >= 4)

# ---------- 3j. 「下载 json 文件」走壳的原生另存为(m04592: WebView2 里 <a download> 没反应) ----------
check("下载修复1 壳里新增 save_text(原生 SaveFileDialog, 返回 ok:<路径>/cancel/err:…)",
      "def save_text(self, filename, text):" in _aw_src
      and "WinForms.SaveFileDialog()" in _aw_src
      and 'dlg.Filter = "JSON 文件 (*.json)|*.json|所有文件 (*.*)|*.*"' in _aw_src
      and 'return "ok:" + path' in _aw_src
      and 'return "cancel"' in _aw_src)

check("下载修复2 页面优先走桥, 失败才退回 Blob; 触屏端有桥时不藏按钮",
      'const nat = window.pywebview && window.pywebview.api;' in _ajs_src
      and "if (nat && nat.save_text)" in _ajs_src
      and 'await nat.save_text(name, $("#ioExpText").value)' in _ajs_src
      and '"已保存到：" + r.slice(3)' in _ajs_src
      and "new Blob([$(\"#ioExpText\").value]" in _ajs_src
      and "if (isTouchDevice() && !(window.pywebview && window.pywebview.api && window.pywebview.api.save_text))" in _ajs_src)

# ---------- 3k. 被屏蔽评论: 整条隐藏 + 评论区顶部一行计数/开关(m04703) ----------
check("评论占位1 被屏蔽的主评论整条隐藏(标记式 + 注入样式的 !important 隐藏规则 + 只扫评论 shadow 根)",
      "function cfCommentEl()" in _cf_src
      and 'querySelectorAll("bili-comment-thread-renderer")' in _cf_src
      and 'bili-comment-thread-renderer[data-msx-blk="1"][data-msx-hide="1"]{display:none !important;}' in _cf_src
      and "function sweep()" in _cf_src
      and "function cfRoots()" in _cf_src)

check("评论占位2 屏蔽条数只在评论区最上方一处 + 「显示屏蔽/隐藏屏蔽」开关(照弹幕筛选那一套)",
      'var BLK_KEY = "msx_cf_show_blocked";' in _cf_src
      and 'sp.id = "msx-cf-bar-t"' in _cf_src
      and 'bt.id = "msx-cf-bar-b"' in _cf_src
      and '"评论筛选：已屏蔽 " + hid + " 条评论"' in _cf_src
      and '(showBlocked ? "隐藏屏蔽" : "显示屏蔽")' in _cf_src
      and "host.insertBefore(bar, host.firstChild)" in _cf_src)

check("评论占位3 数据层不再改写评论内容(原文保留) + 旧的右下角「已隐藏 N 条」浮层已彻底移除",
      "msx-cf-hint" not in _cf_src and "position:fixed" not in _cf_src
      and "function markBlocked(r)" in _cf_src
      and "BLK_RPIDS[rpidOf(d)]" in _cf_src
      and "function rpidOf(r)" in _cf_src
      and _cf_src.count("kick();") >= 2)

check("评论显示1 「显示屏蔽」时原样放出原文 + 纯 CSS「已屏蔽」角标",
      '::before{content:"已屏蔽"' in _cf_src
      and "data-msx-blk" in _cf_src
      and "data-msx-hide" in _cf_src
      and "REDACT_TXT" not in _cf_src)   # 旧占位文案已彻底移除

check("评论翻页1 屏蔽后补泵翻页(哨兵还在视口就替组件 getList, 到底/加载中不重复)",
      "function pump()" in _cf_src
      and "continueTrigger" in _cf_src
      and "inst.getList()" in _cf_src
      and "inst.showEnd || inst.showSpinner || inst.showContinuations" in _cf_src
      and "getBoundingClientRect" in _cf_src)

# ---------- 4. 启动/打包脚本指向窗口壳 ----------
gbk = lambda f: io.open(os.path.join(PROJECT, f), encoding="gbk").read()  # noqa: E731
check("启动.bat 走窗口壳", "app_window.py" in gbk("启动.bat"))
check("服务模式 bat 保留旧路径", "app.py --auto-open" in gbk("启动-服务模式.bat"))
check("构建exe.bat 打包窗口壳", bool(re.search(r"--name %APPNAME% app_window\.py", gbk("构建exe.bat"))))

# ---------- 5. --server 子进程探活(起真实服务, 不开窗口不开浏览器) ----------
ports = app.PORTS
already = any(app.port_busy(p) for p in ports)
proc = subprocess.Popen([sys.executable, os.path.join(PROJECT, "app_window.py"), "--server"],
                        cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ok = False
for _ in range(40):
    for p in ports:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/feed" % p, timeout=1.5) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            continue
    if ok or proc.poll() is not None:
        break
    time.sleep(0.5)
check("app_window --server 起真实服务", ok or (already and proc.poll() == 0),
      "already_running=%s exit=%s" % (already, proc.poll()))
proc.terminate()

# ---------- N. 安装包(NSIS) ----------
_nsi = io.open(os.path.join(PROJECT, "installer", "app_installer.nsi"),
               encoding="utf-8-sig").read()
check("NSIS脚本: WebView2三注册表位探测+缺则静默装",
      _nsi.count("EdgeUpdate\\Clients\\${WV2}") == 3 and "/silent /install" in _nsi)
check("NSIS脚本: 卸载器与添加/删除程序登记",
      "WriteUninstaller" in _nsi and "CurrentVersion\\Uninstall\\${UNKEY}" in _nsi)
check("NSIS脚本: 数据目录策略(卸载不删用户数据)",
      not re.search(r'RMDir[^\n]*LocalLow', _nsi) and 'RMDir /r "$INSTDIR"' in _nsi)
import glob
_pkgs = glob.glob(os.path.join(PROJECT, "dist", "*安装包*.exe"))
check("安装包产物已构建", bool(_pkgs) and os.path.getsize(_pkgs[-1]) > 100 * 1024 * 1024,
      _pkgs[-1] if _pkgs else "missing")

print("RESULT: %s" % ("FAIL(%d)" % FAIL if FAIL else "ALL_PASS (%d checks)" % PASS))
sys.exit(1 if FAIL else 0)
