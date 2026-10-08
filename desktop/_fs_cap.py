# -*- coding: utf-8 -*-
"""复现: 应用窗口'窗口态' vs '软件全屏态'(复刻 shell_tabstrip._fill_bounds) 下,
   对播放中的B站视频做 BitBlt 窗口截屏, 判播放器区域是否黑。
   用法: python _fs_cap.py [源码|exe]
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _shot_run import force_shot                       # noqa: E402
from _e2e_shell import wait_window                     # noqa: E402

PY = r"C:\Python314\python.exe"
EXE = os.path.join(HERE, "dist", "洞鉴虚明v0.3.1.7dsk.exe")
URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
N = int(os.environ.get("MSX_FS_N", "6"))
GAP = float(os.environ.get("MSX_FS_GAP", "3"))


def judge(path):
    from PIL import Image, ImageStat
    im = Image.open(path).convert("L")
    w, h = im.size
    crop = im.crop((int(w * 0.08), int(h * 0.45), int(w * 0.55), int(h * 0.75)))
    st = ImageStat.Stat(crop)
    m, s = st.mean[0], st.stddev[0]
    return round(m, 1), round(s, 1), bool(m < 6 and s < 3)


def set_bounds(hwnd, x, y, w, h):
    u32 = ctypes.windll.user32
    SWP = 0x0004 | 0x0010   # NOZORDER | NOACTIVATE
    u32.SetWindowPos(hwnd, 0, x, y, w, h, SWP)


def grab(hwnd, tag, shots):
    for i in range(N):
        p = os.path.join(HERE, "_fs_%s_%d.png" % (tag, i))
        try:
            force_shot(hwnd, p)
        except Exception as e:
            print("  shot err", e)
        shots.append((p, tag))
        if i < N - 1:
            time.sleep(GAP)


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "src").lower()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    u32 = ctypes.windll.user32
    sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    print("screen", sw, "x", sh)

    if mode == "exe":
        cmd, cwd = [EXE, "--e2e-open", URL], os.path.dirname(EXE)
    else:
        cmd, cwd = [PY, os.path.join(HERE, "app_window.py"), "--e2e-open", URL], HERE
    log = open(os.path.join(HERE, "_fs_shell.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    shots = []
    try:
        hwnd, title, rect = wait_window(proc.pid, "洞鉴虚明", 45)
        if not hwnd:
            print("NO WINDOW")
            return 1
        print("window rect", rect)
        time.sleep(16)   # 加载 + 视频起播
        print("---- 窗口态 ----")
        grab(hwnd, "win", shots)
        # 复刻 _fill_bounds: 整屏(留1px)
        set_bounds(hwnd, 0, 0, sw, sh - 1)
        time.sleep(5)
        print("---- 软件全屏态 (0,0,%d,%d) ----" % (sw, sh - 1))
        grab(hwnd, "fs", shots)
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()

    print("---- 判黑(播放器区 mean/std) ----")
    for tag in ("win", "fs"):
        nb = 0
        rows = []
        for p, t in shots:
            if t != tag:
                continue
            m, s, black = judge(p)
            nb += black
            rows.append("%s mean=%-6s std=%-6s %s" % (os.path.basename(p), m, s,
                                                      "BLACK!" if black else "ok"))
        print("[%s]" % tag)
        for r in rows:
            print("   ", r)
        print("   小计: %d/%d black" % (nb, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
