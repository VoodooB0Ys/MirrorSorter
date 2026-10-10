# -*- coding: utf-8 -*-
"""临时探针: 档位(并发/速率) 与 -352 风控率的关系。用完即删。
每个档位独立进程跑(风控退避是进程内状态)。用法: _bench_gear.py <档位>"""
import collections
import os
import random
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
GEAR = sys.argv[1] if len(sys.argv) > 1 else "2"
TMP = tempfile.mkdtemp(prefix="msx_gear_")
shutil.copy2(r"C:\Users\sunsi\AppData\LocalLow\MirrorSorter\data.db",
             os.path.join(TMP, "data.db"))
os.environ["MIRRORSORTER_DATA_DIR"] = TMP
os.environ["MIRRORSORTER_NO_OPEN"] = "1"
import app  # noqa: E402

CODES = collections.Counter()
_orig = app._bili_acc_probe


def probe(mid):
    d, code = _orig(mid)
    CODES[code] += 1
    return d, code


app._bili_acc_probe = probe
app.sset("m_filter_speed", GEAR)
random.seed(777)
hashes = ["%08x" % random.getrandbits(32) for _ in range(25)]
cands = sorted({m for h in hashes for m in app.crc32_reverse_fast(h)})
todo = cands[:60]
conc, rate, _rc, _rr = app._filter_gear()
print("档位 %s: 并发 %d, 速率 %.1f/s ; 送 %d 个 mid 进队列" % (GEAR, conc, rate, len(todo)))

t0 = time.time()
app._am_enqueue(todo)
while time.time() - t0 < 120:
    if not app._am_warm_q and time.time() - t0 > 3:
        break
    time.sleep(0.5)
dt = time.time() - t0
n352 = CODES.get(-352, 0)
tot = sum(CODES.values())
print("耗时 %.1fs ; 请求 %d 次 ; 返回码 %s" % (dt, tot, dict(CODES)))
print("★ -352 风控占比: %.0f%%  ; 实际速率 %.1f/s" % (100.0 * n352 / max(1, tot), tot / max(0.1, dt)))
sys.stdout.flush()
os._exit(0)
