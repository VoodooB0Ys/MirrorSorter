# -*- coding: utf-8 -*-
"""同一次运行内 DOM 探针 + 截图对照: ownui 开/关各一轮。
截图与探针报告按时间对齐, 找出顶栏"DOM可见但像素消失"的机制。"""
import json
import os
import subprocess
import sys
import threading
import time
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _e2e_shell import wait_window  # noqa: E402
from PIL import ImageGrab  # noqa: E402

u32 = ctypes.windll.user32
URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
OUT = os.path.join(HERE, "_probe_out.jsonl")
lock = threading.Lock()


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


def force_shot(hwnd, path):
    u32.ShowWindow(hwnd, 9)
    u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(path)
    u32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


def run(tag):
    import io
    if os.path.exists(OUT):
        os.remove(OUT)
    srv = HTTPServer(("127.0.0.1", 38117), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = dict(os.environ, MSX_PROBE="1")
    if tag == "ownui_off":
        env["MSX_NO_OWNUI"] = "1"
    log = open(os.path.join(HERE, "_shot_shell.log"), "a", encoding="utf-8")
    log.write("\n===== COMBO %s =====\n" % tag)
    log.flush()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app_window.py"), "--e2e-open", URL],
        cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        hwnd, _, _ = wait_window(proc.pid, "洞鉴虚明", 45)
        if not hwnd:
            print(tag, "NO WINDOW")
            return
        time.sleep(10)
        force_shot(hwnd, os.path.join(HERE, "_combo_%s_10s.png" % tag))
        time.sleep(8)
        force_shot(hwnd, os.path.join(HERE, "_combo_%s_18s.png" % tag))
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
        srv.shutdown()
    time.sleep(1)
    if not os.path.exists(OUT):
        print(tag, "NO PROBE DATA")
        return
    for line in io.open(OUT, encoding="utf-8").read().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if "www.bilibili.com/video" not in d.get("url", "") or not d.get("main"):
            continue
        m = d["main"]
        print("[%s] main disp=%s vis=%s top=%s rect=%s hidden=%d scrollY=%s hit=%s" % (
            tag, m["display"], m["vis"], m.get("top"), m.get("rect"),
            len(m.get("hiddenChain") or []), d.get("scrollY"),
            json.dumps(d.get("hitAtHeader"), ensure_ascii=False)[:60]))
        # 顶栏骨架树(压缩: 只列 disp!=none 的路径上的节点)
        def compact(o):
            if not o:
                return None
            kids = [compact(k) for k in (o.get("kids") or [])]
            kids = [k for k in kids if k]
            return {"c": o["cls"][:40], "d": o["disp"],
                    "kids": kids if kids else None}
        bt = d.get("barTree")
        if bt:
            print("   barTree: %s" % json.dumps(compact(bt), ensure_ascii=False)[:900])


def main():
    run("ownui_on")
    run("ownui_off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
