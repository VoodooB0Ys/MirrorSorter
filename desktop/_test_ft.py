# -*- coding: utf-8 -*-
"""端到端验证: app.py 的「快速文本分析」是否真的通了（模型加载 / 三条链路 / HTTP 接口）。"""
import io
import os
import sys
import json
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  [OK] " if cond else "  [!!] ") + msg)
    if not cond:
        FAIL.append(msg)


print("=" * 76)
print("【1】模型状态")
print("=" * 76)
print("  模型路径 : %s" % app._ft_path())
t0 = time.time()
st = app.ft_status()
print("  状态     : %s" % json.dumps(st, ensure_ascii=False))
chk(os.path.isfile(app._ft_path()), "模型文件存在")
chk(st["has_module"], "ftrt 运行时可用")
chk(st["size_mb"] > 50, "模型体积 > 50MB (实际 %.1fMB)" % st["size_mb"])

print()
print("=" * 76)
print("【2】开关矩阵：总开关 / 作用范围 必须相互独立")
print("=" * 76)
TEXTS = [
    "这游戏画面真不错",                    # ok
    "赢麻了",                              # yy
    "你就是个废物，滚出去",                 # flame
    "加微信领取免费课程，名额有限",           # spam
    "惊了！这个显卡居然只要999",             # clickbait
    "这优化卡得不行，根本没法玩",             # neg
]

app.sset("ft_on", "0")
app.sset("ft_video", "1")
app.sset("ft_dm", "1")
app.sset("ft_cf", "1")
app.sset("ft_thresh", "70")
app.sset("ft_labels", "flame,spam,clickbait,yy,neg")
app._FT["tried"] = False
app._FT["err"] = ""
app._FT_CACHE.clear()
app._FT_CACHE_SIG[0] = None

r = app.ft_scan(TEXTS, "video")
chk(r == [""] * 6, "总开关=0 时全部放行 → %s" % r)

app.sset("ft_on", "1")
t0 = time.time()
r = app.ft_scan(TEXTS, "video")
load_ms = (time.time() - t0) * 1000
print("  首次判定(含加载) %.0f ms" % load_ms)
print("  video 结果: %s" % r)
chk(r[0] == "", "正常文本放行")
chk(r[1] != "", "阴阳怪气命中 (%s)" % r[1])
chk(r[2] != "", "引战命中 (%s)" % r[2])
chk(r[3] != "", "广告命中 (%s)" % r[3])
chk(r[4] != "", "标题党命中 (%s)" % r[4])
chk(r[5] != "", "负面命中 (%s)" % r[5])

app.sset("ft_video", "0")
chk(app.ft_scan(TEXTS, "video") == [""] * 6, "关掉「信息筛选」后 video 全放行")
chk(app.ft_scan(TEXTS, "dm")[2] != "", "但「弹幕」不受影响, 仍命中")
chk(app.ft_scan(TEXTS, "cf")[2] != "", "「评论」也不受影响, 仍命中")
app.sset("ft_video", "1")

app.sset("ft_dm", "0")
chk(app.ft_scan(TEXTS, "dm") == [""] * 6, "关掉「弹幕」后 dm 全放行")
chk(app.ft_scan(TEXTS, "video")[2] != "", "「信息筛选」仍独立工作")
app.sset("ft_dm", "1")

print()
print("=" * 76)
print("【3】标签勾选 与 阈值 生效（★ 改配置必须让判定缓存失效）")
print("=" * 76)
app.sset("ft_labels", "spam")
r = app.ft_scan(TEXTS, "video")
print("  只勾 spam: %s" % r)
chk(r[2] == "" and r[3] != "", "只勾 spam 时, 引战放行 / 广告命中")
chk(r[1] == "" and r[4] == "", "阴阳怪气/标题党 也放行(未被勾选)")
app.sset("ft_labels", "flame")
r = app.ft_scan(TEXTS, "video")
print("  只勾 flame: %s" % r)
chk(r[2] != "" and r[3] == "", "切成只勾 flame 后立即生效(缓存已作废)")
app.sset("ft_labels", "flame,spam,clickbait,yy,neg")

app.sset("ft_thresh", "99.9")
r99 = app.ft_scan(TEXTS, "video")
app.sset("ft_thresh", "5")
r05 = app.ft_scan(TEXTS, "video")
app.sset("ft_thresh", "70")
print("  阈值99.9%%: %s" % r99)
print("  阈值   5%%: %s" % r05)
chk(sum(1 for x in r99 if x) <= sum(1 for x in r05 if x), "阈值越高命中越少")

print()
print("=" * 76)
print("【4】吞吐 与 缓存")
print("=" * 76)
app._FT_CACHE.clear()
batch = (TEXTS * 400)
t0 = time.perf_counter()
app.ft_scan(batch, "video")
dt = time.perf_counter() - t0
print("  冷缓存 %d 条: %.3f 秒 → %.0f 条/秒" % (len(batch), dt, len(batch) / dt))
app._FT_CACHE.clear()
mixed = [TEXTS[i % 6] + str(i) for i in range(2400)]
t0 = time.perf_counter()
app.ft_scan(mixed, "video")
dt = time.perf_counter() - t0
print("  不重复 %d 条: %.3f 秒 → %.0f 条/秒" % (len(mixed), dt, len(mixed) / dt))

print()
print("=" * 76)
print("【5】HTTP 接口 /api/textfilter")
print("=" * 76)
import socket
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
_free = _s.getsockname()[1]
_s.close()
app.PORTS = [_free]        # 避开已在跑的实例
srv, port = app.run_server(auto_open=False)
chk(srv is not None, "HTTP 服务启动 (端口 %s)" % port)
if srv is not None:
    import threading
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    time.sleep(0.4)
    base = "http://127.0.0.1:%d" % port

    def post(path, obj):
        req = urllib.request.Request(base + path,
                                     data=json.dumps(obj).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    j = post("/api/textfilter", {"kind": "dm", "texts": TEXTS})
    print("  POST /api/textfilter → %s" % json.dumps(j, ensure_ascii=False))
    chk(j.get("ok") is True, "接口返回 ok")
    chk(len(j.get("why", [])) == len(TEXTS), "返回长度与输入一致")
    chk(j["why"][2] != "", "弹幕链路命中引战")

    j2 = get("/api/textfilter/status")
    print("  GET /api/textfilter/status → %s" % json.dumps(j2, ensure_ascii=False)[:220])
    chk(j2.get("ok") is True, "状态接口可用")

    j3 = post("/api/machine", {"ft_on": "1", "ft_thresh": "70"})
    chk(j3 is not None, "/api/machine 可写 ft_* 设置")

    j4 = post("/api/textfilter/probe", {"text": "赢麻了，又是魂游"})
    print("  POST /api/textfilter/probe → %s" % json.dumps(j4, ensure_ascii=False))
    chk(j4.get("ok") is True, "「试一下」接口可用")
    chk(len(j4.get("top") or []) >= 4, "「试一下」返回各标签概率")

    app.shutdown(srv)

print()
print("=" * 76)
print("【6】设置页「试一下」: 单条文本的原始判定")
print("=" * 76)
app.sset("ft_on", "1")
app.sset("ft_labels", "flame,spam,clickbait,yy")
app.sset("ft_thresh", "70")
PROBE = [
    ("赢麻了，又是魂游", True),
    ("震惊！99%的人不知道这个秘密", True),
    ("你就是个废物，滚吧", True),
    ("这游戏画面真不错，推荐", False),
]
for txt, want_hit in PROBE:
    r = app.ft_probe(txt)
    print("  %-24s → %s" % (txt[:24], json.dumps(r, ensure_ascii=False)[:170]))
    chk(r.get("ok") is True, "「%s」能判定" % txt[:10])
    if r.get("ok"):
        chk(r.get("hit") == want_hit,
            "「%s」hit=%s (期望 %s)" % (txt[:10], r.get("hit"), want_hit))
        chk(len(r.get("top") or []) >= 4, "返回标签概率排名")

r = app.ft_probe("好")
print("  过短文本 → %s" % json.dumps(r, ensure_ascii=False))
chk(r.get("ok") is False, "过短文本被挡下(不浪费一次前向)")

app.sset("ft_thresh", "99")
r = app.ft_probe("这游戏优化太差了，卡成PPT")   # 实测 yy≈0.79, 落在 70~99 之间
print("  阈值 99%% / 长句 → %s" % json.dumps(r, ensure_ascii=False)[:170])
chk(r.get("ok") is True and r.get("hit") is False, "阈值拉满后不再拦(证明阈值真生效)")
app.sset("ft_thresh", "70")
r = app.ft_probe("这游戏优化太差了，卡成PPT")
chk(r.get("ok") is True and r.get("hit") is True, "阈值回到 70 又拦下(阈值双向生效)")
r = app.ft_probe("笑死")
chk(r.get("ok") is False and "太短" in (r.get("reason") or ""), "2 字文本按「太短」挡下")

app.sset("ft_thresh", "70")
app.sset("ft_labels", "spam")
r = app.ft_probe("赢麻了，又是魂游")
print("  只勾 spam → %s" % json.dumps(r, ensure_ascii=False)[:170])
chk(r.get("ok") is True and r.get("hit") is False and r.get("picked") is False,
    "未勾选的标签不参与拦截")

app.sset("ft_labels", "flame,spam,clickbait,yy")
app.sset("ft_on", "0")
r = app.ft_probe("赢麻了")
print("  总开关关闭 → %s" % json.dumps(r, ensure_ascii=False))
chk(r.get("ok") is False, "总开关关掉时不可判定")
app.sset("ft_on", "1")

print()
print("=" * 76)
if FAIL:
    print("失败 %d 项:" % len(FAIL))
    for f in FAIL:
        print("   - %s" % f)
else:
    print("全部通过")
print("=" * 76)
sys.stdout.flush()
os._exit(1 if FAIL else 0)
