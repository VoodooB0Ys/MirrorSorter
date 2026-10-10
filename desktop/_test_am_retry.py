# -*- coding: utf-8 -*-
"""画像补图链路的行为回归(v0.3.1.45dsk): 失败重入队 + 风控硬退避。

    python _test_am_retry.py

为什么单独一个脚本(而不是塞进 test_shell.py): 这段逻辑是**多线程 + 定时**的,
要真等线程跑几轮才有意义, 而 test_shell.py 的定位是"静态断言 + 纯函数, 跑得快"。
参照 _test_tabstrip_drag_gui.py 的做法: 把真实现拉起来跑, 但把网络整个换掉
(monkeypatch bili_author_meta), 于是不联网、不碰用户账号。

覆盖:
  ① 100% 失败时 mid 不再被丢弃 —— 会被反复重试(旧实现排空一次就永久丢失);
  ② 重试次数超上限后停止, 不会死循环;
  ③ 中途恢复成功 → 立刻停止重试并清空重试表;
  ④ 撞 -352 期间队列**原地等待**, 不 pop、不消耗(旧实现会把整队打光);
  ⑤ 一次成功即解除退避。
"""
import os
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="msx_amretry_")
shutil.copy2(r"C:\Users\sunsi\AppData\LocalLow\MirrorSorter\data.db",
             os.path.join(TMP, "data.db"))
os.environ["MIRRORSORTER_DATA_DIR"] = TMP
os.environ["MIRRORSORTER_NO_OPEN"] = "1"

import app  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s   %s" % (name, extra))


# 把网络与节流整个换掉: 不联网、跑得快
app._AM_RETRY_DELAY = 0.4
app._AM_RETRY_TICK = 0.3
app._am_gate = app._TokenBucket(0, 1)      # 不限速, 让 worker 全速跑
app._am_apply_gear = lambda: 2             # 固定并发 2, 不查库

CALLS = {"n": 0, "fail": True}


def fake_meta(mid, want_bio=True, want_lottery=True):
    CALLS["n"] += 1
    if CALLS["fail"]:
        return None
    return {"sign": "", "mystery": False, "lottery": False, "nodyn": False,
            "level": 6, "ts": int(time.time())}


app.bili_author_meta = fake_meta

MIDS = [str(700000000 + i) for i in range(8)]
MIDS_HASHES = ["%08x" % (0x10000000 + i * 7919) for i in range(12)]


def reset():
    with app._am_warm_lock:
        app._am_warm_q[:] = []
        app._am_warm_seen.clear()
        app._am_retry.clear()
        app._am_tries.clear()
    with app._dmrev_lock:
        app._dmrev_q[:] = []
        app._dmrev_seen.clear()
    app._am_risk["streak"] = 0
    app._am_risk["pause_until"] = 0.0
    app._am_risk["hits"] = 0
    CALLS["n"] = 0


print("[1] 100% 失败: mid 必须被反复重试, 不能一次排空就永久丢失")
reset()
CALLS["fail"] = True
app._am_enqueue(MIDS)
time.sleep(4.0)
first = CALLS["n"]
ok("失败后确实发生了重试(调用次数 > 首次入队数)", first > len(MIDS), "调用 %d 次" % first)
ok("重试表里还有未超上限的 mid", len(app._am_retry) > 0, "retry=%d" % len(app._am_retry))

print("[2] 重试次数封顶: 不会无限重试")
reset()
CALLS["fail"] = True
app._am_enqueue(MIDS)
time.sleep(14.0)
ok("调用次数被 _AM_RETRY_MAX 封住(每 mid ≤ 上限+1 次)",
   CALLS["n"] <= len(MIDS) * (app._AM_RETRY_MAX + 1), "调用 %d 次" % CALLS["n"])
ok("封顶后重试表清空(不再排队)", len(app._am_retry) == 0, "retry=%d" % len(app._am_retry))

print("[3] 中途恢复成功 → 立刻停止重试并清空重试表")
reset()
CALLS["fail"] = True
app._am_enqueue(MIDS)
time.sleep(1.2)
CALLS["fail"] = False
time.sleep(3.5)
n_after_ok = CALLS["n"]
time.sleep(2.0)
ok("恢复后重试停止(调用次数不再增长)", CALLS["n"] == n_after_ok,
   "%d -> %d" % (n_after_ok, CALLS["n"]))
ok("重试表已清空", len(app._am_retry) == 0, "retry=%d" % len(app._am_retry))

print("[4] 撞风控期间队列原地等待(不 pop、不消耗)")
reset()
CALLS["fail"] = True
app._am_risk["pause_until"] = time.time() + 3.0     # 手动进入退避窗口
app._am_enqueue(MIDS)
time.sleep(1.5)
ok("退避期间一次请求都没发", CALLS["n"] == 0, "调用 %d 次" % CALLS["n"])
ok("退避期间条目仍留在队列里(没被 pop 掉)",
   len(app._am_warm_q) + len(app._am_retry) > 0,
   "q=%d retry=%d" % (len(app._am_warm_q), len(app._am_retry)))
time.sleep(2.5)
ok("退避结束后恢复请求", CALLS["n"] > 0, "调用 %d 次" % CALLS["n"])

print("[5] 一次成功即解除退避")
reset()
app._am_note_risk(-352)
ok("-352 后处于暂停", app._am_paused())
app._am_note_ok()
ok("成功后解除暂停", not app._am_paused())

print("[6] 三个账号级规则全关 → 弹幕/评论侧零联网、连逆推都不做")
reset()


def boom(*a, **k):
    raise AssertionError("规则全关时不该发生任何联网/补图")


app.bili_author_meta = boom
r = app.bili_dm_flags(MIDS_HASHES, want_bio=False, want_lottery=False, want_empty=False)
ok("dmflags 直接返回空", r.get("flags") == {} and r.get("pending") == [])
ok("没有把候选塞进补图队列", not app._am_warm_q and not app._am_retry)
ok("连逆推队列都没动", not app._dmrev_q)
r2 = app.bili_author_flags(MIDS, want_bio=False, want_lottery=False, want_empty=False)
ok("评论侧同样直接返回空", r2.get("flags") == {} and r2.get("pending") == [])
ok("评论侧也没塞补图队列", not app._am_warm_q and not app._am_retry)

print()
print("通过 %d / 失败 %d" % (PASS, FAIL))
sys.stdout.flush()
os._exit(1 if FAIL else 0)
