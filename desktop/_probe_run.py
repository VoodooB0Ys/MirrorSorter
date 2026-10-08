# -*- coding: utf-8 -*-
"""顶栏隐藏取证: 本地收集器 + MSX_PROBE=1 起壳开视频页, 收 _probe_out.jsonl。"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
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
    env = dict(os.environ, MSX_PROBE="1")
    log = open(os.path.join(HERE, "_probe_shell.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app_window.py"),
         "--e2e-open", "https://www.bilibili.com/video/BV1GJ411x7h7"],
        cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        time.sleep(45)   # 8s e2e钩子 + 页面加载 + 探针 1s/4s/10s 三报
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        log.close()
    srv.shutdown()
    if not os.path.exists(OUT):
        print("NO DATA COLLECTED")
        return 1
    for line in io_read():
        d = json.loads(line)
        print("=== t+?s url=%s ownui=%s mf=%s" % (d.get("url", "")[:60], d.get("ownui"), d.get("mf")))
        for k in ("main", "header", "bar"):
            v = d.get(k)
            if v is None:
                print("  %-7s MISSING" % k)
            else:
                print("  %-7s disp=%s vis=%s rect=%s" % (k, v["display"], v["vis"], v["rect"]))
                for h in v["hiddenChain"][:4]:
                    print("        hidden-by: <%s id=%s cls=%s> %s" % (h["tag"], h["id"], h["cls"], h["why"]))
        lf = d.get("left")
        if lf:
            print("  firstLiHTML=%s" % lf.get("firstLiHTML"))
            print("  left-entry parent=%s disp=%s kids=%s" % (lf["parentCls"], lf["disp"],
                  json.dumps(lf["kids"], ensure_ascii=False)[:400]))
        print("  body.kids=%s" % json.dumps(d.get("kids"), ensure_ascii=False)[:400])
    return 0


def io_read():
    import io
    return io.open(OUT, encoding="utf-8").read().splitlines()


if __name__ == "__main__":
    sys.exit(main())
