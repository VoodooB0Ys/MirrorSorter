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
check("app.APP_VER 版本常量", app.APP_VER == "v0.3.1.8dsk", app.APP_VER)

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
      and "DeleteRegKey HKLM \"SOFTWARE\Microsoft\EdgeUpdate\"" in io.open(os.path.join(PROJECT, "installer", "app_installer.nsi"), encoding="utf-8-sig").read())
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
check("自有UI不碰播放器DOM(脚本无播放器选择器)",
      "bilibili-player" not in _ownui and "bpx-player" not in _ownui)
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
check("页脚版本 v0.3.1.8dsk", "洞鉴虚明 v0.3.1.8dsk" in html)
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
