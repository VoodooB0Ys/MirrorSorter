# -*- coding: utf-8 -*-
"""超分截屏取证: 起壳开视频页(SR 按用户真实设置激活), BitBlt 截屏(ImageGrab,
与微信/QQ/PrntScr 同取图路径) + 探针回传 SR 状态。验证: 超分画布是否被
GDI 截屏取到(修复前被 DC overlay 提升则截到黑屏)。输出 _srcap_*.png。"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _shot_run import force_shot                       # noqa: E402
from _e2e_shell import wait_window                     # noqa: E402

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
    try:
        hwnd, title, rect = wait_window(proc.pid, "洞鉴虚明", 45)
        if not hwnd:
            print("NO WINDOW")
            return 1
        time.sleep(12)   # 页面加载 + SR 管线启动 + 探针 t+12 报点
        w, h = force_shot(hwnd, os.path.join(HERE, "_srcap_t12.png"))
        print("t+12s BitBlt shot %dx%d" % (w, h))
        time.sleep(10)
        w, h = force_shot(hwnd, os.path.join(HERE, "_srcap_t22.png"))
        print("t+22s BitBlt shot %dx%d" % (w, h))
        time.sleep(8)    # 等探针 t+18 报点
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
        srv.shutdown()
    import io
    seen = set()
    for line in io.open(OUT, encoding="utf-8").read().splitlines():
        d = json.loads(line)
        if "www.bilibili.com/video" not in d.get("url", "") or d.get("t") in seen:
            continue
        seen.add(d.get("t"))
        s = d.get("sr")
        if s:
            print("[probe] SR mode=%s frames=%s fail=%s" % (s.get("mode"), s.get("frames"), s.get("fail")))
            print("        canvas=%s radius=%s disp=%s" % (s.get("canvas"), s.get("radius"), (s.get("canvas") or {}).get("disp")))
            print("        video opacity=%s vis=%s paused=%s" % (s.get("videoOp"), s.get("videoVis"), s.get("paused")))
        else:
            print("[probe] (no SR canvas/video yet) url=%s" % d.get("url", "")[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
