# -*- coding: utf-8 -*-
"""顶栏消失取证(截图版): 起壳 --e2e-open 视频页, +3s/+14s 各截一张屏。
复用 _e2e_shell 的窗口查找/截图辅助。输出 _shot_home/_shot_t3/_shot_t14.png。
"""
import os
import subprocess
import sys
import time
import ctypes
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _e2e_shell import wait_window, wins_of  # noqa: E402
from PIL import ImageGrab  # noqa: E402

u32 = ctypes.windll.user32
URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


def force_shot(hwnd, path):
    """强制置前后截窗口矩形(诊断用途, 允许抢焦点)。"""
    u32.ShowWindow(hwnd, 9)          # SW_RESTORE
    u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # TOPMOST+NOSIZE+NOMOVE
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(path)
    u32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOTOPMOST
    return r.right - r.left, r.bottom - r.top


def main():
    log = open(os.path.join(HERE, "_shot_shell.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app_window.py"), "--e2e-open", URL],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
    try:
        hwnd, title, rect = wait_window(proc.pid, "洞鉴虚明", 45)
        if not hwnd:
            print("NO WINDOW")
            return 1
        time.sleep(3)
        w, h = force_shot(hwnd, os.path.join(HERE, "_shot_t3.png"))
        print("t+3s shot %dx%d" % (w, h))
        time.sleep(11)
        w, h = force_shot(hwnd, os.path.join(HERE, "_shot_t14.png"))
        print("t+14s shot %dx%d" % (w, h))
        time.sleep(11)
        w, h = force_shot(hwnd, os.path.join(HERE, "_shot_t25.png"))
        print("t+25s shot %dx%d" % (w, h))
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
