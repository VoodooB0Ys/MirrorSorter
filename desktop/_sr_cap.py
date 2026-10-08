# -*- coding: utf-8 -*-
"""超分截屏取证(多帧版): 起壳开视频页(SR 强制激活), BitBlt 连发截屏(ImageGrab,
与微信/QQ/PrntScr 同取图路径) + 探针回传 SR 状态。对每帧自动判黑: 裁出播放器
画面区域(窗口宽 8%~55%、高 45%~75%, 常规布局下必在视频内容区内), 近黑像素
占比 + 方差双阈值(纯黑帧 mean<6 且 std<3; 深色页面底 #0f0f0f 不会被误判)。
验证: v0.3.1.7dsk 三层修复后 GDI 截屏不再偶发黑屏。输出 _srcap_s*.png。"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _e2e_shell import wait_window                     # noqa: E402

# DPI 感知: 不声明的话 GetWindowRect 返回被虚拟化的坐标, 与 ImageGrab 的
# 物理像素 bbox 对不上 → 截到别处(实证: 全屏截成用户桌面纯色壁纸)。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

u32 = ctypes.windll.user32
from PIL import ImageGrab                              # noqa: E402  (DPI 声明之后再 import)

OUT = os.path.join(HERE, "_probe_out.jsonl")
lock = threading.Lock()

N_SHOTS = int(os.environ.get("MSX_CAP_COUNT", "8"))
SHOT_GAP = float(os.environ.get("MSX_CAP_GAP", "5"))


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "ignore")
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(body + "\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def judge(path):
    """裁播放器画面区域判黑: 返回 (mean, std, black?)"""
    from PIL import Image, ImageStat
    im = Image.open(path).convert("L")
    w, h = im.size
    crop = im.crop((int(w * 0.08), int(h * 0.45), int(w * 0.55), int(h * 0.75)))
    st = ImageStat.Stat(crop)
    mean, std = st.mean[0], st.stddev[0]
    return round(mean, 1), round(std, 1), bool(mean < 6 and std < 3)


def force_shot(hwnd, path):
    """强制置前后截窗口矩形(诊断用途, 允许抢焦点)。带窗口状态取证:
    若截到纯色帧(std≈0)说明截的不是本窗口 → 全屏留底一张供分析。"""
    u32.ShowWindow(hwnd, 9)          # SW_RESTORE
    u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # TOPMOST+NOSIZE+NOMOVE
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    cloaked = wintypes.DWORD(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))   # DWMWA_CLOAKED
    except Exception:
        pass
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(path)
    diag = "rect=(%d,%d,%d,%d) iconic=%d vis=%d cloaked=%d" % (
        r.left, r.top, r.right, r.bottom,
        u32.IsIconic(hwnd), u32.IsWindowVisible(hwnd), cloaked.value)
    from PIL import ImageStat
    st = ImageStat.Stat(img.convert("L"))
    if st.stddev[0] < 1.0:   # 整窗纯色 → 截屏必然没截到窗口内容, 留全屏底
        full = os.path.join(os.path.dirname(path), "_srcap_full_%d.png" % int(time.time()))
        ImageGrab.grab(all_screens=True).save(full)
        diag += " [纯色帧! 全屏底=%s]" % os.path.basename(full)
    u32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOTOPMOST
    return r.right - r.left, r.bottom - r.top, diag


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    srv = HTTPServer(("127.0.0.1", 38117), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = dict(os.environ, MSX_PROBE="1", MSX_SR_BYPASS_GATE="1", MSX_SR_FORCE_SR="1")
    log = open(os.path.join(HERE, "_probe_shell.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app_window.py"),
         "--e2e-open", "https://www.bilibili.com/video/BV1GJ411x7h7"],
        cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
    shots = []
    try:
        hwnd, title, rect = wait_window(proc.pid, "洞鉴虚明", 45)
        if not hwnd:
            print("NO WINDOW")
            return 1
        time.sleep(12)   # 页面加载 + SR 管线启动 + 探针 t+12 报点
        for i in range(N_SHOTS):
            p = os.path.join(HERE, "_srcap_s%d.png" % i)
            w, h, diag = force_shot(hwnd, p)
            shots.append(p)
            print("shot#%d %dx%d %s" % (i, w, h, diag))
            if i < N_SHOTS - 1:
                time.sleep(SHOT_GAP)
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
        srv.shutdown()
    print("---- 判黑结果(播放器区域 mean/std, mean<6且std<3 => BLACK) ----")
    nblack = 0
    for p in shots:
        m, s, black = judge(p)
        nblack += black
        print("%-28s mean=%-6s std=%-6s %s" % (os.path.basename(p), m, s,
                                               "BLACK!" if black else "ok"))
    print("RESULT: %d/%d black" % (nblack, len(shots)))
    import io
    seen = set()
    for line in io.open(OUT, encoding="utf-8").read().splitlines():
        d = json.loads(line)
        if "www.bilibili.com/video" not in d.get("url", "") or d.get("t") in seen:
            continue
        seen.add(d.get("t"))
        s = d.get("sr")
        if s:
            cv = s.get("canvas") or {}
            print("[probe] SR mode=%s frames=%s fail=%s" % (s.get("mode"), s.get("frames"), s.get("fail")))
            print("        canvas=%sx%s disp=%s radius=%s" % (cv.get("w"), cv.get("h"), cv.get("disp"), cv.get("radius")))
            print("        video opacity=%s vis=%s disp=%s radius=%s paused=%s" % (
                s.get("videoOp"), s.get("videoVis"), s.get("videoDisp"),
                s.get("videoRadius"), s.get("paused")))
        else:
            print("[probe] (no SR canvas/video yet) url=%s" % d.get("url", "")[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
