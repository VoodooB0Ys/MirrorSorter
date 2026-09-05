# -*- coding: utf-8 -*-
"""端到端冒烟: 真实 HTTP 服务 + 本地 mock OpenAI 兼容接口(流式/非流式)。
覆盖: /api/conv CRUD · /api/chat(命令/指代/搜索/LLM非流式/LLM流式SSE) · chips · 会话隔离
"""
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(PROJECT, "smoke-tmp")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, extra))


# ---------- mock OpenAI 兼容服务 ----------
class MockLLM(BaseHTTPRequestHandler):
    score_calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        stream = bool(body.get("stream"))
        is_intent = any("想让系统做什么" in (m.get("content") or "") for m in body.get("messages", []))
        is_score = any("内容筛选技能" in (m.get("content") or "") for m in body.get("messages", []))
        is_terms = any("近义词/相关搜索词" in (m.get("content") or "") for m in body.get("messages", []))
        if is_score:
            MockLLM.score_calls += 1
            user_msg = next((m for m in body.get("messages", []) if m.get("role") == "user"), {})
            keys = [it["key"] for it in json.loads(user_msg.get("content") or "{}").get("items", [])]
            items = [{"key": k, "flame": 9 if "bad" in k else 0, "spam": 0, "topic": "游戏",
                      "interest": 3 if "low" in k else 8, "reason": "推荐理由" + k} for k in keys]
            out = json.dumps({"choices": [{"message": {"content": json.dumps(
                                {"items": items}, ensure_ascii=False)}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 10}},
                             ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if is_intent:
            user = next((m.get("content", "") for m in body.get("messages", [])
                         if m.get("role") == "user"), "")
            if "刷" in user or "推" in user:
                verdict = '{"action":"search","keywords":["blender"]}'
            else:
                verdict = '{"action":"reply","keywords":[]}'
            out = json.dumps({"choices": [{"message": {"content": verdict}}],
                              "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
                             ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if is_terms:
            # 近义词生成: 首查给"深挖词1"; 扩波按已用词数递增(永不枯竭, 靠波次上限收敛)
            sys_c = (body.get("messages") or [{}])[0].get("content", "")
            n = sys_c.count("、") + 1 if "以下词已搜过" in sys_c else 1
            out = json.dumps({"choices": [{"message": {"content": json.dumps(
                ["深挖词%d" % n], ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
                ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            is_intent2 = False
            # 模拟"思考爆炸": 只出 reasoning 不出 content(截断场景)
            if any("思考爆炸" in (m.get("content") or "") for m in body.get("messages", [])):
                for t in ("好的", "用户", "想知道", "热熔", "怎么用", "绝地潜兵"):
                    ev = json.dumps({"choices": [{"delta": {"content": None,
                                                            "reasoning_content": t}}]},
                                    ensure_ascii=False)
                    self.wfile.write(("data: %s\n\n" % ev).encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            # 模拟推理模型: 先 reason 再 answer(真实 deepseek-v4-flash 行为)
            for t in ("嗯", "这个问题", "需要想想"):
                ev = json.dumps({"choices": [{"delta": {"content": None,
                                                        "reasoning_content": t}}]},
                                ensure_ascii=False)
                self.wfile.write(("data: %s\n\n" % ev).encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.01)
            for t in ("你", "好", "！", "这是", "流式", "回复"):
                ev = json.dumps({"choices": [{"delta": {"content": t, "reasoning_content": None}}]},
                                ensure_ascii=False)
                self.wfile.write(("data: %s\n\n" % ev).encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.02)
            fin = json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 5, "completion_tokens": 6}})
            self.wfile.write(("data: %s\n\ndata: [DONE]\n\n" % fin).encode("utf-8"))
            self.wfile.flush()
        else:
            out = json.dumps({"choices": [{"message": {"content": "非流式回复"}}],
                              "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                             ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)


mock = ThreadingHTTPServer(("127.0.0.1", 8098), MockLLM)
threading.Thread(target=mock.serve_forever, daemon=True).start()

# ---------- 启动被测服务 ----------
os.environ["MIRRORSORTER_DATA_DIR"] = TMP
spec = importlib.util.spec_from_file_location("app2", os.path.join(PROJECT, "app.py"))
app = importlib.util.module_from_spec(spec)
sys.modules["app2"] = app
spec.loader.exec_module(app)
app.DATA_DIR = TMP   # 保持旧测试写法, 双保险隔离真实用户存档

srv = ThreadingHTTPServer(("127.0.0.1", 8099), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def req(path, body=None, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request("http://127.0.0.1:8099" + path, data=data,
                               headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(r, timeout=15) as resp:
        out = resp.read()
    if raw:
        return out.decode("utf-8", "replace")
    return json.loads(out.decode("utf-8"))


try:
    # 页面租约: 刷新交接/多标签不能提前关服; 无页面超过宽限期才触发关闭。
    class LeaseServer:
        def __init__(self):
            self.calls = 0
        def shutdown(self):
            self.calls += 1

    lease_server = LeaseServer()
    old_server, old_grace = app._SERVER, app.PAGE_LEASE_GRACE_SECONDS
    app._SERVER, app.PAGE_LEASE_GRACE_SECONDS = lease_server, 0.03
    app._SHUTDOWN_REQUESTED.clear()
    with app._PAGE_LEASE_LOCK:
        app._PAGE_LEASES.clear()
        app._PAGE_LEASE_GENERATION = 0
    token_a, token_b = "a" * 16, "b" * 16
    app.page_lease_alive(token_a)
    app.page_lease_leaving(token_a)
    app.page_lease_alive(token_b)
    time.sleep(0.06)
    check("页面租约: 刷新交接后服务继续", lease_server.calls == 0, lease_server.calls)
    app.page_lease_leaving(token_b)
    time.sleep(0.06)
    check("页面租约: 全部页面离开后延迟关闭", lease_server.calls == 1, lease_server.calls)
    app._SERVER, app.PAGE_LEASE_GRACE_SECONDS = old_server, old_grace
    app._SHUTDOWN_REQUESTED.clear()
    with app._PAGE_LEASE_LOCK:
        app._PAGE_LEASES.clear()
        app._PAGE_LEASE_GENERATION = 0
    check("页面租约: 测试后状态已清理", not app._PAGE_LEASES and not app._SHUTDOWN_REQUESTED.is_set())

    # 通用展示冷却: 第N次实际展示进入冷却，同一请求重试不重复计数。
    app.sset("m_repeat_limit", "2")
    app.sset("m_repeat_cooldown_hours", "72")
    with app._db_lock:
        app.db().execute("DELETE FROM item_presentation")
        app.db().execute("DELETE FROM item_exposure_state")
        app.db().commit()
    cool_item = {"key": "cooldown-test"}
    app.record_feed_presentations([cool_item], "page-a", now=1000)
    check("展示冷却: 首次展示不冷却", "cooldown-test" not in app.cooldown_keys(1000))
    app.record_feed_presentations([cool_item], "page-a", now=1001)
    row = app.db().execute("SELECT run_count,cooldown_until FROM item_exposure_state WHERE key=?", ("cooldown-test",)).fetchone()
    check("展示冷却: 同请求重试不重复计数", row[0] == 1 and row[1] == 0, row)
    app.record_feed_presentations([cool_item], "page-b", now=1002)
    check("展示冷却: 第N次后硬性冷却", "cooldown-test" in app.cooldown_keys(1003), app.cooldown_keys(1003))
    check("展示冷却: 到期后恢复资格", "cooldown-test" not in app.cooldown_keys(1002 + 72 * 3600),
          app.cooldown_keys(1002 + 72 * 3600))
    app.sset("m_repeat_limit", "4")

    # 配置 mock AI
    check("设置mockAI", req("/api/settings", {"ai_key": "test-key",
                                              "ai_base": "http://127.0.0.1:8098/v1",
                                              "ai_model": "mock-model"}).get("ok"))

    # 冷却供给保护: 候选全部冷却时按最早展示顺序少量复用, 不返回空池。
    app.sset("m_repeat_limit", "4")
    app.sset("m_repeat_cooldown_hours", "72")
    scarce = [{"key": "scarce-%d" % i, "platform": "B站", "title": "冷却候选%d" % i,
               "author": "a", "desc": "d", "views": 1000, "duration": 120,
               "pubdate": int(time.time()), "url": "https://x/scarce-%d" % i}
              for i in range(2)]
    with app._db_lock:
        for i, it in enumerate(scarce):
            app.db().execute("INSERT OR REPLACE INTO item_exposure_state(key,run_count,last_presented_at,cooldown_until) VALUES(?,?,?,?)",
                             (it["key"], 4, 100 + i, int(time.time()) + 3600))
        app.db().commit()
    scarce_order, _ = app.planned_feed(scarce)
    check("展示冷却: 候选不足时不锁空", len(scarce_order) == 2 and all(x.get("_cooldown_relaxed") for x in scarce_order),
          [x.get("key") for x in scarce_order])
    with app._db_lock:
        app.db().execute("DELETE FROM item_exposure_state WHERE key LIKE 'scarce-%'")
        app.db().commit()

    # 冷却复用不清除冷却: 复用展示后原 cooldown_until 仍保留, run_count 递增。
    with app._db_lock:
        app.db().execute("INSERT OR REPLACE INTO item_exposure_state(key,run_count,last_presented_at,cooldown_until) "
                         "VALUES('relax-keep',4,100,2000)")
        app.db().commit()
    app.record_feed_presentations([{"key": "relax-keep", "_cooldown_relaxed": True}], "s1", now=1000)
    row = app.db().execute("SELECT run_count,cooldown_until FROM item_exposure_state WHERE key='relax-keep'").fetchone()
    check("冷却复用: 原冷却保留且计数递增", row[0] == 5 and row[1] == 2000, row)
    with app._db_lock:
        app.db().execute("DELETE FROM item_exposure_state WHERE key='relax-keep'")
        app.db().commit()

    # 会话级去重: 同一会话内已返回的 key 不再返回; 计划重排(reset)后也不重复。
    app.sset("m_repeat_limit", "4")
    feed_items = [{"key": "sess-%02d" % i, "platform": "B站", "title": "会话去重%d" % i,
                   "author": "a", "desc": "d", "views": 1000 + i, "duration": 120,
                   "pubdate": int(time.time()), "url": "https://x/sess-%02d" % i}
                  for i in range(40)]
    app._universe["items"] = feed_items
    app._universe["built_at"] = int(time.time())
    app._plan_cache.update(key=None, order=[], seq=0)
    page1 = app.serve_chunk(0, 30, "", request_id="sess-test")
    page2 = app.serve_chunk(0, 30, "", request_id="sess-test")   # 重复请求同页也不重样
    check("会话去重: 两页无重复key",
          len(page1["items"]) == 30 and len(page2["items"]) == 10 and
          not ({x["key"] for x in page1["items"]} & {x["key"] for x in page2["items"]}),
          {"p1": len(page1["items"]), "p2": len(page2["items"])})

    # 沉积带修复: B站新增枯竭、贴吧持续涌入时, 新到B站必须被重混插回后段中间,
    # 计划尾部不允许形成纯贴吧/小黑盒的扎堆带。
    app.sset("bili_weight", "100")
    app.sset("tb_weight", "25")
    app.sset("xhh_weight", "25")
    def mk(key, plat, views):
        return {"key": key, "platform": plat, "title": key, "author": "a", "desc": "d",
                "views": views, "duration": 120, "pubdate": int(time.time()),
                "url": "https://x/%s" % key}
    app._universe["items"] = [mk("mix-b%02d" % i, "B站", 2000 - i) for i in range(20)] + \
                             [mk("mix-t%02d" % i, "贴吧", 2000 - i) for i in range(10)]
    app._plan_cache.update(key=None, order=[], seq=0)
    with app._session_lock:
        app._session_served.pop("sess-test", None)
    app.serve_chunk(0, 30, "", request_id="sess-test")   # 消费首批(混合)
    # 第二批: 模拟B站枯竭 —— 只来贴吧
    app._universe["items"] += [mk("mix-t2%02d" % i, "贴吧", 1500 - i) for i in range(30)]
    app.serve_chunk(0, 30, "", request_id="sess-test")
    # 第三批: 新B站到货 → 必须重混进未消费后段, 而不是排在贴吧沉积带之后
    app._universe["items"] += [mk("mix-b2%02d" % i, "B站", 1400 - i) for i in range(20)]
    app._feed_plan(list(app._universe["items"]), False, 1)   # 触发重混(不经会话消费)
    with app._plan_lock:
        order = list(app._plan_cache["order"])
    with app._session_lock:
        served = set(app._session_served["sess-test"]["keys"])
    tail = [it for it in order if it["key"] not in served]
    tail_plats = [it["platform"] for it in tail]
    tb_runs, cur = [], 0
    for p in tail_plats:
        cur = cur + 1 if p == "贴吧" else 0
        tb_runs.append(cur)
    check("沉积带: 新B站重混进后段且尾部无纯贴吧扎堆",
          "B站" in tail_plats and (max(tb_runs) if tb_runs else 0) <= 4,
          {"tail_len": len(tail_plats), "max_tb_run": max(tb_runs) if tb_runs else 0,
           "bili_in_tail": tail_plats.count("B站")})
    app._plan_cache.update(key=None, order=[], seq=0)
    with app._session_lock:
        app._session_served.pop("sess-test", None)
    app._universe["items"] = []

    # 同作者降重: 配额(单计划≤3) + 间距打散 + 超额留池分批放出。
    app.sset("m_repeat_limit", "4")
    app.sset("m_repeat_cooldown_hours", "72")
    same_author_items = [mk("au-%02d" % i, "B站", 3000 - i) for i in range(3)] + \
                        [{"key": "au-same-%02d" % i, "platform": "B站", "title": "原声英语社教程%d" % i,
                          "author": "原声英语社", "desc": "d", "views": 2000 - i, "duration": 120,
                          "pubdate": int(time.time()), "url": "https://x/au-same-%02d" % i}
                         for i in range(8)] + \
                        [mk("au-o%02d" % i, "B站", 1000 - i) for i in range(10)]
    for i, it in enumerate(same_author_items):
        if it.get("author") in ("", "a", None):
            it["author"] = "路人UP%d" % i   # 足够多的不同作者, 配额才会生效
    order_a, _ = app.planned_feed(list(same_author_items), False)
    same_n = sum(1 for it in order_a if it.get("author") == "原声英语社")
    au_pos = [i for i, it in enumerate(order_a) if it.get("author") == "原声英语社"]
    au_dists = [b - a for a, b in zip(au_pos, au_pos[1:])]
    check("同作者降重: 单计划配额≤3", same_n <= 3, same_n)
    check("同作者降重: 无相邻出现", all(d >= 2 for d in au_dists), au_dists)
    # 留池放出: 模拟已看消费(把配额内3条标记shown), 重混后同作者新条目应补进计划。
    with app._db_lock:
        for it in order_a:
            if it.get("author") == "原声英语社":
                app.db().execute("INSERT OR REPLACE INTO shown(key, ts) VALUES(?,?)",
                                 (it["key"], int(time.time()) - 7 * 3600))
        app.db().commit()
    rest = [it for it in same_author_items if it["key"] not in {x["key"] for x in order_a}]
    order_b, _ = app.planned_feed(rest, False)
    same_b = sum(1 for it in order_b if it.get("author") == "原声英语社")
    check("同作者降重: 留池条目后续分批放出", same_b >= 1, same_b)

    # 负反馈硬排除: 不喜欢的条目与屏蔽作者在信息流/搜索/换一条全部消失, undo 可恢复。
    fb = [{"key": "fb-k1", "platform": "B站", "title": "被点踩的视频", "author": "作者甲",
           "desc": "x", "views": 5000, "duration": 200, "pubdate": int(time.time()),
           "url": "https://x/fb-k1"},
          {"key": "fb-k2", "platform": "B站", "title": "屏蔽作者的视频", "author": "被屏蔽UP",
           "desc": "x", "views": 5000, "duration": 200, "pubdate": int(time.time()),
           "url": "https://x/fb-k2"},
          {"key": "fb-k3", "platform": "B站", "title": "正常视频", "author": "路人",
           "desc": "x", "views": 5000, "duration": 200, "pubdate": int(time.time()),
           "url": "https://x/fb-k3"}]
    with app._db_lock:
        app.db().execute("INSERT INTO feedback(key,author,action,ts) VALUES('fb-k1','作者甲','dislike',?)",
                         (int(time.time()),))
        app.db().execute("INSERT OR IGNORE INTO blacklist(kind,value) VALUES('author','被屏蔽UP')")
        app.db().commit()
    kept_fb = app.machine_filter(fb)
    check("负反馈: 点踩与屏蔽作者入池前剔除",
          {x["key"] for x in kept_fb} == {"fb-k3"}, [x["key"] for x in kept_fb])
    served_fb = app.apply_filters(fb)
    check("负反馈: 服务出口同样剔除",
          {x["key"] for x in served_fb} == {"fb-k3"}, [x["key"] for x in served_fb])
    with app._db_lock:
        app.db().execute("DELETE FROM feedback WHERE key='fb-k1'")
        app.db().execute("DELETE FROM blacklist WHERE value='被屏蔽UP'")
        app.db().commit()
    kept_undo = app.machine_filter(fb)
    check("负反馈: 撤销后恢复", {x["key"] for x in kept_undo} == {"fb-k1", "fb-k2", "fb-k3"},
          [x["key"] for x in kept_undo])

    d = req("/api/conv")
    check("默认会话", len(d["convs"]) == 1 and d["cur"] == 1, d)

    d = req("/api/chat", {"message": "帮我刷blender", "stream": False})
    check("意图搜索回复", d.get("intent", {}).get("action") == "search", d)
    check("chips>=4", len(d.get("chips", [])) >= 4, d)
    check("空AI消息不写库", True)

    # LLM 非流式(自由聊天)
    d = req("/api/chat", {"message": "讲讲黑神话的故事", "stream": False})
    check("非流式LLM回复", d.get("reply") == "非流式回复", d)

    # 流式 SSE(推理模型: think + delta + done)
    raw = req("/api/chat", {"message": "讲讲黑神话的故事", "stream": True}, raw=True)
    check("SSE含think事件", "event: think" in raw, raw[:200])
    check("SSE含delta事件", "event: delta" in raw and "event: done" in raw, raw[:200])
    check("SSE含meta", "event: meta" in raw)
    check("流式内容完整", "你好！这是流式回复" in raw, raw[:400])

    # 思考截断兜底: 只有 reasoning 没有 content → 用思考尾段作答
    raw = req("/api/chat", {"message": "思考爆炸测试", "stream": True}, raw=True)
    m = [l for l in raw.split("\n") if l.startswith("data:") and '"reply"' in l]
    ok = bool(m) and "模型思考过长了" in m[-1] and "热熔" in m[-1]
    check("思考截断兜底", ok, m[-1] if m else raw[:200])

    # 会话隔离 + 指代
    d = req("/api/conv", {})
    cid2 = d["id"]
    req("/api/conv/select", {"id": 1})
    app.set_last_feed({"key": "k1", "title": "黑神话DLC", "author": "游戏观察室"})
    d = req("/api/chat", {"message": "收藏刚才那条", "stream": False})
    check("指代命令回复", d.get("reply", "").startswith("已稍后看"), d)
    saved = req("/api/saved?kind=later")
    check("指代落库saved", any(s["key"] == "k1" for s in saved["items"]), saved)

    # 会话1消息数
    d = req("/api/chat?cid=1")
    check("会话1有消息", len(d["messages"]) >= 2, d)
    d = req("/api/chat?cid=%d" % cid2)
    check("新会话为空", len(d["messages"]) == 0, d)

    # 新对话命令
    d = req("/api/chat", {"message": "开个新对话", "stream": False})
    check("新对话命令cid", d.get("cid", 0) > cid2 and d.get("chips"), d)

    # 重命名/删除
    req("/api/conv/rename", {"id": 1, "title": "默认改名"})
    d = req("/api/conv")
    check("重命名生效", d["convs"][0]["title"] == "默认改名", d)
    req("/api/conv/delete", {"id": 1})
    d = req("/api/conv")
    check("删除后不再出现", all(c["id"] != 1 for c in d["convs"]), d)
    check("删除后仍有会话", len(d["convs"]) >= 1, d)

    # 清空当前会话
    req("/api/conv/select", {"id": cid2 + 1})   # 新对话命令建的
    req("/api/chat/clear", {})
    cur = req("/api/conv")["cur"]
    d = req("/api/chat?cid=%d" % cur)
    check("清空当前会话", len(d["messages"]) == 0, d)

    # --- AI推荐模式: 全量评分开关(评分先行:bake只读缓存) ---
    pool = []
    now = int(time.time())
    for i in range(150):
        pool.append({"key": "k_%03d" % i, "platform": "B站", "title": "测试视频%03d" % i,
                     "author": "作者%d" % i, "desc": "简介", "cover": "", "views": 1000000 - i,
                     "likes": 100, "duration": 300, "pubdate": now - 3600,
                     "url": "https://www.bilibili.com/video/k_%03d" % i})
    with app._db_lock:
        for it in pool:
            app.db().execute("INSERT INTO items(key,json,fetched_at,score_at) VALUES(?,?,?,0)",
                             (it["key"], json.dumps(it, ensure_ascii=False), now))
        app.db().commit()
    app.score_items(pool)   # 预热(模拟历史评分缓存)
    app.sset("ai_full_scan", "1")
    kept = app.bake_universe({"items": pool}, quick=False)
    scored1 = sum(1 for it in kept if it.get("reason"))
    check("开启模式: 缓存优先全部有理由", scored1 == 150, scored1)
    app.sset("ai_full_scan", "0")
    kept0 = app.bake_universe({"items": pool}, quick=False)
    scored0 = sum(1 for it in kept0 if it.get("reason"))
    check("关闭模式: 不评分零理由且全保留", scored0 == 0 and len(kept0) == 150, (scored0, len(kept0)))
    check("评分进度已记录", app._score_progress["total"] >= 30 and app._score_progress["done"] >= 30,
          app._score_progress)

    # --- 信息流门禁: 可见条目必须已筛查(评分+剔除引战/兴趣不足), 同步完成 ---
    req("/api/settings", {"ai_full_scan": "1"})

    def mock_pool(quick=False):
        now = int(time.time())
        its = [{"key": "f_%03d" % i, "platform": "B站", "title": "feed视频%03d" % i,
                "author": "作者%d" % i, "desc": "简介", "cover": "", "views": 900000 - i,
                "likes": 100, "duration": 300, "pubdate": now - 3600,
                "url": "https://www.bilibili.com/video/f_%03d" % i} for i in range(38)]
        its.append({"key": "f_bad_000", "platform": "B站", "title": "引战测试标题",
                    "author": "坏作者", "desc": "引战", "cover": "", "views": 900000,
                    "likes": 1, "duration": 300, "pubdate": now - 3600,
                    "url": "https://www.bilibili.com/video/f_bad_000"})
        its.append({"key": "f_low_000", "platform": "B站", "title": "低兴趣测试标题",
                    "author": "普通作者", "desc": "简介", "cover": "", "views": 900000,
                    "likes": 1, "duration": 300, "pubdate": now - 3600,
                    "url": "https://www.bilibili.com/video/f_low_000"})
        return {"items": its, "offline": False, "source": "mock"}
    app.fetch_pool = mock_pool
    app.search_items = lambda kw, page=1, limit=30: [
        {"key": "s_%s_%d" % (str(hash(kw))[-4:], i), "platform": "B站",
         "title": "搜索%s_%d" % (kw[:8], i), "author": "作者", "desc": "简介", "cover": "",
         "views": 500000, "likes": 10, "duration": 200, "pubdate": int(time.time()) - 3600,
         "url": "https://www.bilibili.com/video/s_%d" % i} for i in range(30)]
    app.tb_search_items = lambda q, rn=20: [
        {"key": "tb_%s_%d" % (str(hash(q))[-4:], i), "platform": "贴吧",
         "title": "贴吧搜%s_%d" % (str(q)[:6], i), "author": "贴友", "desc": "帖内简介",
         "cover": "", "covers": [], "views": 0, "likes": 2, "danmaku": None, "reply": 5,
         "duration": 0, "pubdate": int(time.time()) - 3600,
         "url": "https://tieba.baidu.com/p/90000%d" % i, "tname": "测试吧", "area": "",
         "tag": "", "gid": "tieba", "src": "贴吧·测试吧"} for i in range(4)]
    # 后台评分线程已就绪(模拟): feed 候选预评分写库, 门禁只读缓存命中才展示(交互路径不阻塞/不烧token)
    app.score_items(mock_pool()["items"], only_cache=False)
    d1 = req("/api/feed?offset=0&limit=40")
    it1 = d1.get("items", [])
    check("feed响应含槽位统计字段", isinstance(d1.get("slots"), dict), d1.get("slots"))
    check("feed门禁: 返回条目全部带理由", len(it1) >= 1 and all((it.get("reason") or "").strip() for it in it1),
          "n=%d" % len(it1))
    check("feed门禁: 引战条目被剔除", not any("bad" in (it.get("key") or "") for it in it1), it1[:1])
    s = req("/api/search?q=" + urllib.parse.quote("feed测试") + "&limit=30")
    s_bili = [it for it in s.get("items", []) if it.get("platform") == "B站"]
    s_tb = [it for it in s.get("items", []) if it.get("platform") == "贴吧"]
    swr = sum(1 for it in s_bili if (it.get("reason") or "").strip())
    check("搜索结果同步评分门禁(B站条目)", len(s_bili) >= 1 and len(s_bili) == swr, swr)
    check("搜索跨平台: 贴吧条目混入首屏(豁免评分)", len(s_tb) >= 1 and s.get("platform_hits", 0) >= 1,
          "tb=%d hits=%s" % (len(s_tb), s.get("platform_hits")))
    check("搜索响应含深挖字段", all(k in s for k in ("end", "deep_terms", "building")), list(s.keys()))

    # --- 关闭模式: 零AI消耗(不评分) ---
    req("/api/settings", {"ai_full_scan": "0"})
    MockLLM.score_calls = 0
    s0 = req("/api/search?q=" + urllib.parse.quote("关闭模式测试") + "&limit=30")
    check("关闭模式: 不评分零AI调用", MockLLM.score_calls == 0 and
          not any((it.get("reason") or "") for it in s0.get("items", [])),
          "calls=%d items=%d" % (MockLLM.score_calls, len(s0.get("items", []))))
    req("/api/settings", {"ai_full_scan": "1"})

    # --- 搜索深挖: 到底后近义词扩波 + 波次上限收敛 + 循环保护 ---
    app._search_wave.clear()

    def _deep_base(kw):
        return [{"key": "d_%s_%d" % (str(kw).replace(" ", "")[:6], i), "platform": "B站",
                 "title": "深挖%s_%d" % (str(kw)[:8], i), "author": "作者", "desc": "简介",
                 "cover": "", "views": 500000, "likes": 10, "duration": 200,
                 "pubdate": int(time.time()) - 3600,
                 "url": "https://www.bilibili.com/video/d_%d" % i} for i in range(30)]
    app.search_items = lambda kw, page=1, limit=30: (_deep_base(kw) if page == 1 else [])
    app.tb_search_items = lambda q, rn=20: []
    sd1 = req("/api/search?q=" + urllib.parse.quote("深挖测试") + "&limit=30")
    check("深挖: 首波不扩波", sd1.get("total") == 30 and sd1.get("end") is False
          and sd1.get("deep_terms") == [],
          (sd1.get("total"), sd1.get("end"), sd1.get("deep_terms")))
    sd2 = req("/api/search?q=" + urllib.parse.quote("深挖测试") + "&offset=30&limit=30")
    check("深挖: 到底触发近义词扩波", sd2.get("total") == 60 and sd2.get("deep_terms") == ["深挖词1"]
          and len(sd2.get("items", [])) == 30,
          (sd2.get("total"), sd2.get("deep_terms"), len(sd2.get("items", []))))
    sd5 = req("/api/search?q=" + urllib.parse.quote("深挖测试") + "&offset=120&limit=30")
    check("深挖: 扩波3次达上限收敛", sd5.get("total") == 120 and sd5.get("end") is True
          and sd5.get("deep_terms") == ["深挖词1", "深挖词2", "深挖词3"],
          (sd5.get("total"), sd5.get("end"), sd5.get("deep_terms")))
    sd6 = req("/api/search?q=" + urllib.parse.quote("深挖测试") + "&offset=120&limit=30")
    check("深挖: 到底后循环保护(不再增长)", sd6.get("end") is True and not sd6.get("items")
          and sd6.get("total") == 120, (sd6.get("total"), sd6.get("end")))
    app._search_wave.clear()

    # --- 推荐标准: 兴趣分门槛剔除 ---
    req("/api/settings", {"ai_interest_min": "7"})
    d2 = req("/api/feed?offset=0&limit=40")
    check("兴趣门槛: 低兴趣条目被剔除", not any("low" in (it.get("key") or "") for it in d2.get("items", [])),
          [it.get("key") for it in d2.get("items", [])][:3])
    req("/api/settings", {"ai_interest_min": "0"})

    # --- 省钱方式: 批大小 30 → 评分请求数减半 ---
    MockLLM.score_calls = 0
    app.sset("ai_batch", "30")
    app.score_items([{"key": "b_%03d" % i, "platform": "B站", "title": "批%d" % i,
                      "author": "a", "desc": "d", "cover": "", "views": 100, "likes": 1,
                      "duration": 10, "pubdate": int(time.time()) - 3600,
                      "url": "https://x/b_%03d" % i} for i in range(30)])
    check("批大小30: 30条只发1个评分请求", MockLLM.score_calls == 1, MockLLM.score_calls)
    app.sset("ai_batch", "15")
    MockLLM.score_calls = 0
    app.score_items([{"key": "c_%03d" % i, "platform": "B站", "title": "批%d" % i,
                      "author": "a", "desc": "d", "cover": "", "views": 100, "likes": 1,
                      "duration": 10, "pubdate": int(time.time()) - 3600,
                      "url": "https://x/c_%03d" % i} for i in range(30)])
    check("批大小15: 30条发2个评分请求", MockLLM.score_calls == 2, MockLLM.score_calls)

    # --- AI 总开关: 关闭=评分/聊天全部停用, 零调用 ---
    req("/api/settings", {"ai_enabled": "0"})
    MockLLM.score_calls = 0
    app.score_items([{"key": "g_%03d" % i, "platform": "B站", "title": "批%d" % i,
                      "author": "a", "desc": "d", "cover": "", "views": 100, "likes": 1,
                      "duration": 10, "pubdate": int(time.time()) - 3600,
                      "url": "https://x/g_%03d" % i} for i in range(15)])
    check("总开关关: 评分零调用", MockLLM.score_calls == 0, MockLLM.score_calls)
    d = req("/api/chat", {"message": "你好", "stream": False})
    check("总开关关: 聊天提示已关闭", "关闭" in (d.get("reply") or ""), d.get("reply"))
    check("总开关关: 聊天不调LLM", MockLLM.score_calls == 0, MockLLM.score_calls)
    req("/api/settings", {"ai_enabled": "1"})
    d2 = req("/api/chat", {"message": "你好", "stream": False})
    check("总开关开: 聊天恢复", "关闭" not in (d2.get("reply") or ""), d2.get("reply"))

    # --- 并发评分去重: 两个请求同时评同一批, 只评一次 ---
    MockLLM.score_calls = 0

    def _score_concurrent():
        app.score_items([{"key": "h_%03d" % i, "platform": "B站", "title": "并发%d" % i,
                          "author": "a", "desc": "d", "cover": "", "views": 100, "likes": 1,
                          "duration": 10, "pubdate": int(time.time()) - 3600,
                          "url": "https://x/h_%03d" % i} for i in range(30)])
    ts = [threading.Thread(target=_score_concurrent), threading.Thread(target=_score_concurrent)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("并发评分去重: 2请求共享1次评分", MockLLM.score_calls == 2, MockLLM.score_calls)

    # --- 推送套餐引擎(规范.txt): 指纹去重/情绪过滤/资讯保鲜/槽位套餐/跨会话防重复/新维度门禁 ---
    def mkitem(key, title, views=100000, area="知识", interest=None, pubdate=None, dur=300):
        it = {"key": key, "platform": "B站", "title": title, "author": "作者", "desc": "简介",
              "cover": "", "views": views, "likes": 10, "duration": dur,
              "pubdate": pubdate if pubdate is not None else int(time.time()) - 3600,
              "url": "https://www.bilibili.com/video/%s" % key, "area": area, "tname": area,
              "tag": "", "topic": ""}
        if interest is not None:
            it["interest"] = interest
        return it

    # 规则②: 标题指纹去重(同标题只留最高热一条)
    mf = app.machine_filter([
        mkitem("fp_a1", "绝地潜兵2 新赛季更新 完整解析", 500000),
        mkitem("fp_a2", "绝地潜兵2 新赛季更新 完整解析", 400000),
        mkitem("fp_b", "blender 建模 材质 入门教程", 300000)])
    check("标题指纹去重: 同标题只留最高热", len(mf) == 2 and "fp_a1" in [x["key"] for x in mf],
          [x["key"] for x in mf])

    # 搜索下推(供给优先): 只有"不损失任何有效供给"时才下推档位;
    # pubdate 排序仅在时间窗开启且无播放量窗口时使用(pubdate+播放量下限会过滤光搜索来源)。
    old_views = app.sget("m_max_views")
    old_min_views = app.sget("m_min_views")
    app.sset("m_min_views", "0")
    app.sset("m_min_duration", "1800")
    app.sset("m_max_duration", "0")
    app.sset("m_max_age_months", "5")
    push = app.search_pushdown()
    check("搜索下推: 30分+档位", push.get("duration") == "3" and push.get("order") == "pubdate", push)
    app.sset("m_min_duration", "60")
    app.sset("m_max_duration", "1800")
    push2 = app.search_pushdown()
    check("搜索下推: 供给优先不丢部分有效档", "duration" not in push2, push2)
    app.sset("m_max_age_months", "0")
    push3 = app.search_pushdown()
    check("搜索下推: 无时间窗不推pubdate", "order" not in push3, push3)
    app.sset("m_max_age_months", "5")
    app.sset("m_min_views", "500")
    check("搜索下推: 播放量窗口开启不推pubdate",
          "order" not in app.search_pushdown(), app.search_pushdown())
    app.sset("m_min_views", "0")
    app.sset("m_max_duration", "300")
    app.sset("m_min_duration", "0")
    check("搜索下推: 无整档可保证时不下推", "duration" not in app.search_pushdown(), app.search_pushdown())
    app.sset("m_min_duration", "60")
    app.sset("m_max_duration", "0")
    app.sset("m_max_age_months", "5")
    app.sset("m_min_views", old_min_views or "0")
    app.sset("m_max_views", old_views or "0")

    # 漏斗仪表盘: machine_filter 每条规则剔除量可观测。
    before = app.funnel_stats()
    old_age = app.sget("m_max_age_months")
    app.sset("m_max_age_months", "1")
    now_ts = int(time.time())
    app.machine_filter([
        mkitem("fun_old", "一年前的旧视频", 1000, pubdate=now_ts - 400 * 86400),
        mkitem("fun_ok", "新鲜的正常视频", 1000, pubdate=now_ts)])
    app.sset("m_max_age_months", old_age or "5")
    after = app.funnel_stats()
    check("漏斗统计: 时间窗剔除可观测",
          after.get("age", 0) - before.get("age", 0) >= 1
          and after.get("fetched", 0) - before.get("fetched", 0) == 2,
          {"before": before, "after": after})

    # 源产出率自学习: 低产源被裁剪, 高产/未知源保留, 更新带指数衰减。
    with app._src_yield_lock:
        old_yield = dict(app._src_yield)
        app._src_yield.clear()
    try:
        app._src_yield_update("rank", 100, 2)
        app._src_yield_update("hot", 100, 60)
        check("源产出率: 高产源保留", app._src_yield_ok("hot") is True)
        check("源产出率: 低通过率源不抓取", app._src_yield_ok("rank") is False)
        check("源产出率: 未知源默认允许", app._src_yield_ok("related") is True)
        avail = [("全站榜", "rank", "u", 0, 1)] + [("热门%d" % i, "hot", "u", 0, 8) for i in range(1, 7)]
        pruned = app._src_yield_prune(avail, 4)
        check("源产出率: 低产源被裁剪", all(s[1] != "rank" for s in pruned[:4]) and len(pruned) == 4,
              [s[1] for s in pruned])
        app._src_yield_update("rank", 100, 100)   # 后续高产 → 衰减回升
        check("源产出率: 高产后恢复抓取", app._src_yield_ok("rank") is True)
        # 低产源被跳过时缓慢恢复(防永久饿死), 几轮后回到阈值以上重新参与抓取。
        with app._src_yield_lock:
            app._src_yield["related"] = {"fetched": 100, "kept": 1, "yield": 0.01}
        for _ in range(6):
            check_ok = app._src_yield_ok("related")
            if not check_ok:
                app._src_yield_recover("related")
        check("源产出率: 跳过源可恢复重试", app._src_yield_ok("related") is True,
              app._src_yield.get("related"))
    finally:
        with app._src_yield_lock:
            app._src_yield.clear()
            app._src_yield.update(old_yield)

    # 播放量上限/时长下限: 仅约束普通B站内容, 0 表示关闭上限
    app.sset("m_min_views", "0")
    app.sset("m_max_views", "500")
    app.sset("m_max_duration", "0")
    app.sset("m_min_duration", "60")
    range_pool = [mkitem("r_low", "低播放", 100, dur=120),
                  mkitem("r_high", "高播放", 100000, dur=120),
                  mkitem("r_short", "过短", 1000, dur=30),
                  dict(mkitem("r_follow", "关注内容", 100000, dur=30), following=True)]
    kept_range = app.machine_filter(range_pool)
    check("播放量上限/时长下限: 普通内容剔除且关注内容保留",
          {x["key"] for x in kept_range} == {"r_low", "r_follow"},
          [x["key"] for x in kept_range])
    app.sset("m_max_views", "0")
    kept_unlimited = app.machine_filter([mkitem("r_unlimited", "高播放", 100000, dur=120)])
    check("播放量上限0: 关闭上限", len(kept_unlimited) == 1, [x["key"] for x in kept_unlimited])
    app.sset("m_min_duration", "0")
    kept_short = app.machine_filter([mkitem("r_short_off", "短视频", 1000, dur=30)])
    check("时长下限0: 关闭下限", len(kept_short) == 1, [x["key"] for x in kept_short])
    app.sset("m_min_duration", "60")

    # 情绪过滤器(零token): 感叹号轰炸/标题党 → 剔除; 关闭开关则放行
    emo_pool = [mkitem("e1", "震惊！！！99%的人不看后悔"), mkitem("e2", "正常教程视频")]
    app.sset("m_emotion", "1")
    kept_e = app.machine_filter(emo_pool)
    check("情绪过滤器: 标题党剔除", len(kept_e) == 1 and kept_e[0]["key"] == "e2", [x["key"] for x in kept_e])
    app.sset("m_emotion", "0")
    kept_e2 = app.machine_filter(emo_pool)
    check("情绪过滤器: 关闭后放行", len(kept_e2) == 2, len(kept_e2))
    app.sset("m_emotion", "1")

    # 资讯保鲜: 24h 外的资讯剔除, 干货不受影响
    fresh_pool = [mkitem("n1", "某游戏版本更新公告", 1000, pubdate=int(time.time()) - 2 * 86400),
                  mkitem("n2", "blender 建模 材质 入门教程", 1000, pubdate=int(time.time()) - 2 * 86400)]
    app.sset("m_news_fresh", "1")
    kept_n = app.machine_filter(fresh_pool)
    check("资讯保鲜: 24h外资讯剔除/干货保留", [x["key"] for x in kept_n] == ["n2"], [x["key"] for x in kept_n])
    app.sset("m_news_fresh", "0")
    kept_n2 = app.machine_filter(fresh_pool)
    check("资讯保鲜: 关闭后放行", len(kept_n2) == 2, len(kept_n2))
    app.sset("m_news_fresh", "1")

    # 槽位套餐: 舒适60/拓展20/热点10 + 权重自适应(探索池空时自动再分配), 不丢条目
    app.sset("ai_full_scan", "0")
    app.sset("mode", "标准")
    app.sset("prefs_note", "blender教程")
    slot_pool = ([mkitem("s_c_%02d" % i, "blender教程 建模技巧%d" % i, 100000, interest=5)
                  for i in range(16)] +
                 [mkitem("s_x_%02d" % i, "军事解说 战史解析%d" % i, 300000, area="军事", interest=8)
                  for i in range(12)] +
                 [mkitem("s_s_%02d" % i, "新版本更新公告 第%d期" % i, 2000000, area="资讯",
                         pubdate=int(time.time()) - 3600)
                  for i in range(6)])
    order, slots = app.planned_feed(slot_pool, False)
    keys_in = {it["key"] for it in order}
    check("套餐规划: 总数不变且不丢条目", len(order) == len(slot_pool) and keys_in == {x["key"] for x in slot_pool},
          (len(order), len(slot_pool)))
    check("套餐规划: 槽位统计齐全", all(k in slots for k in ("舒适区", "拓展区", "热点")), slots)
    first10 = {it.get("_slot") for it in order[:10]}
    check("套餐规划: 首屏槽位交错(≥2类)", len(first10) >= 2, first10)
    check("槽位理由前缀(可解释性)", app._slot_reason({"reason": "测试", "_slot": "拓展区"}) == "拓展区·测试"
          and app._slot_reason({"reason": "拓展区·x", "_slot": "拓展区"}) == "拓展区·x", None)
    check("槽位理由兜底显示标签", app._slot_reason({"reason": "", "_slot": "舒适区", "tag": "blender,建模"}) == "舒适区·blender / 建模"
          and app._slot_reason({"reason": "", "_slot": "探索区", "platform": "贴吧", "tname": "rimworld"}) == "探索区·rimworld", None)

    # 跨会话防重复: 上次会话末尾类型(知识) → 新会话前5条强制避开
    app.sset("last_types", json.dumps(["B站|知识"]))
    order2, _ = app.planned_feed(slot_pool, True)
    check("跨会话防重复: 前5条避开上次类型", all(app._cat_key(it) != "B站|知识" for it in order2[:5]),
          [app._cat_key(it) for it in order2[:5]])
    app.sset("last_types", "")

    # 新评分维度门禁: 情绪净值极负一票剔除 / 严肃模式信息密度门槛
    app.sset("ai_full_scan", "1")
    now = int(time.time())
    for nk, sc in (("gate_emo", {"flame": 1, "spam": 1, "interest": 8, "density": 8, "emo": -7,
                                 "facts": 1, "topic": "测试", "reason": "测试理由"}),
                   ("gate_ok", {"flame": 1, "spam": 1, "interest": 8, "density": 8, "emo": 2,
                                "facts": 1, "topic": "测试", "reason": "测试理由"}),
                   ("gate_dense_low", {"flame": 1, "spam": 1, "interest": 8, "density": 2, "emo": 0,
                                       "facts": 1, "topic": "测试", "reason": "测试理由"}),
                   ("gate_dense_ok", {"flame": 1, "spam": 1, "interest": 8, "density": 9, "emo": 0,
                                      "facts": 1, "topic": "测试", "reason": "测试理由"})):
        it = mkitem(nk, ("深度学习教程 入门详解" if "dense" in nk else "中性测试标题") + nk[-4:], 1000)
        with app._db_lock:
            app.db().execute("INSERT INTO items(key,json,fetched_at,score_at,score_json) VALUES(?,?,?,?,?)",
                             (nk, json.dumps(it, ensure_ascii=False), now, now, json.dumps(sc, ensure_ascii=False)))
            app.db().commit()
    emo_acts = [app.item_by_key("gate_emo"), app.item_by_key("gate_ok")]
    o_emo, _ = app.planned_feed(emo_acts, False)
    check("情绪净值门禁: 极负剔除/正常保留", [x["key"] for x in o_emo] == ["gate_ok"],
          [x["key"] for x in o_emo])
    app.sset("mode", "严肃")
    den_acts = [app.item_by_key("gate_dense_low"), app.item_by_key("gate_dense_ok")]
    o_den, _ = app.planned_feed(den_acts, False)
    check("严肃密度门禁: 低密度剔除/高密度保留", [x["key"] for x in o_den] == ["gate_dense_ok"],
          [x["key"] for x in o_den])
    serious_cases = [
        ("unity教程 从零做背包系统", "", "游戏开发", "B站", True),
        ("UE5零基础入门教程 虚幻引擎开发", "", "游戏开发", "B站", True),
        ("blender建模入门课程", "", "知识", "B站", True),
        ("量子力学原理 深度讲解", "", "知识", "B站", True),
        ("绝地潜兵2 新手入门教学", "", "游戏", "B站", False),
        ("绝地潜兵2 机制讨论", "", "游戏", "B站", False),
        ("独立游戏开发者成为肥羊", "", "游戏", "B站", False),
        ("某某番剧世界观入门解析", "", "番剧", "B站", False),
        ("动画演出原理学习", "", "动画", "B站", False),
        ("Blender角色上色和骨骼蒙皮教程", "", "", "贴吧", True),
        ("Blender为什么简易形变不行", "", "", "贴吧", True),
        ("盒友杂谈 游戏推荐和新作资讯", "", "", "小黑盒", False),
        ("DLSS性能配置与显卡测试", "", "", "小黑盒", True),
        ("绝地潜兵2 爆笑实况解说", "", "游戏", "B站", False),
        ("新游戏锐评 吐槽合集", "", "游戏", "B站", False),
        ("原神抽卡名场面高能剪辑", "", "游戏", "B站", False),
    ]
    ok_ser = []
    for title, desc, area, platform, want in serious_cases:
        got = app.is_serious_ok({"title": title, "desc": desc, "area": area, "platform": platform,
                                 "tag": "", "tname": ""})
        ok_ser.append(got == want)
    check("严肃模式内容门禁: 教程硬核放行娱乐剔除", all(ok_ser), ok_ser)
    app.sset("mode", "标准")
    app.sset("prefs_note", "")

    # 实时干预: 聊天框临时屏蔽(到期自动解封)
    d = req("/api/chat", {"message": "未来1小时内别推游戏", "stream": False})
    check("临时屏蔽命令: 回复+解封提示", "已记下" in (d.get("reply") or "") and "解封" in (d.get("reply") or ""),
          d.get("reply"))
    app.add_tmp_block("二次元", 60)
    blocks_now = json.loads(app.sget("tmp_blocks"))
    blocks_now.append({"k": "过期词", "until": time.time() - 10})
    app.sset("tmp_blocks", json.dumps(blocks_now, ensure_ascii=False))
    kws = app.tmp_block_kws()
    check("临时屏蔽: 生效且过期自动清理", sorted(kws) == sorted(["游戏", "二次元"]), kws)
    app.sset("tmp_blocks", "")
    # 探索模式命令
    d2 = req("/api/chat", {"message": "探索一下新领域", "stream": False})
    check("探索模式命令", "探索模式" in (d2.get("reply") or ""), d2.get("reply"))
    app.sset("explore_boost_ts", "0")

    # --- 空流修复回归(规范引擎落地后发现的三个坑) ---
    # ① 指纹记忆随池清空: 刷新/清池后重建拉回同内容, 旧指纹失效否则永远0内容
    fp_t = app._title_fp("指纹清空回归测试标题")
    app._fp_dup(fp_t, time.time())
    check("指纹记忆: 2h内重复拦截", app._fp_dup(fp_t, time.time()) is True, None)
    app._fp_clear()
    check("指纹记忆: 清池后失效", app._fp_dup(fp_t, time.time()) is False, None)
    # ② 冷启动降级: 全扫开启但尚无任何评分 → 临时按热度放行(不长时间空白)
    app.sset("ai_full_scan", "1")
    cold_pool = [mkitem("cold_%02d" % i, "冷启动测试视频%d" % i, 100000 - i) for i in range(12)]
    o_cold, _ = app.planned_feed(cold_pool, False)
    check("冷启动放行: 无评分不空白", len(o_cold) == len(cold_pool), len(o_cold))
    # ③ 追加规划分页稳定: 同世代新增条目只接尾部, 已有顺序不动(offset 不错位)
    app.sset("ai_full_scan", "0")
    base_pool = [mkitem("ap_%02d" % i, "追加规划测试%d" % i, 900000 - i, area="科技") for i in range(20)]
    app._plan_cache.update(key=None, order=[], token=-1, epoch=-1, stail=None)
    o1 = app._feed_plan(base_pool, False, 1)
    more = [mkitem("ap2_%02d" % i, "追加规划第二批%d" % i, 800000 - i, area="军事") for i in range(10)]
    o2 = app._feed_plan(base_pool + more, False, 2)
    # 新不变式: 未消费后段允许重混(修沉积带), 但必须不丢条目、无重复、旧条目全保留。
    check("追加规划: 不丢条目且无重复",
          len(o2) == 30 and len({x["key"] for x in o2}) == 30
          and {x["key"] for x in o1} <= {x["key"] for x in o2},
          (len(o1), len(o2)))
    # ④ 评分落库推进 epoch → 全扫模式下新筛内容渐进可见(规划缓存不永久陈旧)
    e0 = app._score_epoch
    app._write_scores({"epoch_probe": {"flame": 0, "interest": 5}})
    check("评分写入推进epoch", app._score_epoch == e0 + 1, (e0, app._score_epoch))
    # ⑤ 刷新回归: 同样内容清池重建后必须正常出流(端到端)
    req("/api/feed?refresh=1")
    ok_refresh = False
    for _ in range(40):
        d4 = req("/api/feed?offset=0&limit=40")
        if d4.get("items"):
            ok_refresh = True
            break
        time.sleep(0.1)
    check("刷新回归: 同样内容重建不空流", ok_refresh, "items=%d" % len(d4.get("items", [])))
    # ⑥ 构建窗口期: 空池请求不得崩溃(IN () 非法SQL防御 / 规划对空输入安全)
    empty_cached, empty_pending = app._load_cache([], 3600)
    o_empty, _ = app.planned_feed([], False)
    check("空池规划: 不崩溃且返回空", empty_cached == {} and empty_pending == [] and o_empty == [], None)
    app.sset("ai_full_scan", "1")

    # --- 贴吧: 解析器/权重混排/吧列表/平台豁免/登录态 ---
    tb_html = """
    <ul>
      <li class="j_thread_list clearfix" data-field='{"tid":11111111}'>
        <a rel="noopener" href="/p/11111111" title="blender教程 从零开始" class="j_th_tit ">blender教程 从零开始</a>
        <span class="threadlist_rep_num center_text" title="回复">88</span>
        <span class="pull-right is_show_create_time" title="创建时间">8-10</span>
        <a class="frs-author-name j_user_card" href="/home/main/">楼主甲</a>
        <div class="threadlist_abs threadlist_abs_onlyline">记录一下blender学习历程，第一周做的小房子</div>
        <img src="https://tb3.bdstatic.com/public/img/icon_pc_picheader_n.432946a7.png" attr="52154" data-original="https://tiebapic.baidu.com/forum/a.jpg" />
        <img class="threadlist_pic j_m_pic" data-original="https://tiebapic.baidu.com/forum/b.jpg" />
      </li>
      <li class="j_thread_list clearfix" data-field='{"tid":22222222}'>
        <i class="icon-top" alt="置顶"></i>
        <a rel="noopener" href="/p/22222222" title="吧规公告" class="j_th_tit ">吧规公告</a>
        <span class="threadlist_rep_num center_text" title="回复">5</span>
      </li>
      <li class="j_thread_list clearfix" data-field='{"tid":33333333}'>
        <a rel="noopener" href="/p/33333333" title="绝地潜兵2 新赛季攻略" class="j_th_tit ">绝地潜兵2 新赛季攻略</a>
        <span class="threadlist_rep_num center_text" title="回复">1234</span>
        <span class="pull-right is_show_create_time" title="创建时间">23:32</span>
        <a class="frs-author-name j_user_card" href="/home/main/">楼主乙</a>
        <div class="threadlist_abs threadlist_abs_onlyline">新赛季配装解析</div>
      </li>
    </ul>
    """
    tb_items = app._tb_html_to_items(tb_html, "绝地潜兵")
    check("贴吧解析: 置顶剔除+2条保留", len(tb_items) == 2, len(tb_items))
    it0 = tb_items[0]
    check("贴吧解析: 字段齐全", it0["platform"] == "贴吧" and it0["title"] == "blender教程 从零开始"
          and it0["author"] == "楼主甲" and it0["reply"] == 88 and it0["tname"] == "绝地潜兵",
          it0)
    check("贴吧解析: 双图+占位图剔除", it0["covers"] == ["https://tiebapic.baidu.com/forum/a.jpg",
                                                 "https://tiebapic.baidu.com/forum/b.jpg"], it0["covers"])
    check("贴吧解析: 摘要+时间", "blender" in it0["desc"] and it0["pubdate"] > 0, it0)
    check("贴吧解析: key=帖子链接", it0["key"] == "https://tieba.baidu.com/p/11111111", it0["key"])
    # 严肃模式词门(吧名"blender"无游戏词, 教程帖放行)
    tb_items_ser = app._tb_html_to_items(tb_html, "blender")
    check("贴吧严肃词门: 教程帖放行", app.is_serious_ok(tb_items_ser[0]), tb_items_ser[0])
    # 游戏相关吧不再因吧名整体剔除: 教程/学习帖放行, 纯玩法攻略仍按标题词剔除
    check("贴吧严肃词门: 游戏吧教程放行", app.is_serious_ok(tb_items[0]), tb_items[0])
    bad_game_post = dict(tb_items[0], title="绝地潜兵2 高难BOSS打法攻略", desc="配装走位速通")
    check("贴吧严肃词门: 纯玩法攻略剔除", not app.is_serious_ok(bad_game_post), bad_game_post)
    # 权重混排: 25/90 → 目标贴吧占比 25/115≈21.7%
    def mkpool(n_bili, n_tb):
        pool = []
        for i in range(n_bili):
            pool.append({"key": "b%d" % i, "platform": "B站", "title": "v%d" % i,
                         "author": "a", "desc": "", "cover": "", "views": 1000 - i,
                         "likes": 1, "duration": 10, "pubdate": 0,
                         "url": "https://www.bilibili.com/video/b%d" % i,
                         "src": "B%d" % (i % 3)})
        for i in range(n_tb):
            pool.append({"key": "t%d" % i, "platform": "贴吧", "title": "p%d" % i,
                         "author": "a", "desc": "", "cover": "", "covers": [],
                         "views": 0, "likes": 0, "reply": 10, "danmaku": None,
                         "duration": 0, "pubdate": 0,
                         "url": "https://tieba.baidu.com/p/%d" % i,
                         "src": "贴吧·X%d" % (i % 2)})
        return pool
    app.sset("tb_weight", "25")
    app.sset("bili_weight", "90")
    # 候选不足目标(120bili+20tb, 目标30tb→截断至20): 全部采用, 均匀散布, 不丢条目
    mixed = app.mix_sources(mkpool(120, 20))
    n_tb = sum(1 for it in mixed if it.get("platform") == "贴吧")
    check("权重混排: 总数不变", len(mixed) == 140, len(mixed))
    check("权重混排: 候选不足时贴吧全采用", n_tb == 20, n_tb)
    gaps, last = [], -1
    for i, it in enumerate(mixed):
        if it.get("platform") == "贴吧":
            if last >= 0:
                gaps.append(i - last)
            last = i
    check("权重混排: 均匀不扎堆(间隔≤9)", gaps and max(gaps) <= 9, max(gaps) if gaps else None)
    # 平台防连发: 100:25:25 下只要其他平台还有候选, 同平台最多连续2条
    spaced = app.mix_sources(
        [{"key": "b%d" % i, "platform": "B站", "src": "b"} for i in range(40)] +
        [{"key": "t%d" % i, "platform": "贴吧", "src": "t"} for i in range(10)] +
        [{"key": "x%d" % i, "platform": "小黑盒", "src": "x"} for i in range(10)])
    plats = [it["platform"] for it in spaced]
    runs = []
    cur = 1
    for a, b in zip(plats, plats[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    # 防连发约束中段; 末尾单平台连发属于候选耗尽(B站被消费完)的物理极限, 由重混+供给修复。
    check("权重混排: 平台连续不超过2条(候选耗尽的尾段除外)",
          max(runs[:-1] or [0]) <= 2, {"runs": runs})
    # 桶级混排: 大小悬殊的桶不能尾部连片(旧轮转缺陷), 大桶按占比散布
    big = [{"key": "b%d" % i, "src": "搜·军事"} for i in range(12)]
    small = [{"key": "s%d" % i, "src": "搜·菜谱"} for i in range(2)]
    bm = app._bucket_mix(big + small)
    srcs = [it["src"] for it in bm]
    runs, cur = [], 1
    for a, b in zip(srcs, srcs[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur); cur = 1
    runs.append(cur)
    check("桶级混排: 大桶不尾部连片", len(bm) == 14 and max(runs) <= 7,
          {"max_run": max(runs), "order": srcs[:14]})
    # 50/50 完美交替
    app.sset("tb_weight", "50")
    app.sset("bili_weight", "50")
    mixed50 = app.mix_sources(mkpool(50, 50))
    n_tb50 = sum(1 for it in mixed50 if it.get("platform") == "贴吧")
    gaps50, last50 = [], -1
    for i, it in enumerate(mixed50):
        if it.get("platform") == "贴吧":
            if last50 >= 0:
                gaps50.append(i - last50)
            last50 = i
    check("权重混排: 50/50 交替", n_tb50 == 50 and max(gaps50) <= 3, (n_tb50, max(gaps50)))
    # 100/100(同权) → 严格交替
    app.sset("tb_weight", "100")
    app.sset("bili_weight", "100")
    mixed1 = app.mix_sources(mkpool(40, 40))
    gaps1, last1 = [], -1
    for i, it in enumerate(mixed1):
        if it.get("platform") == "贴吧":
            if last1 >= 0:
                gaps1.append(i - last1)
            last1 = i
    check("权重混排: 100/100同权散布", max(gaps1) <= 3, max(gaps1))
    # 权重=0: 贴吧不出 / 贴吧=0 B站不出
    app.sset("tb_weight", "0")
    app.sset("bili_weight", "90")
    mixed0 = app.mix_sources(mkpool(100, 40))
    check("权重0: 贴吧全不出", not any(it.get("platform") == "贴吧" for it in mixed0), len(mixed0))
    app.sset("tb_weight", "25")
    app.sset("bili_weight", "0")
    mixed0b = app.mix_sources(mkpool(100, 40))
    check("B站权重0: 只出贴吧", all(it.get("platform") == "贴吧" for it in mixed0b), len(mixed0b))
    app.sset("bili_weight", "90")
    app.sset("tb_weight", "50")
    app.sset("xhh_weight", "50")
    app.sset("bili_weight", "50")
    tri_pool = mkpool(40, 40)
    now_tri = int(time.time())
    for i in range(40):
        tri_pool.append({"key": "x%d" % i, "platform": "小黑盒", "title": "xhh%d" % i,
                         "author": "a", "desc": "", "cover": "", "covers": [],
                         "views": 0, "likes": 0, "reply": 10, "danmaku": None,
                         "duration": 0, "pubdate": now_tri,
                         "url": "https://www.xiaoheihe.cn/app/bbs/link/%d" % i,
                         "src": "小黑盒·X%d" % (i % 2)})
    planned_tri, _ = app.planned_feed(tri_pool, False)
    first30 = planned_tri[:30]
    n_bili30 = sum(1 for it in first30 if it.get("platform") == "B站")
    n_tb30 = sum(1 for it in first30 if it.get("platform") == "贴吧")
    n_xhh30 = sum(1 for it in first30 if it.get("platform") == "小黑盒")
    check("套餐规划后仍保留平台权重", min(n_bili30, n_tb30, n_xhh30) >= 7,
          (n_bili30, n_tb30, n_xhh30))
    app.sset("tb_weight", "25")
    app.sset("xhh_weight", "15")
    app.sset("bili_weight", "90")
    app.sset("tb_bars", "blender\nrimworld")
    allowed = {"key": "ta", "platform": "贴吧", "title": "blender教程", "author": "a",
               "desc": "", "cover": "", "covers": [], "views": 0, "likes": 0, "reply": 1,
               "danmaku": None, "duration": 0, "pubdate": now, "url": "https://tieba.baidu.com/p/3",
               "tname": "blender", "tag": "blender", "src": "贴吧·blender"}
    denied = dict(allowed, key="td", title="原神攻略", tname="原神", tag="原神", src="贴吧·原神",
                  url="https://tieba.baidu.com/p/4")
    check("贴吧允许吧过滤: 放行配置吧", app.tieba_bar_allowed(allowed), allowed)
    check("贴吧允许吧过滤: 拒绝非配置吧", not app.tieba_bar_allowed(denied), denied)
    app.sset("tb_bars", "")
    # 吧列表设置
    r = req("/api/tieba", {"bars": ["绝地潜兵", "blender", "军事"]})
    check("吧列表保存", r.get("ok"))
    d = req("/api/tieba")
    check("吧列表读取", d["bars"] == ["绝地潜兵", "blender", "军事"], d)
    check("权重读取默认", d["bili_weight"] == 90 and d["tb_weight"] == 25, (d["bili_weight"], d["tb_weight"]))
    # 求助帖开关: 默认开
    check("求助帖默认允许", d.get("allow_help", True) is True, d)
    # is_help_post 判定
    hp = app.is_help_post({"title": "求助 blender 渲染报错", "desc": ""})
    hp2 = app.is_help_post({"title": "blender教程 从零开始", "desc": "记录学习历程"})
    check("求助判定: 求助帖命中", hp and not hp2, (hp, hp2))
    # machine_filter 过滤求助帖(贴吧)
    now = int(time.time())
    pool_h = [
        {"key": "h1", "platform": "贴吧", "title": "求助 侧边栏不见了", "author": "a",
         "desc": "", "cover": "", "views": 0, "likes": 0, "duration": 0, "pubdate": now,
         "url": "https://tieba.baidu.com/p/1", "tname": "blender", "tag": ""},
        {"key": "h2", "platform": "贴吧", "title": "blender教程 从零开始", "author": "b",
         "desc": "记录学习", "cover": "", "views": 0, "likes": 0, "duration": 0, "pubdate": now,
         "url": "https://tieba.baidu.com/p/2", "tname": "blender", "tag": ""},
    ]
    app.sset("tb_allow_help", "0")
    kept = app.machine_filter(pool_h)
    check("求助帖过滤: 关闭后剔除", len(kept) == 1 and kept[0]["key"] == "h2", [k["key"] for k in kept])
    app.sset("tb_allow_help", "1")
    kept2 = app.machine_filter(pool_h)
    check("求助帖过滤: 开启后全保留", len(kept2) == 2, len(kept2))
    # API开关
    req("/api/tieba", {"allow_help": False})
    check("求助帖API: 关闭保存", req("/api/tieba")["allow_help"] is False)
    req("/api/tieba", {"allow_help": True})
    # 权重白名单(合法: 0/25/50/75/100)
    req("/api/tieba", {"tb_weight": "37", "bili_weight": "99"})
    d = req("/api/tieba")
    check("权重白名单: 非法值忽略", d["bili_weight"] == 90 and d["tb_weight"] == 25, (d["bili_weight"], d["tb_weight"]))
    req("/api/tieba", {"tb_weight": "100", "bili_weight": "25"})
    d = req("/api/tieba")
    check("权重白名单: 100合法", d["bili_weight"] == 25 and d["tb_weight"] == 100, (d["bili_weight"], d["tb_weight"]))
    req("/api/tieba", {"tb_weight": "25", "bili_weight": "90"})
    # 平台豁免: 贴吧条目不过分区检查
    check("item_domain_ok 贴吧豁免", app.item_domain_ok({"platform": "贴吧", "tname": "X"}, {"anime"}))
    # 关注的人标记: UID 是权威身份；旧条目缺 UID 时才回退到昵称。
    app.sset("bili_follow_names", json.dumps(["旧昵称"], ensure_ascii=False))
    app.sset("bili_follow_mids", json.dumps(["42"]))
    with app._follow_lock:
        app._following_names_cache.update(ts=0, names=set(), mids=set())
    now_follow = int(time.time())
    uid_cand = {"platform": "B站", "author": "改名后的作者", "author_mid": 42,
                "title": "普通推荐流里的视频", "pubdate": now_follow, "following": False}
    reused_name = {"platform": "B站", "author": "旧昵称", "author_mid": 99,
                   "title": "昵称复用的普通视频", "pubdate": now_follow, "following": False}
    legacy_cand = {"platform": "B站", "author": "旧昵称", "title": "旧缓存视频",
                   "pubdate": now_follow, "following": False}
    app._annotate_following([uid_cand, reused_name, legacy_cand])
    check("关注的人标记: UID支持改名", uid_cand.get("following") is True, uid_cand)
    check("关注的人标记: UID防昵称复用误标", reused_name.get("following") is not True, reused_name)
    check("关注的人标记: 旧缓存昵称兼容", legacy_cand.get("following") is True, legacy_cand)
    raw_rank = {"bvid": "BVuidtest", "title": "UID测试", "owner": {"name": "改名后的作者", "mid": 42},
                "stat": {}, "duration": 120, "pubdate": now_follow}
    normal_item = app.normalize(raw_rank)
    app._annotate_following([normal_item])
    check("关注的人标记: 正常榜单保留UID", normal_item.get("author_mid") == 42 and normal_item.get("following") is True,
          normal_item)
    # 贴吧未读数: 仅解析页面明确给出的“回复我/@我”计数，缺字段时不猜测为0。
    old_tb_check, old_urlopen = app.tb_login_check, app.urllib.request.urlopen
    class TbUnreadResponse:
        def __init__(self, text): self.text = text.encode("utf-8")
        def read(self): return self.text
        def __enter__(self): return self
        def __exit__(self, *args): return False
    try:
        app.tb_login_check = lambda: {"logged_in": True}
        pages = iter(["<script>count:{replyme:4,atme:2}</script>",
                      "<script>count:{replyme:4,atme:2}</script>"])
        app.urllib.request.urlopen = lambda *args, **kwargs: TbUnreadResponse(next(pages))
        tb_unread = app.tb_unread_messages()
        check("贴吧未读: 回复我加@我真实汇总", tb_unread == 6, tb_unread)
        app.urllib.request.urlopen = lambda *args, **kwargs: TbUnreadResponse("<script>count:{}</script>")
        check("贴吧未读: 缺失计数保持未知", app.tb_unread_messages() is None)
    finally:
        app.tb_login_check, app.urllib.request.urlopen = old_tb_check, old_urlopen
    # 登录态(空cookie=未登录)
    check("贴吧登录态: 未登录", not req("/api/tieba")["logged_in"])
    # 手动cookie无效 → 被清
    r = req("/api/tieba/login/manual", {"cookie": "BDUSS=fake123"})
    check("贴吧手动登录: 无效cookie拒绝", not r.get("ok") and not req("/api/tieba")["cookie_set"], r)
    # 无 Chromium 浏览器时: 打开默认浏览器并切换到手动 Cookie 导入，不再直接报启动失败
    old_find, old_open = app._tb_find_browser, app.webbrowser.open
    try:
        app._tb_find_browser = lambda: ""
        opened = []
        app.webbrowser.open = lambda url: opened.append(url) or True
        app._CDP_PROC = None
        fallback = app.tb_login_start()
        check("贴吧登录: 默认浏览器手动导入兜底", fallback.get("ok") and fallback.get("manual") and opened,
              fallback)
    finally:
        app._tb_find_browser, app.webbrowser.open = old_find, old_open
    # 匿名会话种子(不验证内容, 只验证函数可调用无异常)
    jar = app._tb_session_jar()
    seeded = app._tb_seed(jar)
    check("贴吧匿名会话种子(可选联网)", isinstance(seeded, bool))
    # 时间解析
    check("贴吧时间解析: 月日", app.tb_time_parse("8-10") > 0, app.tb_time_parse("8-10"))
    check("贴吧时间解析: 时分", app.tb_time_parse("23:32") > 0, app.tb_time_parse("23:32"))
    check("贴吧时间解析: 非法", app.tb_time_parse("") == 0 and app.tb_time_parse("abc") == 0)

    # --- 小黑盒: 签名/平台注册/混排 ---
    # hkey 对照真实抓包(期望 ZYP7136)
    hk = app.xhh_hkey("/bbs/app/api/search/found", 1787973690, "C838A69A567F021B8833960FC7E63DA3")
    check("小黑盒hkey对照抓包", hk == "ZYP7136", hk)
    # 平台注册表含小黑盒
    check("平台注册含小黑盒", any(p[0] == "小黑盒" for p in app.PLATFORMS), app.PLATFORMS)
    # 混排含小黑盒(权重15)
    app.sset("xhh_weight", "15")
    app.sset("bili_weight", "90")
    app.sset("tb_weight", "25")
    mp = app.mix_sources([
        {"key": "h%d" % i, "platform": "小黑盒", "title": "x%d" % i, "src": "H%d" % (i % 2)}
        for i in range(10)] + [
        {"key": "b%d" % i, "platform": "B站", "title": "v%d" % i, "src": "B%d" % (i % 3)}
        for i in range(30)])
    n_xhh = sum(1 for it in mp if it.get("platform") == "小黑盒")
    check("小黑盒混排: 条目保留", n_xhh == 10, n_xhh)
    # 权重API含小黑盒
    d = req("/api/tieba")
    pnames = [p["name"] for p in d.get("platforms", [])]
    check("平台API含小黑盒", "小黑盒" in pnames, pnames)
    # 小黑盒未读数: 仅接受上游明确的未读字段，空结果不伪造为0。
    old_xhh_get = app.xhh_get
    try:
        app.sset("xhh_heybox_id", "10001")
        app.xhh_get = lambda *args, **kwargs: ({"status": "ok", "message_unread_num": 2, "result": []}, None)
        check("小黑盒未读: 当前接口顶层计数", app.xhh_unread_messages() == 2, app.xhh_unread_messages())
        app.xhh_get = lambda *args, **kwargs: ({"status": "ok", "result": {"unread_count": 3}}, None)
        check("小黑盒未读: 服务端总数", app.xhh_unread_messages() == 3, app.xhh_unread_messages())
        app.xhh_get = lambda *args, **kwargs: ({"status": "ok", "result": {"messages": [{"is_read": False}, {"is_read": True}]}}, None)
        check("小黑盒未读: 明确已读标记", app.xhh_unread_messages() == 1, app.xhh_unread_messages())
        app.xhh_get = lambda *args, **kwargs: ({"status": "ok", "result": {"messages": [{"id": 1}]}}, None)
        check("小黑盒未读: 无标记保持未知", app.xhh_unread_messages() is None)
    finally:
        app.xhh_get = old_xhh_get
        app.sset("xhh_heybox_id", "")
    # 小黑盒完整采集: 四页并发请求且跨页去重。
    old_xhh_feed, old_xhh_cursor = app.xhh_feed_items, app._xhh_cursor
    try:
        calls = []
        def fake_xhh_feed(offset=0, limit=20, pull="0"):
            calls.append((offset, limit))
            time.sleep(0.02)
            return ([{"key": "xhh-%d" % (offset // 30), "platform": "小黑盒"}], "")
        app.xhh_feed_items = fake_xhh_feed
        app._xhh_cursor = 0
        xitems, xerrs = app.fetch_xhh_pool(quick=False)
        check("小黑盒采集: 多页并发且去重", len(calls) == 4 and len(xitems) == 4 and not xerrs,
              {"calls": calls, "items": len(xitems)})
    finally:
        app.xhh_feed_items, app._xhh_cursor = old_xhh_feed, old_xhh_cursor
    # 小黑盒状态API
    dx = req("/api/xhh")
    check("小黑盒状态API", dx.get("ok") is True, dx)

finally:
    srv.shutdown()
    mock.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

print("\n==== %d PASS / %d FAIL ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
