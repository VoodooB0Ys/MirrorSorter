# -*- coding: utf-8 -*-
"""v0.3.1.46dsk 验收: 打包产物里「弹幕侧 fastText 与弹幕总开关解耦」真的生效。

分三层, 逐层加强说服力:
  【1】静态: exe 的 CArchive 里抽出来的 extensions/bilibili-dmfilter.user.js
       必须是修复后的版本, 且与源码字节一致(防止"改了源码但没重打包")。
  【2】行为: 把**抽出来的那一份** userscript 喂给 node 沙盒(_test_dm_ft_gate.js)
       跑真实 seg.so 字节流 —— 证明"装进 exe 的那份代码"行为是对的, 不只是
       源码对。这是本脚本最有价值的一步。
  【3】运行期: 用隔离数据目录 + 隔离端口起 exe, 按**用户的实际配置**
       (m_dm_on=0 + ft_on=1 + ft_dm=1) 验证 /api/textfilter 的 dm 通道照常工作。
       全程不碰用户的 data.db。

用法: python _verify_dmft_gate.py [exe路径]
"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"D:\Information Sorter\desktop"
FAIL = []


def chk(cond, msg):
    print(("  [OK] " if cond else "  [!!] ") + msg)
    if not cond:
        FAIL.append(msg)


def pick_exe():
    d = os.path.join(BASE, "dist")
    try:
        cand = [os.path.join(d, f) for f in os.listdir(d)
                if f.startswith("洞鉴虚明v") and f.endswith(".exe")]
        cand.sort(key=os.path.getmtime)
        if cand:
            return cand[-1]
    except Exception:
        pass
    return os.path.join(d, "洞鉴虚明.exe")


EXE = sys.argv[1] if len(sys.argv) > 1 else pick_exe()
_m = re.search(r"(v[\d.]+dsk)", os.path.basename(EXE))
VER = _m.group(1) if _m else "v0.0.0dsk"
print("=" * 76)
print("验收目标: %s" % EXE)
print("=" * 76)

# ------------------------------------------------------------------ 【1】静态
print()
print("【1】归档内容: exe 里那份 userscript 是不是修复后的")
print("-" * 76)
from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

rd = CArchiveReader(EXE)
toc = dict(rd.toc)

key = next((k for k in toc if k.replace("\\", "/").endswith("extensions/bilibili-dmfilter.user.js")), None)
chk(key is not None, "CArchive 里有 extensions/bilibili-dmfilter.user.js")
packed_js = None
if key:
    b = rd.extract(key)
    packed_js = (b[1] if isinstance(b, tuple) else b).decode("utf-8", "replace")
    src_path = os.path.join(BASE, "extensions", "bilibili-dmfilter.user.js")
    src = io.open(src_path, encoding="utf-8").read()
    chk(packed_js == src, "打包内 userscript 与源码**字节一致**(防止改了源码没重打包)")

if packed_js:
    lines = [ln.strip() for ln in packed_js.splitlines() if ln.strip().startswith("var NEED_TEXT")]
    chk(lines == ["var NEED_TEXT = !!CF.ft_dm;"],
        "打包内 NEED_TEXT 已解耦(实际: %s)" % lines)
    chk("askText(r.texts, false);" in packed_js, "打包内 XHR 路径已补文本预热")
    chk("var MYSTERY = DM_ON && !!CF.mystery;" in packed_js,
        "打包内身份规则仍受总开关管(解耦没解过头)")

key2 = next((k for k in toc if k.replace("\\", "/").endswith("web/index.html")), None)
if key2:
    b = rd.extract(key2)
    html = (b[1] if isinstance(b, tuple) else b).decode("utf-8", "replace")
    chk("「快速文本分析」的弹幕通道不受本开关影响" in html, "打包内设置页文案已更新")
    chk(VER in html, "打包内页脚版本 = %s" % VER)
else:
    chk(False, "CArchive 里有 web/index.html")

# ------------------------------------------------------------------ 【2】行为
print()
print("【2】行为: 把**exe 里抽出来的那份** userscript 跑一遍 node 沙盒")
print("-" * 76)
if packed_js:
    tmp_js = os.path.join(tempfile.gettempdir(), "msx_packed_dmfilter.js")
    with io.open(tmp_js, "w", encoding="utf-8", newline="\n") as f:
        f.write(packed_js)
    node = r"C:\Users\sunsi\.workbuddy-ai\binaries\node\versions\22.22.2-6\node.exe"
    env = dict(os.environ, MSX_DM_SRC=tmp_js)
    r = subprocess.run([node, os.path.join(BASE, "_test_dm_ft_gate.js")],
                       cwd=BASE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    for ln in out.splitlines():
        if ln.startswith(("PASS", "FAIL", "RESULT")):
            print("    " + ln)
    chk(r.returncode == 0 and "ALL_PASS" in out,
        "打包内 userscript 的行为回归全过(退出码 %s)" % r.returncode)

# ------------------------------------------------------------------ 【3】运行期
print()
print("【3】运行期: 隔离库 + 隔离端口, 按用户实际配置(m_dm_on=0 + ft_on=1 + ft_dm=1)")
print("-" * 76)
s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
DATA = os.path.join(tempfile.gettempdir(), "msx_verify_dmft_%d" % PORT)
os.makedirs(DATA, exist_ok=True)
print("  隔离数据目录: %s" % DATA)
print("  隔离端口: %d (不动用户正在用的 8000)" % PORT)

flags = 0x08000000 if os.name == "nt" else 0
env = dict(os.environ, MIRRORSORTER_PORT=str(PORT),
           MIRRORSORTER_DATA_DIR=DATA, MIRRORSORTER_NO_OPEN="1")
proc = subprocess.Popen([EXE, "--server"], creationflags=flags, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    up = False
    for _ in range(90):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/feed" % PORT, timeout=1.5) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            pass
        time.sleep(0.5)
    chk(up, "exe 起了服务 (退出码 %s)" % proc.poll())
    if up:
        base = "http://127.0.0.1:%d" % PORT

        def get(path):
            with urllib.request.urlopen(base + path, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        def post(path, obj):
            req = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                         headers={"Content-Type": "text/plain;charset=UTF-8"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        # 复刻用户的实际配置: 总开关关、fastText 开(含弹幕通道)、五个标签全勾
        post("/api/machine", {"m_dm_on": "0", "ft_on": "1", "ft_dm": "1", "ft_cf": "1",
                              "ft_video": "1", "ft_thresh": "70",
                              "ft_labels": "flame,spam,clickbait,yy,neg"})
        cfg = get("/api/machine").get("settings") or {}
        chk(cfg.get("m_dm_on") == "0", "隔离库里 m_dm_on=0 (复刻用户配置)")
        chk(cfg.get("ft_on") == "1" and cfg.get("ft_dm") == "1", "隔离库里 ft_on=1 ft_dm=1")

        st = None
        for _ in range(60):
            st = get("/api/textfilter/status")
            if st.get("ready"):
                break
            time.sleep(0.25)
        chk(st and st.get("ready") is True, "exe 里模型已加载(%.1fMB)" % (st or {}).get("size_mb", 0))

        # ★ 核心: dm 通道必须照常判定, 哪怕弹幕总开关是关的
        j = post("/api/textfilter", {"kind": "dm",
                                     "texts": ["赢麻了，又是魂游", "这游戏画面真不错", "典中典，急了急了"]})
        print("    /api/textfilter kind=dm → %s" % json.dumps(j, ensure_ascii=False))
        chk(j.get("ok") is True and j.get("on") is True, "dm 通道在总开关关闭时仍然生效")
        chk(j.get("why", [""])[0] != "", "「赢麻了，又是魂游」被命中")
        chk(j.get("why", ["", ""])[1] == "", "「这游戏画面真不错」放行")

        # 身份规则必须仍然被总开关掐死(不能解耦解过头)
        d = get("/api/bili/dmflags?bio=1&lot=1&nodyn=1&hashes=aabbccdd,11223344")
        print("    /api/bili/dmflags → %s" % json.dumps(d, ensure_ascii=False))
        chk(d.get("flags") == {} and d.get("pending") == [],
            "总开关关 → 身份规则零请求零入队(解耦没解过头)")

        # 反向: 把总开关打开, 身份规则要能正常工作(证明上面不是"因为接口坏了")
        post("/api/machine", {"m_dm_on": "1"})
        d2 = get("/api/bili/dmflags?bio=0&lot=1&nodyn=0&hashes=aabbccdd")
        print("    m_dm_on=1 后 → %s" % json.dumps(d2, ensure_ascii=False))
        chk(len(d2.get("pending") or []) > 0, "总开关打开后身份规则恢复入队")
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
        shutil.rmtree(DATA, ignore_errors=True)
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
