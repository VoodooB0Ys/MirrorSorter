# -*- coding: utf-8 -*-
"""打包产物验收：静态(归档里到底装了什么) + 运行期(exe 里模型真的能跑)。

版本号从 exe 文件名反推, 所以这个脚本**不用每版改一遍**:
    python _verify_exe.py                     # 自动取 dist 下最新的 洞鉴虚明v*.exe
    python _verify_exe.py <某个 exe 的路径>

静态部分回答"模型有没有被打进去":
  · CArchive 的 TOC 里有没有 model/ft_model.bin, 字节数对不对;
  · PYZ 里有没有 ftrt(纯 Python 运行时);
  · web/index.html 里「快速文本分析」那套 DOM 在不在。

运行期部分回答"打进去之后能不能用" —— 这是唯一有说服力的证据:
  起 exe --server → POST /api/textfilter/probe 与 /api/textfilter →
  必须返回真实的标签/概率, 且 /api/textfilter/status 的 size_mb > 50。
  (源码态能跑不代表打包态能跑: sys._MEIPASS 路径、hiddenimport 都可能漏。)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _pick_exe():
    """默认取 dist 下最新的 洞鉴虚明v*.exe。"""
    d = r"D:\Information Sorter\desktop\dist"
    try:
        cand = [os.path.join(d, f) for f in os.listdir(d)
                if f.startswith("洞鉴虚明v") and f.endswith(".exe")]
        cand.sort(key=os.path.getmtime)
        if cand:
            return cand[-1]
    except Exception:
        pass
    return os.path.join(d, "洞鉴虚明.exe")


EXE = sys.argv[1] if len(sys.argv) > 1 else _pick_exe()
_m = re.search(r"(v[\d.]+dsk)", os.path.basename(EXE))
VER = _m.group(1) if _m else "v0.0.0dsk"
FAIL = []


def _free_port():
    """★ 绝不碰 8000/8010/8020 —— 用户可能正开着软件在用。
    走 MIRRORSORTER_PORT 指定单一端口(不回落), 拿一个临时空闲口来验收。"""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()


def chk(cond, msg):
    print(("  [OK] " if cond else "  [!!] ") + msg)
    if not cond:
        FAIL.append(msg)


# ---------------------------------------------------------------- 静态
print("=" * 76)
print("【1】归档内容: 模型 / 运行时 / 页面 是否都在包里")
print("=" * 76)
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader  # noqa: E402

chk(os.path.isfile(EXE), "exe 存在: %s (%.1f MB)" % (EXE, os.path.getsize(EXE) / 1048576.0))
rd = CArchiveReader(EXE)
toc = dict(rd.toc)
print("  CArchive 条目数: %d" % len(toc))

# --add-data 的资源在 TOC 里以 "model/ft_model.bin" 这种相对路径出现
model_key = next((k for k in toc if k.replace("\\", "/").endswith("model/ft_model.bin")), None)
chk(model_key is not None, "CArchive 里找到 model/ft_model.bin")
if model_key:
    blob = rd.extract(model_key)
    n = len(blob[1] if isinstance(blob, tuple) else blob)
    print("  打包内模型体积: %.2f MB" % (n / 1048576.0))
    chk(n > 50 * 1024 * 1024, "打包内模型 > 50MB")
    on_disk = os.path.getsize(r"D:\Information Sorter\desktop\model\ft_model.bin")
    chk(n == on_disk, "打包内模型与源文件字节数一致 (%d == %d)" % (n, on_disk))

idx_key = next((k for k in toc if k.replace("\\", "/").endswith("web/index.html")), None)
chk(idx_key is not None, "CArchive 里找到 web/index.html")
if idx_key:
    b = rd.extract(idx_key)
    html = (b[1] if isinstance(b, tuple) else b).decode("utf-8", "replace")
    for token in ('id="cfTabText"', 'id="ftOn"', 'id="ftDm"', 'id="ftCf"',
                  'id="ftVideo"', 'id="ftThreshRow"', 'id="ftProbeIn"', 'id="ftProbeBtn"'):
        chk(token in html, "index.html 含 %s" % token)
    chk(VER in html, "index.html 页脚版本 = %s" % VER)

pyz_name = next((n for n in toc if n.endswith(".pyz")), None)
tmp = os.path.join(tempfile.gettempdir(), "msx_verify_exe.pyz")
with open(tmp, "wb") as f:
    b = rd.extract(pyz_name)
    f.write(b[1] if isinstance(b, tuple) else b)
z = ZlibArchiveReader(tmp)
ztoc = set(z.toc)
print("  PYZ 模块数: %d" % len(ztoc))
chk("ftrt" in ztoc, "PYZ 里有 ftrt(纯 Python 推理运行时)")
chk("app" in ztoc, "PYZ 里有 app")


def grab(mod):
    if mod not in ztoc:
        return None
    obj = z.extract(mod)
    if isinstance(obj, tuple):
        obj = obj[-1]
    if not hasattr(obj, "co_consts"):
        return None
    consts, funcs = set(), set()

    def walk(co):
        for c in getattr(co, "co_consts", ()):
            if isinstance(c, (str, float, int)):
                consts.add(c)
            elif isinstance(c, tuple):
                for x in c:
                    if isinstance(x, (str, float, int)):
                        consts.add(x)
            elif hasattr(c, "co_consts"):
                funcs.add(c.co_name)
                walk(c)
    walk(obj)
    return consts, funcs


g = grab("ftrt")
chk(g is not None and "FastTextLite" in g[1], "ftrt 里定义了 FastTextLite")
g = grab("app")
if g:
    consts, funcs = g
    strs = {x for x in consts if isinstance(x, str)}
    for fn in ("ft_scan", "ft_probe", "ft_load", "ft_status", "_ft_path"):
        chk(fn in funcs, "app 里定义了 %s" % fn)
    chk(any(VER in s for s in strs), "app 常量里有版本号 %s" % VER)

# v0.3.1.44dsk: 标签栏"往下拖才撕出悬浮窗"的修复必须在包里
g = grab("shell_tabstrip")
if g is None:
    chk(False, "PYZ 里有 shell_tabstrip")
else:
    consts, funcs = g
    chk("detach_verdict" in funcs, "shell_tabstrip 里定义了 detach_verdict(可单测的向下拖判据)")
    chk("on_mouse_up" in funcs and "on_capture_changed" in funcs,
        "拖拽收尾处理器在位(on_mouse_up / on_capture_changed)")
    chk("on_mouse_move" in funcs and "on_mouse_down" in funcs, "鼠标处理器在位")
    # ★ 不能查 co_consts 找小整数: Python 3.12+ 起 26/8 这类值走 LOAD_SMALL_INT,
    #   值直接编码在 oparg 里, 根本不进常量表(踩过 —— 查常量表会误判成"没打进去")。
    import dis as _dis
    obj = z.extract("shell_tabstrip")
    if isinstance(obj, tuple):
        obj = obj[-1]
    small = {ins.argval for ins in _dis.get_instructions(obj)
             if ins.opname == "LOAD_SMALL_INT" and isinstance(ins.argval, int)}
    chk({26, 8} <= small, "阈值常量 DETACH_DY=26 / HINT_DY=8 在包里 (LOAD_SMALL_INT %s)" % sorted(small))

# ---------------------------------------------------------------- 运行期
print()
print("=" * 76)
print("【2】运行期: exe 里模型真的能加载并给出判定")
print("=" * 76)
print("  验收端口: %d (MIRRORSORTER_PORT 隔离, 不动用户正在用的 8000)" % PORT)

flags = 0x08000000 if os.name == "nt" else 0
# ★ MIRRORSORTER_NO_OPEN=1 是必须的: frozen exe 的 --server 模式默认 auto_open=True,
#   会**真的在用户桌面上弹一个浏览器页**; 而那个页面一关就 sendBeacon("/api/shutdown")
#   把测试服务杀掉 —— 表现为跑到一半突然 RemoteDisconnected(踩过), 还会骚扰用户。
# ★ v0.3.1.46dsk 起再加 MIRRORSORTER_DATA_DIR 指向临时目录: 本脚本会写 ft_on / ft_labels
#   这类设置, 直接跑会把**用户的**配置改掉(实测把 ft_labels 从五个标签改成四个)。
#   模型在 _MEIPASS 里, 跟 DATA_DIR 无关, 所以隔离库不影响模型相关的断言。
_DATA = os.path.join(tempfile.gettempdir(), "msx_verify_exe_%d" % PORT)
os.makedirs(_DATA, exist_ok=True)
env = dict(os.environ, MIRRORSORTER_PORT=str(PORT), MIRRORSORTER_NO_OPEN="1",
           MIRRORSORTER_DATA_DIR=_DATA)
print("  隔离数据目录: %s" % _DATA)
proc = subprocess.Popen([EXE, "--server"], creationflags=flags, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    port = None
    for _ in range(80):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/feed" % PORT, timeout=1.5) as r:
                if r.status == 200:
                    port = PORT
        except Exception:
            pass
        if port:
            break
        time.sleep(0.5)
    print("  服务端口: %s (exe 退出码 %s)" % (port, proc.poll()))
    if not port:
        chk(False, "exe 起了服务")
        raise SystemExit(1)

    base = "http://127.0.0.1:%d" % port

    def get(path):
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def post(path, obj):
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                     headers={"Content-Type": "text/plain;charset=UTF-8"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    # 先把总开关打开(设置写库, 重启后仍在)
    post("/api/machine", {"ft_on": "1", "ft_thresh": "70",
                          "ft_labels": "flame,spam,clickbait,yy",
                          "ft_video": "1", "ft_dm": "1", "ft_cf": "1"})

    # ★ 打开总开关会触发 ft_warmup() 后台预热(免得用户第一次筛选卡一下),
    #   所以这里等它加载完再断言 —— 不是"立刻 ready", 而是"很快 ready"。
    st = None
    for _ in range(40):
        st = get("/api/textfilter/status")
        if st.get("ready"):
            break
        time.sleep(0.25)
    print("  /api/textfilter/status → %s" % json.dumps(st, ensure_ascii=False)[:260])
    chk(st.get("ok") is True, "状态接口可用")
    chk(st.get("size_mb", 0) > 50, "exe 里模型 > 50MB (%.1fMB)" % st.get("size_mb", 0))
    chk(st.get("has_module") is True, "exe 里 ftrt 可导入")
    chk("_MEI" in (st.get("path") or ""), "模型路径指向 _MEIPASS 解包目录: %s" % st.get("path"))
    chk(not st.get("err"), "加载无报错 (%r)" % st.get("err"))
    chk(st.get("ready") is True, "开总开关后后台预热已把模型读进内存")

    print()
    print("  「试一下」逐条:")
    CASES = [("赢麻了，又是魂游", True), ("震惊！99%的人不知道这个秘密", True),
             ("你就是个废物，滚吧", True), ("这游戏画面真不错，推荐", False)]
    for txt, want_hit in CASES:
        r = post("/api/textfilter/probe", {"text": txt})
        print("    %-24s → %s" % (txt[:24], json.dumps(r, ensure_ascii=False)[:130]))
        chk(r.get("ok") is True, "「%s」能判定" % txt[:10])
        chk(r.get("hit") == want_hit, "「%s」hit=%s" % (txt[:10], r.get("hit")))

    print()
    print("  【2b】判定之后状态复核(标签表 / 加载时间戳 / 缓存):")
    st2 = get("/api/textfilter/status")
    print("    /api/textfilter/status → %s" % json.dumps(st2, ensure_ascii=False)[:260])
    chk(st2.get("ready") is True, "首次使用后模型已加载")
    chk(st2.get("loaded_at", 0) > 0, "记录了加载时间戳")
    chk(len(st2.get("labels") or []) == 6, "读到 6 个标签: %s" % st2.get("labels"))

    print()
    print("  三条链路批量:")
    for kind in ("video", "dm", "cf"):
        j = post("/api/textfilter", {"kind": kind,
                                     "texts": ["这游戏画面真不错", "赢麻了", "震惊！99%的人不知道"]})
        print("    kind=%-5s → %s" % (kind, json.dumps(j, ensure_ascii=False)))
        chk(j.get("ok") is True and j.get("on") is True, "kind=%s 生效" % kind)
        chk(len(j.get("why", [])) == 3, "kind=%s 返回长度一致" % kind)
        chk(j["why"][1] != "", "kind=%s 阴阳怪气被命中" % kind)

    print()
    print("  独立开关(总开关关掉 → 三通道全放行):")
    post("/api/machine", {"ft_on": "0"})
    j = post("/api/textfilter", {"kind": "dm", "texts": ["赢麻了"]})
    print("    ft_on=0 → %s" % json.dumps(j, ensure_ascii=False))
    chk(j.get("on") is False and j.get("why") == [""], "总开关关掉时整批放行")
    post("/api/machine", {"ft_on": "1"})

    with urllib.request.urlopen(base + "/", timeout=10) as r:
        home = r.read().decode("utf-8", "replace")
    m = re.search(r"洞鉴虚明\s*(v[\d.]+dsk)", home)
    chk(m and m.group(1) == VER, "首页页脚版本 = %s (实际 %s)" % (VER, m.group(1) if m else None))
finally:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        import shutil
        shutil.rmtree(_DATA, ignore_errors=True)
    except Exception:
        pass

print()
print("=" * 76)
if FAIL:
    print("失败 %d 项:" % len(FAIL))
    for f in FAIL:
        print("   - %s" % f)
else:
    print("ALL_PASS")
print("=" * 76)
sys.stdout.flush()
sys.exit(1 if FAIL else 0)
