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
check("app.APP_VER 版本常量", app.APP_VER == "v0.2.9.0a", app.APP_VER)

# ---------- 2. app_window 标签页管理与降级探测 ----------
aw = load("app_window_shell_test", "app_window.py")
check("壳版本与 app 一致", aw.APP_VER == app.APP_VER)
check("WebView2 运行时探测可用", isinstance(aw.webview2_available(), bool))
check("pywebview 探测可用", isinstance(aw.have_pywebview(), bool))

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

# ---------- 3. 前端统一出口 ----------
js = io.open(os.path.join(PROJECT, "web", "app.js"), encoding="utf-8").read()
fallback = js.count('window.open(url, "_blank", "noopener")')
check("app.js 裸 window.open 只剩桥内兜底", fallback == 3, str(fallback))
check("app.js 跳转出口调用点 ≥ 10", js.count("openExternal(") >= 11, str(js.count("openExternal(")))
check("index.html 无 target=_blank", 'target="_blank"' not in io.open(
    os.path.join(PROJECT, "web", "index.html"), encoding="utf-8").read())
html = io.open(os.path.join(PROJECT, "web", "index.html"), encoding="utf-8").read()
check("页脚版本 v0.2.9.0a", "洞鉴虚明 v0.2.9.0a" in html)
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

print("RESULT: %s" % ("FAIL(%d)" % FAIL if FAIL else "ALL_PASS (%d checks)" % PASS))
sys.exit(1 if FAIL else 0)
