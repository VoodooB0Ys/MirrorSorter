# -*- coding: utf-8 -*-
"""临时探针 v2: 画像补图队列吞吐 —— 同时用「独立连接直接数表」和「_am_row_get」两种口径,
并把队列长度/闸门速率打出来, 区分"没写进去"还是"读不到"。用完即删。"""
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="msx_q2_")
DB = os.path.join(TMP, "data.db")
shutil.copy2(r"C:\Users\sunsi\AppData\LocalLow\MirrorSorter\data.db", DB)
os.environ["MIRRORSORTER_DATA_DIR"] = TMP
os.environ["MIRRORSORTER_NO_OPEN"] = "1"
import app  # noqa: E402


def raw_count(mids):
    """独立连接直接查表 —— 绕过 _am_row_get 的所有容错, 看真实落库情况。"""
    c = sqlite3.connect(DB, timeout=5)
    try:
        q = ",".join("?" * len(mids))
        n_am = c.execute("SELECT COUNT(*) FROM author_meta WHERE mid IN (%s)" % q,
                         tuple(mids)).fetchone()[0]
        n_ab = c.execute("SELECT COUNT(*) FROM absent_mid WHERE mid IN (%s)" % q,
                         tuple(mids)).fetchone()[0]
    finally:
        c.close()
    return n_am, n_ab


random.seed(20261010)
hashes = ["%08x" % random.getrandbits(32) for _ in range(30)]
GEAR = sys.argv[1] if len(sys.argv) > 1 else "2"
app.sset("m_filter_speed", GEAR)

t0 = time.time()
cands = sorted({m for h in hashes for m in app.crc32_reverse_fast(h)})
print("逆推 %.2fs, 候选 %d 个" % (time.time() - t0, len(cands)))
todo = [m for m in cands if app._am_row_get(m, fast=True) is None
        and not app._am_is_absent(m, fast=True)]
print("需联网补图 %d 个; 档位 %s → 并发 %d, 速率 %.1f/s"
      % (len(todo), GEAR, app._filter_gear()[0], app._filter_gear()[1]))

t0 = time.time()
app._am_enqueue(todo)
print("入队后: 队列 %d, 已见 %d, 闸门 rate=%.2f cap=%.1f"
      % (len(app._am_warm_q), len(app._am_warm_seen), app._am_gate.rate, app._am_gate.cap))

last = -1
for _ in range(90):
    na, nab = raw_count(todo)
    n = na + nab
    qlen = len(app._am_warm_q)
    if n != last or qlen == 0:
        print("  %5.1fs  落库 %d/%d (画像 %d, 不存在 %d)  队列 %d  闸门 rate=%.2f"
              % (time.time() - t0, n, len(todo), na, nab, qlen, app._am_gate.rate))
        last = n
    if n >= len(todo):
        break
    time.sleep(1.0)
print("结束: %.1fs, 落库 %d/%d, 队列残留 %d" % (time.time() - t0, last, len(todo),
                                            len(app._am_warm_q)))
sys.stdout.flush()
os._exit(0)
