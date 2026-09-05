# -*- coding: utf-8 -*-
"""
洞鉴虚明 · 基础版 (v0.2.6.2a)
=========================
本地运行的洞鉴虚明：读取 B站 内容 → 黑白名单/AI 分拣 → 推荐流 + 数据源状态 + AI 聊天。

运行:   python app.py
访问:   http://127.0.0.1:8000

特性:
  - 零第三方依赖(只用 Python 标准库)
  - SQLite 持久化: 反馈/黑白名单/历史/收藏/聊天/统计
  - AI 可选: 在左侧设置里填 API Key(OpenAI 兼容, 默认 DeepSeek)后,
    开启 引战/低质 评分过滤 + 推荐理由 + AI 聊天; 未配置时一切照常可用
  - 模式档位: 严格/标准/宽松(引战剔除阈值)
  - 只存「标题+作者+封面+热度+链接」元数据, 不搬运内容
"""
import base64
import collections
import hashlib
import html
import http.cookiejar
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTS = (8000, 8010, 8020)
PAGE_LEASE_GRACE_SECONDS = 3
_SERVER = None
_SHUTDOWN_REQUESTED = threading.Event()
_PAGE_LEASES = set()
_PAGE_LEASE_LOCK = threading.Lock()
_PAGE_LEASE_GENERATION = 0
CACHE_TTL = 300          # B站数据缓存 5 分钟
SCORE_TTL_DEFAULT = 86400        # AI 评分缓存 24 小时(可经 ai_cache_hours 设置 24/48)
AI_BATCH_DEFAULT = 15            # 每次 AI 评分的条目数(省钱法: 可设 30)
SPAM_CUT = 7             # 低质/营销分 ≥7 一律剔除
MODE_THRESH = {"严格": 4, "标准": 6, "宽松": 8, "严肃": 5}   # 引战分 ≥ 阈值即剔除(严肃=严格偏中)
DISCLAIMER_VERSION = "2026-09-03"

# ---------------- 设置导入导出 ----------------
# 运行状态/会话类键: 永不导出(导了也没意义, 还会把错误状态带过去)
_tieba_thread_cache = {}          # tid -> {"payload":..., "ts":...} 帖子首页缓存(10分钟, 防重复抓)
_TIEBA_THREAD_TTL = 600
_xhh_thread_cache = {}            # link_id -> {"payload":..., "ts":...} 小黑盒帖子缓存(10分钟)
_XHH_THREAD_TTL = 600
_IO_STATE_KEYS = {"fetch_error", "fetch_last", "last_feed", "last_types", "scorer_v",
                  "tb_cursor", "tb_error", "tb_last_fetch", "xhh_error", "xhh_last_fetch",
                  "xhh_qr_code", "xhh_qr_tokenid", "explore_boost_ts", "feedback_sig",
                  "tmp_blocks", "cur_conv", "disclaimer_version"}
# 登录凭证类键: 敏感, 仅在显式勾选时导出; 导入方可一键克隆各平台登录态
_IO_CRED_KEYS = {"bili_cookie", "tb_cookie", "tb_user", "tb_avatar", "xhh_cookie",
                 "xhh_heybox_id", "xhh_pkey", "xhh_user", "xhh_avatar",
                 "acct_nickname", "acct_profile", "bili_follow_mids", "bili_follow_names"}
# 允许导入的白名单(= 可导出的全集): 防止导入文件里塞进任意键污染 settings 表
_IO_IMPORTABLE = ({"mode", "ai_base", "ai_key", "ai_model", "ai_enabled", "ai_full_scan",
                   "ai_interest_min", "ai_batch", "ai_cache_hours", "price_in", "price_out",
                   "m_min_views", "m_max_views", "m_max_duration", "m_min_duration",
                   "m_max_age_months", "m_seen_hours", "m_repeat_limit",
                   "m_repeat_cooldown_hours", "m_dedup", "m_blocklist_sync", "m_emotion",
                   "m_news_fresh", "ser_max_age_months", "m_sources", "tb_allow_help",
                   "tb_bars", "xhh_comm", "xhh_topic", "bili_weight", "tb_weight",
                   "xhh_weight", "bili_follow_emph", "bili_follow_months",
                   "prefs_note", "dislike_note"} | _IO_CRED_KEYS) - _IO_STATE_KEYS


def settings_export_payload(include_credentials):
    """收集可导出的设置 + 黑名单(含登录凭证与否由调用方决定)。"""
    rows = db().execute("SELECT k,v FROM settings").fetchall()
    st = {k: v for k, v in rows
          if k in _IO_IMPORTABLE and (include_credentials or k not in _IO_CRED_KEYS)
          and v not in (None, "")}
    bl = {"authors": [r[0] for r in db().execute("SELECT value FROM blacklist WHERE kind='author'")],
          "keywords": [r[0] for r in db().execute("SELECT value FROM blacklist WHERE kind='keyword'")]}
    return {"app": "洞鉴虚明", "kind": "mirror-sorter-settings", "v": 1,
            "ts": int(time.time()), "settings": st, "blacklist": bl}

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
# 正式运行统一读用户存档目录; 测试/隔离开发可用 MIRRORSORTER_DATA_DIR 覆盖。
DATA_DIR = os.environ.get("MIRRORSORTER_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), "AppData", "LocalLow", "MirrorSorter")

# 数据目录迁移(旧路径 ~/信息分拣器 → 新路径 MirrorSorter): 保留设置/登录/历史/收藏
_DATA_OLD = os.path.join(os.path.expanduser("~"), "信息分拣器")
if os.path.exists(os.path.join(_DATA_OLD, "data.db")) and not os.path.exists(os.path.join(DATA_DIR, "data.db")):
    os.makedirs(DATA_DIR, exist_ok=True)
    for _f in ("data.db", "data.db-wal", "data.db-shm"):
        _s, _d = os.path.join(_DATA_OLD, _f), os.path.join(DATA_DIR, _f)
        if os.path.exists(_s) and not os.path.exists(_d):
            try:
                shutil.move(_s, _d)
            except Exception:
                pass
os.makedirs(DATA_DIR, exist_ok=True)

if getattr(sys, "frozen", False):   # 一次性迁移: exe旁旧库 → 用户目录
    _old = os.path.dirname(sys.executable)
    for _f in ("data.db", "data.db-wal", "data.db-shm"):
        _src, _dst = os.path.join(_old, _f), os.path.join(DATA_DIR, _f)
        if os.path.exists(_src) and not os.path.exists(_dst):
            try:
                shutil.move(_src, _dst)
            except Exception:
                pass


def web_path(name):
    base = getattr(sys, "_MEIPASS", APP_DIR)   # PyInstaller onefile 时资源在临时目录
    return os.path.join(base, "web", name)


# ---------------------------------------------------------------- 数据库

_db_lock = threading.RLock()   # RLock: 允许嵌套(锁内再调 sget 等); 用于多语句复合
_tls = threading.local()       # 每线程独立连接: 共享连接并发读写会竞态(InterfaceError/空值)


def db():
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = sqlite3.connect(os.path.join(DATA_DIR, "data.db"), check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")   # 并发写等待10s, 防 database is locked
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS items(key TEXT PRIMARY KEY, json TEXT, fetched_at INTEGER,
                                          score_json TEXT, score_at INTEGER);
        CREATE TABLE IF NOT EXISTS feedback(key TEXT, author TEXT, action TEXT, ts INTEGER);
        CREATE TABLE IF NOT EXISTS blacklist(kind TEXT, value TEXT, PRIMARY KEY(kind, value));
        CREATE TABLE IF NOT EXISTS saved(key TEXT, kind TEXT, ts INTEGER, PRIMARY KEY(key, kind));
        CREATE TABLE IF NOT EXISTS history(key TEXT, ts INTEGER, PRIMARY KEY(key));
        CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, ts INTEGER, cid INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS conv(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, ts INTEGER, updated INTEGER);
        CREATE TABLE IF NOT EXISTS stats(day TEXT PRIMARY KEY, in_tokens INTEGER, out_tokens INTEGER,
                                         calls INTEGER, received INTEGER, filtered INTEGER);
        CREATE TABLE IF NOT EXISTS ablock(name TEXT PRIMARY KEY, ts INTEGER);
        CREATE TABLE IF NOT EXISTS kw(kw TEXT PRIMARY KEY, last_used INTEGER, new_cnt INTEGER,
                                      ok_cnt INTEGER, gid TEXT);
        CREATE TABLE IF NOT EXISTS shown(key TEXT PRIMARY KEY, ts INTEGER);
        CREATE TABLE IF NOT EXISTS item_presentation(key TEXT, request_id TEXT, presented_at INTEGER,
                                                      PRIMARY KEY(key, request_id));
        CREATE TABLE IF NOT EXISTS item_exposure_state(key TEXT PRIMARY KEY, run_count INTEGER NOT NULL DEFAULT 0,
                                                       last_presented_at INTEGER NOT NULL DEFAULT 0,
                                                       cooldown_until INTEGER NOT NULL DEFAULT 0);
        """)
        try:   # 老库补 gid 列
            conn.execute("ALTER TABLE kw ADD COLUMN gid TEXT")
        except Exception:
            pass
        try:   # 老库补 cid 列(多会话); 旧消息归为默认会话
            conn.execute("ALTER TABLE chat ADD COLUMN cid INTEGER DEFAULT 0")
        except Exception:
            pass
        try:   # 分区源重构后清理旧内容池(一次性迁移)
            if conn.execute("SELECT v FROM settings WHERE k='feed_version'").fetchone() is None:
                conn.execute("DELETE FROM items")
                conn.execute("DELETE FROM shown")
                conn.execute("DELETE FROM kw")
                conn.execute("INSERT INTO settings(k,v) VALUES('feed_version','2')")
        except Exception:
            pass
        if conn.execute("SELECT COUNT(*) FROM conv").fetchone()[0] == 0:
            try:   # 多线程首建连接可能同时插入, 冲突忽略
                conn.execute("INSERT INTO conv(id,title,ts,updated) VALUES(1,'默认对话',?,?)",
                             (int(time.time()), int(time.time())))
            except Exception:
                pass
        # 老库消息(无cid)归入默认会话1
        conn.execute("UPDATE chat SET cid=1 WHERE cid=0")
        conn.commit()
        _tls.conn = conn
    return conn


DEFAULTS = {
    "ai_base": "https://api.deepseek.com/v1",
    "ai_key": "",
    "ai_model": "deepseek-chat",
    "ai_enabled": "1",        # AI 总开关: 0=全部AI功能停用(评分/聊天/添词都不烧钱), 1=按其它设置运行
    "mode": "标准",
    "prefs_note": "",
    "dislike_note": "",
    "price_in": "1.0",      # ¥/百万输入token
    "price_out": "2.0",     # ¥/百万输出token
    "m_min_views": "0",     # 机器筛选: 低于此播放量剔除(0=关闭)
    "m_max_views": "0",     # 机器筛选: 高于此播放量剔除(0=关闭)
    "m_max_duration": "0",  # 机器筛选: 时长超过此秒数剔除(0=关闭)
    "m_min_duration": "60", # 机器筛选: 时长低于此秒数剔除(默认1分钟)
    "m_dedup": "1",         # 机器筛选: 相似标题去重
    "m_blocklist_sync": "1",  # 机器筛选: 同步B站账号拉黑名单并屏蔽
    "m_max_age_months": "5",  # 机器筛选: 只显示最近N个月发布的内容(0=不限)
    "ser_max_age_months": "30",  # 严肃模式独立时间窗: 知识/教程内容历史长, 默认30个月(0=不限)
    "m_seen_hours": "6",      # 通用筛选: 已看信息N小时内不重复出现(0=不限)
    "m_cold_start_n": "10",   # 启动预缓存: 退出时缓存池首未展示条目N条, 下次打开直接展示(0=关闭)
    "m_repeat_limit": "4",    # 通用筛选: 同一信息累计展示N次后进入冷却
    "m_repeat_cooldown_hours": "72",  # 通用筛选: 冷却N小时后再显示
    "m_emotion": "1",         # 机器筛选: 情绪过滤器(感叹号轰炸/引战黑话/标题党句式, 零token)
    "m_news_fresh": "1",      # 机器筛选: 资讯/热点类只推送24小时内(干货/教程不受此限)
    "bili_follow_emph": "1",  # B站关注的人: 强调关注up发布的视频(入池+视觉强调)
    "bili_follow_months": "1",  # B站关注的人: 只强调最近N个月发布(0=不限, 滑条0-12)
    "ai_full_scan": "0",      # AI推荐模式: 1=所有候选都AI评分+推荐理由(费token), 0=只评热度前120条
    "ai_interest_min": "0",   # 推荐标准: 兴趣分门槛(0=不限, 5=一般, 7=严格)
    "ai_batch": "15",         # 省钱方式: 每批评分条数(15=稳, 30=省一半请求)
    "ai_cache_hours": "24",   # 省钱方式: 评分缓存时长(24h/48h)
    "tb_bars": "",            # 贴吧吧名列表(每行一个/逗号分隔), 空=不采集
    "tb_weight": "25",        # 贴吧平台权重%(0/25/50/75/90), 与 bili_weight 互比
    "bili_weight": "90",      # B站平台权重%(0/25/50/75/90)
    "tb_cookie": "",          # 贴吧登录凭证(BDUSS/STOKEN 等), 空=匿名会话
    "tb_user": "",            # 贴吧登录昵称(展示用)
    "tb_avatar": "",          # 贴吧头像URL(登录时从页面抓取)
    "tb_allow_help": "1",     # 贴吧: 允许求助帖(0=过滤"求助/求教/请问"类提问帖)
    "xhh_weight": "15",       # 小黑盒平台权重%(0/25/50/75/100)
    "xhh_cookie": "",         # 小黑盒设备tokenid(x_xhh_tokenid, 扫码登录后保存)
    "xhh_pkey": "",           # 小黑盒登录会话token(pkey, 网页版真实凭证)
    "xhh_heybox_id": "",      # 小黑盒用户ID(heybox_id/userid)
    "xhh_user": "",           # 小黑盒登录昵称
    "xhh_avatar": "",         # 小黑盒头像URL(登录时抓取)
    "xhh_comm": "",           # 小黑盒社区/板块筛选(逗号分隔, 空=全部)  >> 匹配帖子 topics
    "xhh_topic": "",          # 小黑盒话题筛选(逗号分隔, 空=全部)      >> 匹配帖子 hashtags
    "xhh_qr_code": "",        # 当前登录二维码code
    "xhh_qr_tokenid": "",     # 扫码登录固定tokenid(生成与轮询必须一致, 登录态绑定发码方)
    "tmp_blocks": "",         # 临时屏蔽(聊天指令): [{"k":词,"until":ts}] 到点自动解封
    "last_types": "",         # 上次会话末尾5条的一级分类(跨会话防重复: 新会话前5条避开)
    "explore_boost_ts": "0",  # 探索模式(聊天指令): 生效截止时间戳(0=未启用)
    "scorer_v": "0",          # 评分器版本(升级新维度时一次性标记)
    "disclaimer_version": "", # 用户已确认的免责声明版本
}


def sget(key):
    # 锁保护: _FX 8线程并发 sget 会导致共享连接读竞态(InterfaceError/空值)
    with _db_lock:
        row = db().execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return row[0] if row else DEFAULTS.get(key, "")


def sset(key, value):
    with _db_lock:
        db().execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (key, str(value)))
        db().commit()


def today():
    return time.strftime("%Y-%m-%d")


def bump_stats(**kw):
    with _db_lock:
        conn = db()
        cols = ",".join("%s=IFNULL(%s,0)+?" % (k, k) for k in kw)
        conn.execute("INSERT INTO stats(day,%s) VALUES(?,%s) ON CONFLICT(day) DO UPDATE SET %s"
                     % (",".join(kw), ",".join("?" * len(kw)), cols),
                     (today(),) + tuple(kw.values()) + tuple(kw.values()))
        conn.commit()


def day_stats():
    row = db().execute("SELECT IFNULL(in_tokens,0),IFNULL(out_tokens,0),IFNULL(calls,0),"
                       "IFNULL(received,0),IFNULL(filtered,0) FROM stats WHERE day=?",
                       (today(),)).fetchone()
    if not row:
        return {"ai_calls": 0, "cost": 0.0, "received": 0, "filtered": 0}
    pi, po = float(sget("price_in")), float(sget("price_out"))
    cost = (row[0] * pi + row[1] * po) / 1e6
    return {"ai_calls": row[2], "cost": round(cost, 4), "received": row[3], "filtered": row[4]}


# ---------------------------------------------------------------- 筛选漏斗仪表盘(今日统计, 进程内)
# 记录"抓回多少条 → 每条规则各扔多少条 → 最终留多少", 用于发现异常漏斗并做抓取侧优化。
_funnel_lock = threading.Lock()
_funnel = {"day": today(), "counts": {}}
_FUNNEL_LABELS = {
    "fetched": "抓回候选", "partition": "分区不符", "age": "超时间窗", "news": "资讯保鲜",
    "views": "播放量窗口", "duration": "时长窗口", "emotion": "情绪过滤", "dislike": "厌恶词",
    "blocklist": "拉黑名单", "help": "求助帖", "xhh": "小黑盒筛选", "dedup": "重复标题",
    "follow_window": "关注超强调窗", "cooldown": "展示冷却", "cooldown_relaxed": "冷却不足复用", "ai_gate": "AI门禁", "kept": "最终入池",
    "build_full": "全量构建", "build_quick": "快速构建",
}


def _funnel_bump(key, n=1):
    with _funnel_lock:
        if _funnel["day"] != today():
            _funnel.update(day=today(), counts={})
        _funnel["counts"][key] = _funnel["counts"].get(key, 0) + n


def funnel_stats():
    with _funnel_lock:
        if _funnel["day"] != today():
            _funnel.update(day=today(), counts={})
        return dict(_funnel["counts"])


# ---------------------------------------------------------------- B站数据源

# 每次刷新轮换取 6 个源(交错组合), 保证刷新时出现新内容
# 每项: (显示名, 分组id, 接口地址); 分组可手动开关(默认全开)
SOURCE_GROUPS = [
    ("rank", "全站榜"), ("hot", "热门"),
    ("anime", "动画"), ("bangumi", "番剧"), ("guochuang", "国创"), ("guichu", "鬼畜"),
    ("music", "音乐"), ("dance", "舞蹈"), ("game", "游戏"), ("esports", "电竞"),
    ("knowledge", "知识"), ("tech", "科技"), ("digital", "数码"),
    ("life", "生活"), ("food", "美食"), ("animal", "动物圈"), ("vlog", "生活Vlog"),
    ("film", "影视"), ("movie", "电影"), ("series", "电视剧"), ("documentary", "纪录片"),
    ("sports", "运动"), ("events", "赛事"), ("auto", "汽车"),
    ("fashion", "时尚"), ("beauty", "美妆"), ("ent", "娱乐"), ("comic", "漫画"),
    ("news", "资讯"), ("live", "直播"), ("military", "军事"),
]
SOURCES_PER_FETCH = 6
_src_cursor = 0
_page_cursor = 0

# 源产出率自学习: 记录每个数据源"抓回→机器筛选存活"的通过率(指数衰减)。
# 通过率过低的源少抓/跳过, 把抓取预算让给高产源 —— 避免"抓1万条只剩几条"的漏斗浪费。
_SRC_YIELD_MIN = 0.12        # 低于此通过率的源被裁剪(从未抓过的源不裁, 给冷启动机会)
_SRC_YIELD_DECAY = 0.7       # 每轮旧数据衰减系数, 近期表现权重更高
_src_yield = {}              # gid -> {"kept": n, "fetched": n, "yield": f}
_src_yield_lock = threading.Lock()


def _src_yield_update(gid, fetched, kept):
    if not gid or fetched <= 0:
        return
    rate = kept / float(fetched)
    with _src_yield_lock:
        cur = _src_yield.get(gid) or {}
        old = cur.get("yield", rate)
        _src_yield[gid] = {"fetched": cur.get("fetched", 0) + fetched,
                           "kept": cur.get("kept", 0) + kept,
                           "yield": _SRC_YIELD_DECAY * old + (1 - _SRC_YIELD_DECAY) * rate}


def _src_yield_ok(gid):
    """该源历史通过率是否允许本轮抓取(未知=允许)。"""
    with _src_yield_lock:
        v = _src_yield.get(gid)
    return not v or v["yield"] >= _SRC_YIELD_MIN


def _src_yield_recover(gid, amount=0.15):
    """被跳过的源缓慢恢复通过率(epsilon-greedy 探索): 防止低产源被裁后不再被抓、
    通过率永不更新而永久饿死。每跳过一轮回升一档, 几轮后回到阈值以上重新参与抓取。"""
    with _src_yield_lock:
        v = _src_yield.get(gid)
        if v:
            v["yield"] = min(1.0, v["yield"] + (1.0 - v["yield"]) * amount)


def _src_yield_prune(avail, per):
    """低产源裁剪: 按传入(轮转)顺序优先取高产/未知源, 不足 per 个时用低产源补齐(宁多勿空)。"""
    if len(avail) <= per:
        return avail
    with _src_yield_lock:
        low = {g for g, v in _src_yield.items() if v["yield"] < _SRC_YIELD_MIN}
    if not low:
        return avail[:per]
    good = [s for s in avail if s[1] not in low][:per]
    if len(good) < per:
        good += [s for s in avail if s[1] in low][:per - len(good)]
    picked_gids = {s[1] for s in good}
    for g in low:   # 本轮没被选中的低产源缓慢恢复, 保证之后仍有重试机会
        if g not in picked_gids:
            _src_yield_recover(g)
    return good

# 分区内容走"搜索词引擎"(真实按分区); 这里只有全球性榜单(实测 popular 的 rid 参数被B站无视!)
# (显示名, 分组id, URL模板, rid, 可翻页数)
BILI_SOURCES = [
    ("全站榜", "rank", "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all", 0, 1),
    ("热门1", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门2", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门3", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门4", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门5", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门6", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门7", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
    ("热门8", "hot", "https://api.bilibili.com/x/web-interface/popular?ps=50&pn={p}", 0, 8),
]


def enabled_source_ids():
    raw = sget("m_sources")
    if not raw:
        return {g[0] for g in SOURCE_GROUPS}   # 默认全部允许
    try:
        ids = json.loads(raw)
    except Exception:
        return {g[0] for g in SOURCE_GROUPS}
    return {i for i in ids if any(g[0] == i for g in SOURCE_GROUPS)}


# ---------------------------------------------------------------- 相关推荐链 + 搜索词引擎(WBI)

MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
             61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62,
             11, 36, 20, 34, 44, 52]
_wbi_cache = {"ts": 0, "img": "", "sub": ""}


def wbi_keys():
    if time.time() - _wbi_cache["ts"] > 3600:
        try:
            d = bili_get("https://api.bilibili.com/x/web-interface/nav")
            img = (d.get("data") or {}).get("wbi_img", {})
            def key(u):
                return (u.rsplit("/", 1)[-1].split(".")[0]) if u else ""
            _wbi_cache.update(ts=time.time(), img=key(img.get("img_url")), sub=key(img.get("sub_url")))
        except Exception:
            return "", ""
    return _wbi_cache["img"], _wbi_cache["sub"]


def wbi_enc(params):
    """对请求参数做 WBI 签名(社区公开算法)。"""
    img, sub = wbi_keys()
    if not img:
        return urllib.parse.urlencode(params)
    mixin = "".join((img + sub)[i] for i in MIXIN_TAB)[:32]
    p = dict(params)
    p["wts"] = int(time.time())
    q = urllib.parse.urlencode(sorted(p.items()))
    q = re.sub(r"[\x21\x27\x28\x29\x2a]", "", q)
    p["w_rid"] = hashlib.md5((q + mixin).encode("utf-8")).hexdigest()
    return urllib.parse.urlencode(p)


def normalize_search(it):
    """搜索接口返回条目 → 统一格式(标题带html标签/播放是字符串/时长 mm:ss)。"""
    title = re.sub(r"<[^>]+>", "", html.unescape(it.get("title", "")))
    views = it.get("play", 0)
    if isinstance(views, str):
        m = re.match(r"([\d.]+)(万|亿)?", views)
        views = int(float(m.group(1)) * (10000 if m.group(2) == "万" else 100000000 if m.group(2) == "亿" else 1)) if m else 0
    dur = it.get("duration") or "00:00"
    try:
        parts = [int(x) for x in str(dur).split(":")]
        duration = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
    except Exception:
        duration = 0
    pic = (it.get("pic") or "").replace("http://", "https://")
    if pic.startswith("//"):
        pic = "https:" + pic
    tag = it.get("tag")
    if isinstance(tag, str):
        tag = tag.strip()   # B站搜索/相关返回: 逗号分隔字符串(可能带html标签)
        tag = re.sub(r"<[^>]+>", "", html.unescape(tag))
    elif isinstance(tag, list):   # 有些接口返回数组
        tag = ",".join(str(x) for x in tag)
    else:
        tag = ""
    return {"key": "https://www.bilibili.com/video/%s" % it.get("bvid", ""),
            "platform": "B站", "title": title, "author": it.get("author", ""),
            "author_mid": it.get("mid") or it.get("author_mid") or it.get("uid") or 0,
            "desc": (it.get("description") or "")[:500], "cover": pic,
            "views": views, "views_known": True, "likes": 0, "duration": duration, "duration_known": True,
            "danmaku": it.get("danmaku", 0), "reply": it.get("reply", 0),
            "pubdate": it.get("pubdate", 0),
            "url": "https://www.bilibili.com/video/%s" % it.get("bvid", ""),
            "gid": "search", "tag": tag[:200],
            "area": it.get("parent_area_name") or it.get("typename") or "",
            "tname": it.get("typename") or ""}


def fetch_related(seed):
    """相关推荐链: 从种子视频拉最多40条相关(免登录免签名)。"""
    bvid = (seed.get("url") or "").rsplit("/", 1)[-1]
    if not bvid:
        return []
    try:
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/archive/related?bvid=%s" % bvid,
            headers=HEADERS)
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    except Exception:
        return []
    return [it for it in (d.get("data") or []) if it.get("bvid")]


def pick_seeds(items):
    """选高价值种子: 用户点过赞的 > 账号常看作者 > 高热兜底, 最多4个。"""
    seeds = []
    for (k,) in db().execute(
            "SELECT key FROM feedback WHERE action='like' ORDER BY ts DESC LIMIT 6").fetchall():
        r = db().execute("SELECT json FROM items WHERE key=?", (k,)).fetchone()
        if r:
            try:
                seeds.append(json.loads(r[0]))
            except Exception:
                pass
    if not seeds:
        acct = account_slim()
        for a in (acct.get("authors") or [])[:3]:
            for it in items:
                if it["author"] == a["name"]:
                    seeds.append(it)
                    break
    if not seeds:
        seeds = sorted(items, key=lambda x: -x["views"])[:4]
    out, seen_url = [], set()
    for s in seeds:
        if s.get("url") and s["url"] not in seen_url:
            seen_url.add(s["url"])
            out.append(s)
        if len(out) >= 4:
            break
    return out


WORD_BANK = {
    "anime": ["新番推荐", "动漫混剪", "动画解说"],
    "bangumi": ["新番推荐", "追番", "四月新番", "十月新番"],
    "guochuang": ["国漫", "国产动画", "国创"],
    "guichu": ["鬼畜搞笑", "鬼畜调教"],
    "dance": ["宅舞", "舞蹈翻跳"],
    "life": ["生活vlog", "美食教程", "收纳整理"],
    "food": ["美食探店", "家常菜", "烘焙", "地方美食"],
    "animal": ["萌宠", "猫咪", "狗狗", "动物世界"],
    "vlog": ["日常vlog", "旅行vlog", "记录生活"],
    "film": ["电影解说", "影视混剪", "剧集推荐"],
    "movie": ["电影预告", "电影解说", "影评"],
    "series": ["电视剧", "追剧", "剧集解说"],
    "documentary": ["纪录片", "自然纪录片", "历史纪录片"],
    "music": ["音乐翻唱", "live现场", "原创音乐"],
    "game": ["独立游戏", "单机游戏", "游戏评测"],
    "esports": ["电竞", "LPL", "KPL", "赛事集锦"],
    "knowledge": ["科普视频", "硬核知识", "学习方法"],
    "tech": ["人工智能", "前沿科技", "科技资讯"],
    "digital": ["数码评测", "手机体验", "装机教程"],
    "sports": ["健身", "体育", "球类", "运动"],
    "events": ["体育赛事", "联赛", "比赛集锦"],
    "beauty": ["美妆教程", "护肤测评"],
    "fashion": ["穿搭分享", "时尚搭配"],
    "auto": ["汽车评测", "新能源车"],
    "ent": ["综艺", "娱乐", "明星", "影视娱乐"],
    "comic": ["漫画", "漫评", "黑白漫画"],
    "news": ["资讯", "新闻", "时政"],
    "live": ["直播回放", "直播录像"],
    "military": ["军事", "军武", "武器装备", "军事历史", "军迷"],   # 聚合分区(B站无独立军事区, 关键词聚合)
}
KW_SUFFIXES = ["教程", "推荐", "盘点", "实测", "对比", "合集", "评测", "解析", "入门", "安利"]
# 随机采样词库(分区专属): 随机只在你选的分区内随机
RANDOM_BY_GROUP = {
    "anime": ["动漫", "国漫", "番剧", "动画电影", "二次元", "手办模型", "动画短片", "新番"],
    "bangumi": ["新番", "番剧", "追番", "动画新番", "一月新番", "七月新番"],
    "guochuang": ["国漫", "国产动画", "国创", "国产漫画"],
    "guichu": ["鬼畜", "调教", "恶搞", "搞笑", "整活"],
    "dance": ["宅舞", "街舞", "舞蹈", "编舞", "舞蹈教程"],
    "life": ["日常vlog", "美食教程", "收纳整理", "手作", "木工", "露营", "骑行", "钓鱼"],
    "food": ["探店", "家常菜", "烘焙", "地方美食", "美食", "吃播"],
    "animal": ["萌宠", "猫咪", "狗狗", "动物", "宠物", "仓鼠"],
    "vlog": ["vlog", "日常vlog", "旅行vlog", "生活记录"],
    "film": ["电影解说", "剧集推荐", "纪录片", "影视混剪", "电影"],
    "movie": ["电影", "预告片", "影评", "电影幕后"],
    "series": ["电视剧", "追剧", "剧集", "网剧"],
    "documentary": ["纪录片", "自然纪录片", "历史纪录片", "人文纪录片"],
    "music": ["翻唱", "音乐现场", "民谣", "live", "器乐", "原创音乐", "电音"],
    "game": ["游戏实况", "单机游戏", "独立游戏", "游戏攻略", "主机游戏", "手游", "电竞", "游戏评测"],
    "esports": ["电竞", "赛事", "LPL", "KPL", "比赛集锦", "战队"],
    "knowledge": ["科普", "历史", "地理", "天文", "心理", "哲学", "经济", "物理", "化学"],
    "tech": ["科技", "人工智能", "黑科技", "前沿", "机器人", "芯片"],
    "digital": ["数码评测", "手机", "装机", "外设", "摄影摄像", "相机"],
    "sports": ["健身", "体育", "球类", "跑步", "武术", "滑板"],
    "events": ["赛事", "联赛", "比赛", "锦标赛"],
    "beauty": ["美妆", "护肤", "化妆", "口红"],
    "fashion": ["穿搭", "时尚", "潮流", "箱包"],
    "auto": ["汽车", "新能源", "摩托", "自驾", "赛车"],
    "ent": ["综艺", "娱乐", "明星", "娱乐圈", "选秀"],
    "comic": ["漫画", "漫评", "黑白漫画", "绘本"],
    "news": ["资讯", "新闻", "时政", "科技新闻"],
    "live": ["直播", "直播回放", "直播录像", "云直播"],
    "military": ["军事", "军武", "武器", "战争", "军迷", "战术", "国防"],
}
# 相关链白名单: tname → 分组id; 只开部分分区时, 未收录的一律丢弃(严格制)
TNAME_GROUP = {
    "MAD·AMV": "anime", "短片·手书": "anime", "综合": "anime", "动画短片": "anime",
    "同人·手书": "anime", "特摄": "anime", "短片": "anime", "绘画": "anime",
    "音乐现场": "music", "MV": "music", "演奏": "music", "翻唱": "music", "音乐教学": "music",
    "音乐综合": "music", "音乐V": "music", "电音": "music", "说唱": "music",
    "原创音乐": "music", "乐评盘点": "music", "VOCALOID·UTAU": "music",
    "宅舞": "dance", "街舞": "dance", "舞蹈教程": "dance", "舞蹈综合": "dance",
    "颜值·网红舞": "dance",
    "单机游戏": "game", "手机游戏": "game", "网络游戏": "game", "电子竞技": "game",
    "游戏实况": "game", "游戏日常": "game", "GMV": "game", "主机游戏": "game",
    "人文历史": "knowledge", "社科·法律·心理": "knowledge", "财经·商学": "knowledge",
    "校园学习": "knowledge", "职业职场": "knowledge", "科学科普": "knowledge",
    "野生技能协会": "knowledge", "财经商业": "knowledge",
    "数码综合": "digital", "手机平板": "digital", "摄影摄像": "digital",
    "影音智能": "digital", "电脑装机": "digital", "电脑硬件": "digital", "数码": "digital",
    "美妆护肤": "beauty", "仿妆cos": "beauty",
    "穿搭": "fashion", "时尚潮流": "fashion",
    "汽车": "auto", "汽车生活": "auto", "摩托车": "auto",
    "日常": "life", "搞笑": "life", "美食制作": "life", "出行": "life",
    "美食测评": "life", "美食记录": "life", "美食侦探": "life", "田园美食": "life",
    "三农": "life", "汪星人": "life", "亲子": "life", "小剧场": "life",
    "电影": "film", "影视杂谈": "film", "影视剪辑": "film", "短剧": "film",
    "番剧": "bangumi", "连载动画": "bangumi", "资讯": "bangumi", "完结动画": "bangumi",
    "国创": "guochuang", "国产动画": "guochuang", "国产原创相关": "guochuang",
    "科技": "tech", "科学科普": "knowledge", "知识": "tech", "社科·法律·心理": "knowledge",
    "人工智能": "tech", "黑科技": "tech", "健康": "life", "社会": "life",
    "纯爱": "bangumi", "搞笑": "life", "鬼畜": "guichu", "鬼畜剧场": "guichu",
    "美食检测": "food", "美食制作": "food", "美食测评": "food", "美食侦探": "food",
    "田园美食": "food", "美食品鉴": "food", "火锅": "food", "烹饪": "food",
    "宠物": "animal", "萌宠": "animal", "动物": "animal", "喵星人": "animal",
    "汪星人": "animal", "小宠": "animal", "野生动物": "animal",
    "日常": "life", "生活": "life", "户外": "life", "Vlog": "vlog",
    "综艺": "ent", "娱乐": "ent", "娱乐圈": "ent", "明星": "ent",
    "体育": "sports", "健身": "sports", "篮球": "sports", "足球": "sports",
    "运动": "sports", "户外运动": "sports", "竞技": "sports",
    "赛事": "events", "赛事综合": "events", "体育赛事": "events", "直播": "live",
    "电竞": "esports", "竞技游戏": "esports", "英雄联盟": "esports", "王者荣耀": "esports",
    "漫画": "comic", "漫画综合": "comic", "黑白漫画": "comic", "绘本": "comic",
    "新闻": "news", "时政": "news", "社会新闻": "news",
    "纪录片": "documentary", "人文·历史·自然": "documentary",
    "电视剧": "series", "国产剧": "series", "欧美剧": "series", "日剧": "series",
    "泰剧": "series", "电视剧综合": "series",
}
# 搜索条目的一级分区名 → 分组id(严格制, B站一级分区全集)
AREA_GROUP = {"动画": "anime", "番剧": "bangumi", "国创": "guochuang",
              "音乐": "music", "舞蹈": "dance", "游戏": "game", "电竞": "esports",
              "知识": "knowledge", "科技": "tech", "数码": "digital",
              "生活": "life", "美食": "food", "动物圈": "animal", "Vlog": "vlog",
              "影视": "film", "电影": "movie", "电视剧": "series", "纪录片": "documentary",
              "运动": "sports", "赛事": "events", "汽车": "auto",
              "时尚": "fashion", "美妆": "beauty", "娱乐": "ent", "漫画": "comic",
              "资讯": "news", "直播": "live", "鬼畜": "guichu"}
_recent_rand = []
_kw_cursor = 0
_kw_page = 0
_fetch_count = 0
_pref_cursor = 0   # 偏好词轮换游标


def pref_keywords(enabled):
    """偏好备注 → 搜索词(逗号分词, 2-12字); 每轮取2个, 多轮刷新轮换覆盖全部偏好。"""
    raw = sget("prefs_note") or ""
    out = []
    for part in re.split(r"[，,、;；\n。！？!?]+", raw):
        p = part.strip()
        if 2 <= len(p) <= 12:
            out.append(p)
    if not out:
        return []
    if len(out) == 1:
        return out
    idx = _pref_cursor % len(out)
    return [out[idx], out[(idx + 1) % len(out)]]


def pref_all_kws():
    """偏好备注全部关键词(不轮换), 供严肃模式等场景使用。"""
    raw = sget("prefs_note") or ""
    out = []
    for part in re.split(r"[，,、;；\n。！？!?]+", raw):
        p = part.strip()
        if 2 <= len(p) <= 12:
            out.append(p)
    return out


def serious_kw_pool(enabled):
    """严肃模式定向词池: 用户偏好优先, 每个主题生成教程/课程/原理变体, 通用词只作补充。"""
    pref = []
    for raw in pref_all_kws():
        p = raw.strip()
        low = p.lower()
        if any(k in low for k in ("教程", "学习", "入门", "课程", "培训", "教学", "公开课", "开发")):
            variants = [p]
        else:
            variants = [p + " 教程", p + " 课程", p + " 原理", p + " 开发"]
        pref.extend(variants)
    rest = list(SERIOUS_EXTRA)
    idx = _pref_cursor % len(rest)
    rotated = rest[idx:] + rest[:idx]
    return list(dict.fromkeys(pref + rotated))


def _subset_mode(enabled):
    return bool(enabled) and enabled != {g[0] for g in SOURCE_GROUPS}


# 军事聚合分区: B站无独立军事一级分区, 按关键词归属(开启军事时生效)
MILITARY_KWS = ("军事", "军武", "军迷", "武器装备", "枪械", "战争", "装甲", "战机",
                "舰艇", "弹药", "战术", "阅兵", "飞行员", "航母", "导弹", "征兵", "国防")


def item_domain_ok(it, enabled):
    """统一分区校验: 子集模式下按 tname/area 归类, 无法归类(或明确非本域)一律丢弃。
    非B站平台(贴吧/小黑盒等)豁免: 独立平台各自管理, 不随B站分区开关。"""
    if it.get("platform") != "B站":
        return True
    if not _subset_mode(enabled):
        return True
    if "military" in enabled:   # 军事聚合: 关键词命中(标题/tag/分区名) → 归军事
        text = ((it.get("title") or "") + (it.get("tname") or "") +
                (it.get("tag") or "") + (it.get("typename") or "")).lower()
        if any(k in text for k in MILITARY_KWS):
            return True
    t = it.get("tname") or ""
    a = it.get("area") or it.get("typename") or ""
    g = TNAME_GROUP.get(t) or AREA_GROUP.get(a)
    return g in enabled if g else False


def random_word_candidates(enabled):
    words = []
    for gid, ws in RANDOM_BY_GROUP.items():
        if gid in enabled:
            words += ws
    return words or [w for ws in RANDOM_BY_GROUP.values() for w in ws]


def ensure_kw_pool():
    """可生长的词池: 词带分区标记, 切换分区自动清非本域旧词; 少于20个时补足。"""
    global _fetch_count
    gids = enabled_source_ids()
    all_gids = {g[0] for g in SOURCE_GROUPS}
    cnt = db().execute("SELECT COUNT(*) FROM kw").fetchone()[0]
    added, tags = [], []
    t = int(time.time())
    if cnt and gids != all_gids:   # 只开了部分分区 → 清掉非本域旧词(AI词保留)
        with _db_lock:
            db().execute("DELETE FROM kw WHERE gid IS NULL OR (gid != 'ai' AND gid NOT IN (%s))"
                         % ",".join("?" * len(gids)), tuple(gids))
            db().commit()
        cnt = db().execute("SELECT COUNT(*) FROM kw").fetchone()[0]
    if cnt < 20:
        for gid, words in WORD_BANK.items():
            if gid in gids:
                for w in words:
                    for sfx in KW_SUFFIXES:
                        cand = w + sfx
                        if len(cand) <= 12:
                            added.append(cand)
                            tags.append(gid)
        for k in list((account_slim().get("keywords") or []))[:6]:
            added.append(k)
            tags.append("ai")
        if ai_on():
            try:   # LLM 补充新搜索词(每次1次调用)
                content, _ = call_llm([
                    {"role": "system", "content":
                        "为B站内容推荐生成12个搜索词, 每个2-10字, 与已有词不同角度, 只输出JSON: {\"keywords\": [\"\",\"\"]}"},
                    {"role": "user", "content": "用户信息:\n" + (user_context() or "(无)")[:800]}],
                    max_tokens=300)
                for k in [x.strip() for x in json.loads(content).get("keywords", []) if x.strip()]:
                    added.append(k)
                    tags.append("ai")
            except Exception:
                pass
    elif ai_on() and _fetch_count % 12 == 0 and cnt < 120:
        try:   # 定期扩充词池, 防止词库用尽
            content, _ = call_llm([
                {"role": "system", "content":
                    "再为B站推荐生成10个全新的搜索词(避开常用词), 只输出JSON: {\"keywords\": [\"\",\"\"]}"},
                {"role": "user", "content": "用户信息:\n" + (user_context() or "(无)")[:800]}],
                max_tokens=300)
            for k in [x.strip() for x in json.loads(content).get("keywords", []) if x.strip()]:
                added.append(k)
                tags.append("ai")
        except Exception:
            pass
    with _db_lock:
        for w, g in zip(added, tags):
            db().execute("INSERT OR IGNORE INTO kw(kw,last_used,new_cnt,ok_cnt,gid) VALUES(?,?,0,0,?)",
                         (w, t, g))
        db().commit()


def pick_kws(n=4):
    """LRU 选词: 最久没用过的优先。"""
    rows = db().execute("SELECT kw FROM kw ORDER BY last_used ASC LIMIT ?", (n,)).fetchall()
    return [r[0] for r in rows]


def search_pushdown():
    """按当前通用筛选规则推导搜索下推参数(供给优先原则):
    duration: 仅当某档整档通过时长窗口、且其余档整档无效(不丢任何有效供给)时才下推;
    order: 时间窗开启且未启用播放量窗口时才用 pubdate —— 新发布视频播放量低,
    若同时有播放量下限会把搜索来源整体过滤光(B站供给塌方的根因, 已修复)。
    返回 {} 表示无可下推条件(抓回后再筛)。"""
    try:
        min_dur = int(sget("m_min_duration") or 0)
        max_dur = int(sget("m_max_duration") or 0)
        min_views = int(sget("m_min_views") or 0)
        max_views = int(sget("m_max_views") or 0)
    except Exception:
        min_dur = max_dur = min_views = max_views = 0
    buckets = {1: (0, 600), 2: (600, 1800), 3: (1800, None)}

    def fully_valid(lo, hi):
        return (not min_dur or lo >= min_dur) and (not max_dur or (hi is not None and hi <= max_dur))

    def fully_invalid(lo, hi):
        return (min_dur and hi is not None and hi <= min_dur) or (max_dur and lo >= max_dur)

    fully_valid_buckets = [b for b, (lo, hi) in buckets.items() if fully_valid(lo, hi)]
    partial_buckets = [b for b, (lo, hi) in buckets.items()
                       if not fully_valid(lo, hi) and not fully_invalid(lo, hi)]
    push = {}
    # 下推档位 X 的前提: 其余档位全部整档无效(部分有效的档位里藏着本可通过的供给, 不能丢)。
    if len(fully_valid_buckets) == 1 and not partial_buckets:
        push["duration"] = str(fully_valid_buckets[0])
    try:
        if int(sget("m_max_age_months") or 0) > 0 and min_views == 0 and max_views == 0:
            push["order"] = "pubdate"
    except Exception:
        pass
    return push


def search_items(keyword, page=1, limit=30, order=None, duration=None):
    """WBI签名搜索(实测免登录可用, 可翻页)。失败返回[]。
    order/duration: 筛选条件下推(见 search_pushdown), 不传则行为与旧版一致。"""
    try:
        params = {"search_type": "video", "keyword": keyword,
                  "page": str(page), "page_size": str(limit)}
        if order:
            params["order"] = order
        if duration:
            params["duration"] = duration
        qs = wbi_enc(params)
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/wbi/search/type?" + qs,
            headers=dict(HEADERS, Cookie=account_cookie()) if account_cookie() else HEADERS)
        d = json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8"))
        if d.get("code") != 0:
            return []
        res = (d.get("data") or {}).get("result") or []
        return [normalize_search(it) for it in res[:limit] if it.get("bvid")]
    except Exception:
        return []
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}

_feed_cache = {"ts": 0, "payload": None}
_FX = ThreadPoolExecutor(max_workers=8)   # 常规并行抓取池
_SERIOUS_X = ThreadPoolExecutor(max_workers=16)  # 严肃模式多平台 fan-out 抓取池
_SCORE_X = ThreadPoolExecutor(max_workers=3)   # 评分池(独立, 3并发防DeepSeek限流)
_TAG_X = ThreadPoolExecutor(max_workers=3)     # 标签补全池(独立, 3并发防B站风控)


def _safe_json(url, timeout=10):
    """带错误收集的GET JSON。返回 (data, error)。"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as e:
        return None, str(e)[:80]


def normalize(it):
    stat = it.get("stat") or {}
    owner = it.get("owner") or {}
    pic = it.get("pic") or ""
    if pic.startswith("//"):
        pic = "https:" + pic
    else:
        pic = pic.replace("http://", "https://")
    tag = it.get("tag")
    if isinstance(tag, (list, tuple)):
        tag = ",".join(str(x) for x in tag)
    elif isinstance(tag, str):
        tag = re.sub(r"<[^>]+>", "", html.unescape(tag.strip()))
    else:
        tag = ""
    return {
        "key": "https://www.bilibili.com/video/%s" % it.get("bvid", ""),
        "platform": "B站",
        "title": it.get("title", ""),
        "author": owner.get("name", ""),
        "author_mid": owner.get("mid") or it.get("mid") or 0,
        "desc": (it.get("desc") or "")[:500],
        "cover": pic,
        "views": stat.get("view", 0), "views_known": stat.get("view") is not None,
        "likes": stat.get("like", 0),
        "danmaku": stat.get("danmaku", 0),
        "reply": stat.get("reply", 0),
        "duration": it.get("duration", 0), "duration_known": it.get("duration") is not None,
        "pubdate": it.get("pubdate", 0),
        "url": "https://www.bilibili.com/video/%s" % it.get("bvid", ""),
        "tname": it.get("tname", ""),
        "area": it.get("parent_area_name") or it.get("typename") or "",
        "tag": tag[:200],
    }


# ---------------------------------------------------------------- 贴吧数据源
#
# 无官方API: 走"BAIDUID 会话"通路(实测) —— 先访问 forumpage 种下 BAIDUID cookie,
# 再用同一会话访问经典版 /f?kw= 列表页(无 cookie 时 403)。登录(BDUSS)后更稳。
# 解析目标: li.j_thread_list 块 → tid/标题/楼主/摘要/回复数/最多2张缩略图/时间。

TB_RE = re.compile(r'<li[^>]*class="[^"]*j_thread_list[^"]*"[^>]*>')

# 贴吧原始数据调试记录(用于定位"求助"分类字段): 保留最近若干条, 用户触发采集后由运维读取
_tb_debug_buf = []
_tb_debug_lock = threading.Lock()


def _tb_dump(rec):
    try:
        with _tb_debug_lock:
            _tb_debug_buf.append(rec)
            if len(_tb_debug_buf) > 150:
                del _tb_debug_buf[:len(_tb_debug_buf) - 150]
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tb_debug.jsonl"),
                      "w", encoding="utf-8") as f:
                for r in _tb_debug_buf:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:
        pass

_tb_sess_jars = []          # [cookiejar] 匿名会话池(轮换, 防单会话被风控)
_tb_sess_idx = 0
_tb_sess_lock = threading.Lock()
_tb_last_err = ""
_tb_last_fetch = 0          # 最近一次贴吧抓取时间戳(状态面板用)
_tb_fetch_total = 0         # 本次会话贴吧累计条目(状态面板用)

_CDP_PORT = 0               # 浏览器调试端口(0=未启动)
_CDP_PROC = None            # 浏览器子进程
_CDP_LOGIN = {"ts": 0, "status": "idle", "message": ""}
_CDP_HOST_KEYS = ("BDUSS", "STOKEN", "TIEBA_UID", "BAIDUID", "BAIDUID_BFESS",
                  "TIEBA_UID_BFESS", "H_WISE_SIDS", "H_PS_PSSID", "Hm_lvt")
_tb_avatar_cache = {"ts": 0, "data": None, "ctype": "image/jpeg"}
_xhh_avatar_cache = {"ts": 0, "data": None, "ctype": "image/jpeg"}


def tb_cookie():
    return sget("tb_cookie") or ""


def tb_users():
    """吧名列表: 每行/逗号分隔, 去重, 最多50个。"""
    raw = sget("tb_bars") or ""
    out = []
    for part in re.split(r"[\n,，、;；]+", raw):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out[:50]


def tb_set_bars(bars):
    sset("tb_bars", "\n".join(b.strip() for b in bars if b.strip()))


def tieba_bar_allowed(it):
    """贴吧候选只允许来自用户配置的吧; 未配置吧列表时不限制。"""
    bars = [b.lower() for b in tb_users()]
    if not bars:
        return True
    text = " ".join(str(it.get(k) or "") for k in ("tname", "tag", "src")).lower()
    return any(b in text for b in bars)


def tb_weight():
    try:
        w = int(sget("tb_weight") or 25)
    except Exception:
        w = 25
    return w if w in {0, 25, 50, 75, 100} else 25


def bili_weight():
    try:
        w = int(sget("bili_weight") or 90)
    except Exception:
        w = 90
    return w if w in {0, 25, 50, 75, 100} else 90


def _tb_session_jar():
    """获取/创建匿名会话 jar(池轮换)。失效时重建。"""
    global _tb_sess_idx
    with _tb_sess_lock:
        if not _tb_sess_jars:
            _tb_sess_jars.append(http.cookiejar.CookieJar())
        jar = _tb_sess_jars[_tb_sess_idx % len(_tb_sess_jars)]
        _tb_sess_idx += 1
        return jar


def _tb_seed(jar):
    """匿名会话: 访问 forumpage 种下 BAIDUID(实测 /f?kw= 无cookie必403)。"""
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req = urllib.request.Request(
            "https://tieba.baidu.com/f/index/forumpage?kw=steam",
            headers=dict(TB_HEADERS, Referer="https://tieba.baidu.com/"))
        with opener.open(req, timeout=10) as resp:
            resp.read()
        return any(c.name == "BAIDUID" for c in jar)
    except Exception:
        return False


def _tb_fetch(url, referer="https://tieba.baidu.com/", timeout=12):
    """贴吧 GET: 登录cookie优先; 匿名走"种BAIDUID→请求"通路, 403/302/空壳自动重试1次。
    登录cookie失效时(302→登录页/403)自动降级匿名重试, 不把"登录过期"当采集故障。
    403 时快速失败(不再无谓重试) —— 限流会由 fetch_tieba_pool 的冷却机制兜底。"""
    cookie = tb_cookie()
    for attempt in range(2):   # 0=登录cookie 1=匿名重试(限流时不重试)
        if attempt == 0 and cookie:
            try:
                req = urllib.request.Request(url, headers=dict(
                    TB_HEADERS, Referer=referer, Cookie=cookie))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", "ignore")
                if "j_thread_list" in body or "data-field" in body:
                    return body, None
            except urllib.error.HTTPError as e:
                if e.code in (302, 403, 401):
                    continue   # 登录态失效 → 降级匿名
                return None, "HTTP %d" % e.code
            except Exception as e:
                return None, str(e)[:80]
        # 匿名: 种 BAIDUID → 请求
        jar = _tb_session_jar()
        _tb_seed(jar)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            req = urllib.request.Request(url, headers=dict(TB_HEADERS, Referer=referer))
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
            if "j_thread_list" in body or "data-field" in body:
                return body, None
        except urllib.error.HTTPError as e:
            return None, "HTTP %d" % e.code   # 匿名403=限流, 快速失败(不重试)
        except Exception as e:
            return None, str(e)[:80]
        return body, None   # 空壳页: 这次就这样(下次轮换新会话)
    return None, "请求失败"


def _tb_html_to_items(body, fname, max_items=50):
    """解析列表页 → 贴吧条目(统一 item 格式)。
    置顶/广告判定: 只看标题锚点前方局部区(广告div在</li>后、归入下一块, 不能整块判)。"""
    items = []
    blocks = TB_RE.split(body)
    for blk in blocks[1:]:
        if len(items) >= max_items:
            break
        m = re.search(r'href="(/p/(\d{8,}))"[^>]*title="([^"]*)"', blk)
        if not m:
            continue   # 广告块无帖子链接, 天然跳过
        pre = blk[max(0, m.start() - 500):m.start()]
        if "icon-top" in pre or "icon_thread_hidden" in pre:
            continue   # 置顶/隐藏帖
        tid, title = m.group(2), html.unescape(m.group(3)).strip()
        if not title:
            continue
        # 贴吧求助分类标签(span.pb-title-prefix, 如"求助"): F12确认的class, 命中即求助帖
        helpm = re.search(r'pb-title-prefix[^>]*>\s*([^<]+?)\s*<', blk)
        tclass = html.unescape(helpm.group(1)).strip() if helpm else ""
        if not tclass:
            mtc = re.search(r'【([^】]{1,8})】', title)
            tclass = mtc.group(1) if mtc else ""
        _tb_dump({"src": "html", "tid": tid, "title": title, "tclass": tclass,
                  "prefix_html": blk[max(0, m.start() - 400):m.start()]})
        rm = re.search(r'threadlist_rep_num[^>]*>\s*(\d+)', blk)
        reply = int(rm.group(1)) if rm else 0
        cm = re.search(r'is_show_create_time[^>]*>\s*([^<]+?)\s*<', blk)
        pubdate = tb_time_parse(cm.group(1)) if cm else 0
        am = re.search(r'class="frs-author-name[^"]*"[^>]*>([^<]+)<', blk)
        author = html.unescape(am.group(1)).strip() if am else ""
        absm = re.search(r'threadlist_abs[^>]*>\s*(.*?)\s*</div>', blk, re.S)
        abstract = ""
        if absm:
            abstract = re.sub(r"<[^>]+>", "", absm.group(1)).strip()[:600]
        imgs = []
        # 优先 bpic(580宽大图, 预览清晰); 退回 data-original(200×90缩略图)
        for u in re.findall(r'\bbpic="([^"]+)"', blk):
            u = u.strip()
            if u.startswith("//"):
                u = "https:" + u
            if "bdstatic" in u and "public/img" in u:
                continue   # 占位图
            imgs.append(u)
            if len(imgs) >= 2:
                break
        if not imgs:
            for u in re.findall(r'data-original="([^"]+)"', blk):
                u = u.strip()
                if u.startswith("//"):
                    u = "https:" + u
                if "bdstatic" in u and "public/img" in u:
                    continue   # 占位图
                # 缩略图 → 大图: wh%3D200%2C90%3B → w%3D580%3B
                u = re.sub(r"wh%3D200%2C90%3B", "w%3D580%3B", u)
                imgs.append(u)
                if len(imgs) >= 2:
                    break
        url = "https://tieba.baidu.com/p/%s" % tid
        items.append({
            "key": url, "platform": "贴吧", "title": title[:120],
            "author": author[:30],
            "desc": abstract[:500],
            "cover": imgs[0] if imgs else "",
            "covers": imgs[:2],
            "views": 0, "likes": 0, "danmaku": None,
            "reply": reply, "duration": 0,
            "pubdate": pubdate,
            "url": url,
            "tname": fname, "area": "", "tag": "", "tclass": tclass[:12],
            "gid": "tieba", "src": "贴吧·%s" % fname,
        })
    return items


def tb_time_parse(raw):
    """贴吧时间: '7-30' / '23:32' / '08-23 12:00' → 时间戳(今年/最近)。"""
    raw = (raw or "").strip()
    if not raw:
        return 0
    now = time.localtime()
    m = re.match(r"^(\d{1,2})-(\d{1,2})", raw)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            t = time.mktime((now.tm_year, month, day, 12, 0, 0, 0, 0, -1))
            if t > time.time() + 86400:   # 未来 → 去年
                t = time.mktime((now.tm_year - 1, month, day, 12, 0, 0, 0, 0, -1))
            return int(t)
    m = re.match(r"^(\d{1,2}):(\d{2})", raw)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            t = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hh, mm, 0, 0, 0, -1))
            return int(t)
    return 0


def fetch_tieba_bar(fname, page=0):
    """单个吧列表页 → (items, err)。页游标外部控制。"""
    url = "https://tieba.baidu.com/f?kw=%s&ie=utf-8&pn=%d" % (
        urllib.parse.quote(fname), page * 50)
    body, err = _tb_fetch(url)
    if not body:
        return [], err or "无数据"
    items = _tb_html_to_items(body, fname)
    if not items:
        return [], "解析为空(吧名无效/改版?)"
    return items, ""


_tb_cooldown_until = 0   # 贴吧全403冷却截止时间戳(避免每次构建都白等重试链)


def fetch_tieba_pool(quick=False):
    """吧池轮换采集: 每轮取1-2个吧(并行), 每吧1页。返回 (items, errors)。
    403冷却: 一旦全部403进入冷却期, 期间直接空返回(不再拖慢内容池构建)。"""
    global _tb_last_err, _tb_last_fetch, _tb_fetch_total, _tb_cooldown_until
    bars = tb_users()
    if not bars:
        return [], []
    if time.time() < _tb_cooldown_until:
        return [], []   # 冷却中: 快速返回, 不浪费构建时间
    items, errors, seen = [], [], set()
    per = 1 if quick else 2
    idx = int(sget("tb_cursor") or 0)
    picked = [bars[(idx + i) % len(bars)] for i in range(min(per, len(bars)))]
    sset("tb_cursor", (idx + per) % len(bars))
    # 并行抓取(每吧独立会话, 无跨吧依赖); 每吧随机页, 偏向前几页但引入乱序
    allow_help_fetch = sget("tb_allow_help") != "0"
    results = _FX.map(lambda fname: fetch_tieba_bar(fname, random.choice([0, 0, 1, 2])), picked)
    for fname, (its, err) in zip(picked, results):
        if its:
            for it in its:
                if it["key"] in seen:
                    continue
                if not allow_help_fetch and is_help_post(it):
                    _funnel_bump("help")
                    continue   # 求助帖抓取期直接剔除, 不占池子名额与筛选预算
                seen.add(it["key"])
                items.append(it)
        else:
            errors.append("%s: %s" % (fname, err or "空"))
    if items:
        _tb_last_fetch = int(time.time())
        _tb_fetch_total += len(items)
        sset("tb_last_fetch", _tb_last_fetch)
        sset("tb_error", "")
        _tb_last_err = ""
        _tb_cooldown_until = 0
    elif errors and all("403" in e or "402" in e or "429" in e for e in errors):
        # 全部限流 → 长冷却(60分钟): 贴吧对服务器IP风控属持续封锁, 频繁重试只会加重;
        # 期间跳过贴吧采集(不拖慢内容池), 用户可在贴吧账号页手动"重试采集"
        n_errs = len(errors)
        _tb_last_err = "贴吧服务端被风控(403), 长冷却中(60分钟)…"
        sset("tb_error", _tb_last_err)
        _tb_cooldown_until = int(time.time()) + 3600
        return [], errors
    elif errors:
        _tb_last_err = "; ".join(errors)[:300]
        sset("tb_error", _tb_last_err)
    else:
        sset("tb_error", "")
        _tb_last_err = ""
    return items, errors


def _tb_search_fetch(url, referer="https://tieba.baidu.com/", timeout=12):
    """贴吧 wap JSON 接口 GET: 登录cookie优先, 匿名走"种BAIDUID→请求"。
    与 _tb_fetch 不同: 纯JSON响应, 不做 j_thread_list 内容标记校验。"""
    cookie = tb_cookie()
    if cookie:
        try:
            req = urllib.request.Request(url, headers=dict(
                TB_HEADERS, Referer=referer, Cookie=cookie))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore"), None
        except Exception:
            pass   # 登录态失效/限流 → 降级匿名
    jar = _tb_session_jar()
    _tb_seed(jar)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        req = urllib.request.Request(url, headers=dict(TB_HEADERS, Referer=referer))
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore"), None
    except Exception as e:
        return None, str(e)[:80]


def _tb_int(v):
    """贴吧接口数字字段兼容: "1.2W"/"8600"/"931" 等都能转成 int"""
    try:
        s = str(v).strip()
        m = re.match(r"([\d.]+)([Ww万]?)$", s)
        if not m:
            return 0
        n = float(m.group(1))
        if m.group(2):
            n *= 10000
        return int(n)
    except Exception:
        return 0


def tb_search_items(q, rn=20, fname=""):
    """贴吧帖子搜索; fname 非空时只搜索指定吧, 防止严肃模式扫全站。"""
    q = (q or "").strip()
    fname = (fname or "").strip()
    if not q or time.time() < _tb_cooldown_until:
        return []
    url = ("https://tieba.baidu.com/mo/q/search/thread?word=%s&pn=1&rn=%d"
           % (urllib.parse.quote(q), rn))
    if fname:
        url += "&kw=" + urllib.parse.quote(fname)
    
    body, err = _tb_search_fetch(url)
    if not body:
        return []
    try:
        d = json.loads(body)
    except Exception:
        return []
    if d.get("no") != 0:
        return []
    items = []
    for p in ((d.get("data") or {}).get("post_list") or []):
        tid = str(p.get("tid") or "")
        title = re.sub(r"</?em>", "", html.unescape(p.get("title") or "")).strip()
        if not tid or not title:
            continue
        _tb_dump({"src": "search", "tid": tid, "title": title,
                  "keys": list(p.keys()),
                  "fields": {k: str(v)[:180] for k, v in p.items()}})
        # 贴吧分类文本字段(thread_types 是复杂位掩码, 无求助专属位, 不作判据) / 标题【求助】
        tclass = ""
        for fk in ("topic", "types", "is_help", "has_help", "is_help_thread"):
            v = p.get(fk)
            if not v:
                continue
            if isinstance(v, list):
                for el in v:
                    nm = el.get("name") if isinstance(el, dict) else str(el)
                    if nm:
                        tclass = nm
                        break
            elif isinstance(v, dict):
                tclass = v.get("name") or ""
            else:
                tclass = str(v)
            if tclass:
                break
        if not tclass:
            mtc = re.search(r'【([^】]{1,8})】', title)
            tclass = mtc.group(1) if mtc else ""
        cover = ""
        for m_ in (p.get("media") or []):
            u = m_.get("small_pic") or m_.get("water_pic") or ""
            if u:
                cover = u
                break
        user = p.get("user") or {}
        fname = p.get("forum_name") or (p.get("forum_info") or {}).get("forum_name") or ""
        u_ = "https://tieba.baidu.com/p/" + tid
        it = {
            "key": u_, "platform": "贴吧", "title": title[:120],
            "author": (user.get("show_nickname") or user.get("user_name") or "")[:30],
            "desc": re.sub(r"<[^>]+>", "", p.get("content") or "")[:500],
            "cover": cover, "covers": [cover] if cover else [],
            "views": 0, "likes": _tb_int(p.get("like_num")), "danmaku": None,
            "reply": _tb_int(p.get("post_num")), "duration": 0,
            "pubdate": int(p.get("time") or p.get("create_time") or 0),
            "url": u_, "tname": fname, "area": "", "tag": fname, "tclass": tclass[:12],
            "gid": "tieba", "src": "贴吧·%s" % (fname or "搜索"),
        }
        if tieba_bar_allowed(it):
            items.append(it)
    return items


def _tieba_thread_cache_put(key, payload):
    _tieba_thread_cache[key] = {"payload": payload, "ts": time.time()}
    if len(_tieba_thread_cache) > 80:   # 容量控制: 丢最旧
        oldest = min(_tieba_thread_cache, key=lambda k: _tieba_thread_cache[k]["ts"])
        _tieba_thread_cache.pop(oldest, None)


def tieba_thread_content(tid):
    """抓取并解析贴吧 wap 帖子页(mo/q/m) → 站内阅读数据。
    wap 页固定返回首批楼层(不支持pn翻页, AJAX加载的后续楼层拿不到);
    图片用 pb_img_item 的完整 data-url(带签名参数)——不带签名直连原图会得到
    "打开贴吧App"的 4KB 占位图。杂项(diversBanner/脚本/操作按钮栏)全部剥离。"""
    key = str(tid).strip()
    cached = _tieba_thread_cache.get(key)
    if cached and time.time() - cached["ts"] < _TIEBA_THREAD_TTL:
        return cached["payload"]
    body, err = _tb_fetch("https://tieba.baidu.com/mo/q/m?kz=%s" % key, timeout=15)
    if err or not body:
        return {"ok": False, "error": err or "抓取失败"}
    if "list_item_top_name" not in body:
        return {"ok": False, "error": "被贴吧风控拦截，稍后再试或点右上角跳转App"}
    t_m = re.search(r"<title>([^<]+)</title>", body)
    title = html.unescape(t_m.group(1)).strip() if t_m else "贴吧帖子"
    title = re.sub(r"[_\-]?\s*贴吧\s*$", "", title)
    floors = []
    chunks = re.split(r'<li [^>]*?data-tid="\d+"', re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.S))[1:]
    for ch in chunks:
        ch = re.split(r'<div class="father-cut-daoliu-normal-box"|<div class="operate_statistic_wrapper"', ch)[0]   # 楼层内容在其之前, 之后是导流页脚/回复收藏栏
        a = re.search(r'class="user_name"[^>]*>([^<]+)<', ch)
        t = re.search(r'list_item_time">([^<]+)<', ch)
        av = re.search(r'<img src="([^"]+)"[^>]*class="user_img"', ch)
        lz = re.search(r'<div class="content"[^>]* lz="1"', ch)
        fn = re.search(r'fn="(\d+)"', ch)
        cm = re.search(r'<div class="content"[^>]*>(.*)', ch, re.S)
        if not cm:
            continue
        content = cm.group(1)
        images = []
        for u in re.findall(r"data-url='([^']+)'", content):
            if u.startswith("//"):
                u = "https:" + u
            if u.startswith("http") and u not in images:
                images.append(u)
        text = re.sub(r'<div id="diversBanner".*?</div>', "", content, flags=re.S)
        text = re.sub(r'<div class="pb_less_imgs">.*$', "", text, flags=re.S)
        text = re.sub(r"<img[^>]*>", " ", text)
        text = re.sub(r"<br\s*/?>", chr(10), text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).strip()
        if not text and not images:
            continue
        floors.append({"floor": int(fn.group(1)) if fn else len(floors) + 1,
                       "author": html.unescape(a.group(1)).strip() if a else "匿名",
                       "avatar": (av.group(1) if av else "").replace("http://", "https://"),
                       "lz": bool(lz),
                       "time": t.group(1).strip() if t else "",
                       "text": text[:2000], "images": images[:6]})
    payload = {"ok": True, "title": title, "floors": floors[:30]}
    _tieba_thread_cache_put(key, payload)
    return payload


def _xhh_thread_cache_put(key, payload):
    _xhh_thread_cache[key] = {"payload": payload, "ts": time.time()}
    if len(_xhh_thread_cache) > 80:
        oldest = min(_xhh_thread_cache, key=lambda k: _xhh_thread_cache[k]["ts"])
        _xhh_thread_cache.pop(oldest, None)


def xhh_thread_content(link_id):
    """小黑盒帖子站内阅读: /bbs/app/link/tree → link.text 富文本块 + 首页评论。
    link.text 是 JSON 数组字符串: {"text":"<p>..</p>"} 文本块 与 {"url":图} 图块交替。"""
    key = str(link_id).strip()
    cached = _xhh_thread_cache.get(key)
    if cached and time.time() - cached["ts"] < _XHH_THREAD_TTL:
        return cached["payload"]
    d, err = xhh_get("/bbs/app/link/tree", {"link_id": key})
    if err or not d:
        return {"ok": False, "error": err or "接口失败"}
    if d.get("status") == "show_captcha":
        # 验证码 → 降级: 用本地池里已入库的描述/封面拼一个预览, 不让用户白点
        try:
            row = db().execute(
                "SELECT json FROM items WHERE json LIKE ? ORDER BY fetched_at DESC LIMIT 1",
                ('%' + key + '%',)).fetchone()
            if row:
                it = json.loads(row[0])
                if it.get("platform") == "小黑盒":
                    return {"ok": True, "degraded": True,
                            "title": it.get("title") or "小黑盒帖子",
                            "author": it.get("author") or "",
                            "create_at": int(it.get("pubdate") or 0),
                            "content": (it.get("desc") or "")[:500],
                            "images": [c for c in (it.get("covers") or []) if c][:2],
                            "comments": [], "comment_total": it.get("reply")}
        except Exception:
            pass
        return {"ok": False, "error": "小黑盒要求人机验证(访问过频)，稍后再试或点右上角跳转App"}
    if d.get("status") != "ok":
        return {"ok": False, "error": "接口状态异常: %s" % d.get("status")}
    res = d.get("result") or {}
    link = res.get("link") or {}
    blocks = []
    try:
        blocks = json.loads(link.get("text") or "[]")
        if not isinstance(blocks, list):
            blocks = []
    except Exception:
        blocks = []
    texts, images = [], []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        u = b.get("url")
        if u and u not in images:
            images.append(u)
        t = b.get("text")
        if t:
            t = t.replace("</p>", chr(10))
            t = t.replace("<br>", chr(10)).replace("<br/>", chr(10)).replace("<br />", chr(10))
            t = re.sub(r"<[^>]+>", "", t)
            t = html.unescape(t).strip()
            if t:
                texts.append(t)
    for u in (link.get("imgs") or []):
        if u and u not in images:
            images.append(u)
    comments = []
    for w in (res.get("comments") or [])[:20]:
        c = (w or {}).get("comment") or {}
        u = c.get("user") or {}
        txt = re.sub(r"\[\w+\]|\[cube_[^\]]*\]", "", c.get("description") or c.get("text") or c.get("content") or "")
        txt = html.unescape(txt).strip()
        cimgs = [i for i in (c.get("imgs") or c.get("pics") or []) if i]
        if not txt and not cimgs:
            continue
        comments.append({"author": (u.get("username") or "匿名")[:30],
                         "text": txt[:300], "images": cimgs[:3],
                         "create_at": int(c.get("create_at") or 0)})
    payload = {"ok": True,
               "title": (link.get("title") or "")[:120],
               "xlid": str(link.get("linkid") or ""),
               "ltag": str(link.get("link_tag") or ""),
               "author": ((link.get("user") or {}).get("username") or "")[:30],
               "create_at": int(link.get("create_at") or 0),
               "content": chr(10).join(x for x in texts if x)[:8000],
               "images": images[:12],
               "comments": comments,
               "comment_total": res.get("total_floor_num")}
    _xhh_thread_cache_put(key, payload)
    return payload


def tb_login_check():
    """贴吧登录态: dc/common/tbs → is_login。返回 dict(含昵称/头像)。
    判定前提: 必须含 BDUSS(半截cookie如有STOKEN无BDUSS视为未登录)。"""
    nickname, avatar = sget("tb_user") or "", sget("tb_avatar") or ""
    cookie = tb_cookie()
    if not cookie or "BDUSS=" not in cookie:
        return {"logged_in": False, "nickname": nickname, "avatar": avatar}
    try:
        req = urllib.request.Request("https://tieba.baidu.com/dc/common/tbs",
                                     headers=dict(TB_HEADERS, Cookie=cookie))
        d = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8"))
        if d.get("is_login") == 1:
            return {"logged_in": True, "nickname": nickname or "已登录",
                    "avatar": avatar, "tbs": d.get("tbs", "")}
        return {"logged_in": False, "nickname": "", "avatar": ""}
    except Exception:
        return {"logged_in": bool(cookie), "nickname": nickname, "avatar": avatar}


def tb_profile_from_cookie():
    """用BDUSS拉昵称/头像(mo/q/newmoindex, 手机版网页接口, 免CDP)。
    portrait 形如 tb.1.xxxx → 头像 https://tb.himg.baidu.com/sys/portrait/item/<portrait>。
    返回 (nickname, avatar_url), 失败返回 ("", "")。"""
    cookie = tb_cookie()
    if not cookie or "BDUSS=" not in cookie:
        return "", ""
    try:
        req = urllib.request.Request("https://tieba.baidu.com/mo/q/newmoindex",
                                     headers=dict(TB_HEADERS, Referer="https://tieba.baidu.com/",
                                                  Cookie=cookie))
        d = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore"))
        data = d.get("data") or {}
        portrait = str(data.get("portrait") or "").strip()
        name = str(data.get("name") or data.get("nickname") or "").strip()
        avatar = ("https://tb.himg.baidu.com/sys/portrait/item/" + portrait) if portrait else ""
        return name, avatar
    except Exception:
        return "", ""


def tb_unread_messages():
    """读取贴吧“回复我 + @我”的真实未读数，无法确认时返回None。"""
    state = tb_login_check()
    if not state.get("logged_in"):
        return None
    cookie = tb_cookie()
    total = 0
    try:
        for page, key in (("https://tieba.baidu.com/mo/q/replyme", "replyme"),
                          ("https://tieba.baidu.com/mo/q/atme", "atme")):
            req = urllib.request.Request(page, headers=dict(TB_HEADERS,
                                         Referer="https://tieba.baidu.com/", Cookie=cookie))
            raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
            text = html.unescape(raw)
            # 页面组件配置中的 count.replyme/count.atme 是服务端提供的真实未读数。
            match = re.search(r"(?:[\"']?%s[\"']?\s*[:=]\s*)[\"']?(\d+)" % key, text)
            if not match:
                return None
            total += int(match.group(1))
        return max(0, total)
    except Exception:
        return None


def tb_mylike():
    """登录后拉取关注吧列表(最多3页)。返回 (bars, err)。"""
    cookie = tb_cookie()
    if not cookie:
        return [], "未登录"
    bars, seen = [], set()
    for pn in range(1, 4):
        url = "https://tieba.baidu.com/f/like/mylike?ie=utf-8&pn=%d" % pn
        try:
            req = urllib.request.Request(url, headers=dict(TB_HEADERS, Cookie=cookie))
            body = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        except Exception as e:
            return bars or None, str(e)[:80]
        found = False
        for m in re.finditer(r'href="/f\?kw=([^"&]+)"', body):
            name = urllib.parse.unquote(m.group(1))
            if name and name not in seen and not any(c in name for c in " ￥%#"):
                seen.add(name)
                bars.append(name)
                found = True
        if not found and pn == 1:
            return None, "未找到关注吧(登录失效?)"
    return bars[:50], ""


# ---------------------------------------------------------------- 贴吧一键登录(CDP)
#
# 扫码接口已死(实测getqrcode/getqrcodestatus均返回同一张固定图);
# 客户端API需私有签名。方案: 弹出真实浏览器窗口(Edge/Chrome, 独立profile),
# 用户像平时一样登录(短信/扫码/第三方任选), 后台经 CDP getAllCookies 抓 BDUSS。

_TB_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files\Chromium\Application\chrome.exe",
    r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
    r"C:\Program Files\Opera\launcher.exe",
]


def _tb_find_browser():
    """寻找支持 CDP 的 Chromium 浏览器; 不要求必须 Edge/Chrome。"""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = list(_TB_BROWSERS) + [
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(local, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        os.path.join(local, "Vivaldi", "Application", "vivaldi.exe"),
        os.path.join(local, "Programs", "Opera", "opera.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    for name in ("msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe", "opera.exe", "chromium.exe"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def tb_login_start():
    """启动浏览器窗口(独立profile, 打开贴吧登录页)。"""
    global _CDP_PROC, _CDP_PORT, _CDP_LOGIN
    if _CDP_PROC and _CDP_PROC.poll() is None:
        return {"ok": True, "message": "登录窗口已打开"}
    browser = _tb_find_browser()
    if not browser:
        try:
            webbrowser.open("https://passport.baidu.com/v2/?login")
            _CDP_LOGIN = {"ts": int(time.time()), "status": "manual",
                          "message": "已用默认浏览器打开贴吧登录；登录后请粘贴 Cookie 保存"}
            return {"ok": True, "manual": True,
                    "message": "已用默认浏览器打开贴吧登录，请登录后粘贴 Cookie 保存"}
        except Exception as e:
            return {"ok": False, "error": "未找到支持自动登录的 Chromium 浏览器，且无法打开默认浏览器: %s" % str(e)[:80]}
    profile = os.path.join(DATA_DIR, "_tb_login_profile")
    if os.path.exists(profile):
        # 残留profile(上次进程未杀干净)会让Edge启动即退 → 每次全新
        try:
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass
    os.makedirs(profile, exist_ok=True)
    try:
        _CDP_PROC = subprocess.Popen([
            browser, "--remote-debugging-port=0",
            "--user-data-dir=%s" % profile,
            "--no-first-run", "--no-default-browser-check",
            # 关键: 跳过Edge首启同步确认页(edge://sync-confirmation-dialog会拦截一切导航,
            # 导致登录后贴吧页面无法加载、头像抓取失败)
            "--disable-features=msEdgeFirstRunExperience,msEdgeSidebarV2,msEdgeWalletCheckout",
            "--disable-sync", "--disable-background-networking", "--disable-component-update",
            "--disable-gpu",
            "https://passport.baidu.com/v2/?login",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _CDP_PROC = None
        return {"ok": False, "error": "启动失败: %s" % str(e)[:100]}
    _CDP_PORT = 0
    _CDP_LOGIN = {"ts": int(time.time()), "status": "starting", "message": "浏览器启动中…"}
    # 读 DevToolsActivePort 文件拿到实际端口(port=0 自动分配)
    port_file = os.path.join(profile, "DevToolsActivePort")
    for _ in range(60):
        if os.path.exists(port_file):
            try:
                with open(port_file) as f:
                    _CDP_PORT = int(f.readline().strip())
                break
            except Exception:
                pass
        if _CDP_PROC.poll() is not None:
            _CDP_LOGIN.update(status="error", message="浏览器启动失败")
            return {"ok": False, "error": "浏览器进程退出"}
        time.sleep(0.5)
    if not _CDP_PORT:
        return {"ok": False, "error": "调试端口未就绪(10s)"}
    _CDP_LOGIN.update(status="waiting", message="请在窗口中完成登录(短信/扫码/第三方均可)")
    return {"ok": True, "message": "登录窗口已打开"}


def tb_login_poll():
    """轮询 CDP 取 cookie; 抓到 BDUSS 即保存、顺路抓昵称/头像、关闭窗口。"""
    global _CDP_PROC, _CDP_PORT, _CDP_LOGIN
    if not _CDP_PROC or _CDP_PROC.poll() is not None:
        _CDP_LOGIN.update(status="closed", message="窗口已关闭")
        return dict(_CDP_LOGIN)
    if not _CDP_PORT:
        return _CDP_LOGIN
    if time.time() - _CDP_LOGIN["ts"] > 600:
        tb_login_stop()
        return {"status": "expired", "message": "登录超时(10分钟), 请重试"}
    try:
        import json as _json
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % _CDP_PORT,
                                    timeout=3) as resp:
            targets = _json.loads(resp.read().decode("utf-8"))
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            return {"status": "waiting", "message": "等待登录页面…"}
        ws = page["webSocketDebuggerUrl"]
        cookies = _cdp_get_cookies(ws)
        if not cookies:
            return {"status": "waiting", "message": "轮询中…"}
        got = {}
        for c in cookies:
            dom = c.get("domain") or ""
            if "baidu.com" in dom and c.get("name") in _CDP_HOST_KEYS:
                got[c["name"]] = c["value"]
        if got.get("BDUSS"):
            cookie = "; ".join("%s=%s" % (k, v) for k, v in got.items())
            sset("tb_cookie", cookie)
            # 顺路抓昵称/头像: 导航贴吧首页 → 读 DOM(失败不阻塞登录)
            nickname, portrait = tb_profile_from_page(ws)
            sset("tb_user", nickname or "已登录")
            sset("tb_avatar", portrait)
            tb_login_stop()
            return {"status": "ok", "message": "贴吧登录成功", "cookie_saved": True,
                    "nickname": nickname or "已登录", "avatar": portrait}
        return {"status": "waiting", "message": "正在登录…(检测到登录状态后自动完成)"}
    except Exception as e:
        _CDP_LOGIN.update(status="waiting", message="轮询: %s" % str(e)[:60])
        return dict(_CDP_LOGIN)


def tb_login_stop():
    global _CDP_PROC, _CDP_PORT, _CDP_LOGIN
    if _CDP_PROC and _CDP_PROC.poll() is None:
        try:
            # taskkill /T: 杀整棵进程树(仅terminate主进程会残留子进程, 锁住profile)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(_CDP_PROC.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            try:
                _CDP_PROC.terminate()
                _CDP_PROC.wait(timeout=5)
            except Exception:
                try:
                    _CDP_PROC.kill()
                except Exception:
                    pass
    _CDP_PROC = None
    _CDP_PORT = 0
    _CDP_LOGIN.update(status="stopped", message="窗口已关闭")
    return {"ok": True}


def _cdp_set_cookie(ws_url, name, value, url):
    """CDP Network.setCookie: 向指定URL域写入cookie。"""
    try:
        s = socket.create_connection(_cdp_addr(ws_url), timeout=5)
    except Exception:
        return False
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" %
               (_cdp_path(ws_url), *_cdp_addr(ws_url), key))
        s.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                return False
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            return False
        _cdp_ws_send(s, {"id": 1, "method": "Network.setCookie", "params": {
            "name": name, "value": value, "url": url, "path": "/"}})
        s.settimeout(6)
        while True:
            try:
                m = _cdp_ws_recv(s)
            except Exception:
                break
            if not m:
                break
            if m.get("id") == 1:
                return bool((m.get("result") or {}).get("success"))
        return False
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _cdp_eval(ws_url, expression, timeout=8):
    """CDP Runtime.evaluate: 同一连接发 Runtime.evaluate 命令(自动重试3次, 导航期WS偶发断)。
    返回解析后的 result.value(dict/list/str) 或 None。"""
    for attempt in range(3):
        try:
            s = socket.create_connection(_cdp_addr(ws_url), timeout=5)
        except Exception:
            time.sleep(0.8)
            continue
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                   "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n" %
                   (_cdp_path(ws_url), *_cdp_addr(ws_url), key))
            s.sendall(req.encode())
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            if b" 101 " not in resp.split(b"\r\n")[0]:
                s.close()
                time.sleep(0.8)
                continue
            _cdp_ws_send(s, {"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": expression, "returnByValue": True, "awaitPromise": True}})
            deadline = time.time() + timeout
            s.settimeout(timeout)
            while time.time() < deadline:
                msg = _cdp_ws_recv(s)
                if not msg:
                    break
                if msg.get("id") == 1:
                    r = (msg.get("result") or {}).get("result") or {}
                    return r.get("value")
            s.close()
        except Exception:
            try:
                s.close()
            except Exception:
                pass
            time.sleep(0.8)
            continue
        try:
            s.close()
        except Exception:
            pass
        break
    return None


# 贴吧列表页: 登录态下 PageData.user(user_name/portrait) 是最稳定来源(服务端渲染)
TB_USERINFO_JS = """
(function(){
  try {
    if (window.PageData && window.PageData.user) {
      var u = window.PageData.user;
      var name = (u.user_name || "").trim();
      var pt = u.portrait || "";
      var avatar = "";
      if (pt && pt !== "0" && pt !== "None" && pt.indexOf("/") >= 0) {
        avatar = pt.indexOf("//") === 0 ? "https:" + pt : pt;
      } else if (pt && pt !== "0" && pt !== "None") {
        // 贴吧portrait规则: https://tb1.bdstatic.com/tb/cms/portrait/<hash>
        avatar = "https://tb1.bdstatic.com/tb/cms/portrait/" + pt;
      }
      return {name: name, avatar: avatar, portrait: pt, is_login: !!u.is_login};
    }
    return {name: "", avatar: "", is_login: false};
  } catch (e) { return {name: "", avatar: "", err: String(e).slice(0, 80)}; }
})()
"""


# 贴吧首页登录态: 昵称/头像 DOM 探测(多选择器兜底; 头像排除 header/banner/logo 等装饰图)
TB_PROFILE_JS = """
(function(){
  try {
    var out = {name: "", avatar: ""};
    // ① 优先: 新版页面 img.user-avatar / img.avatar-img(实测 gss0.bdstatic.com/portrait/item/...)
    var aImg = document.querySelector('img.user-avatar') || document.querySelector('img.avatar-img');
    if (aImg) out.avatar = aImg.src || "";
    // ② PageData.user(旧版列表页服务端渲染)
    if (!out.avatar && window.PageData && window.PageData.user && window.PageData.user.user_name) {
      var u = window.PageData.user;
      out.name = (u.user_name || "").trim();
      var pt = u.portrait || "";
      if (pt && pt !== "0" && pt !== "None") {
        if (pt.indexOf("/") >= 0) {
          out.avatar = pt.indexOf("//") === 0 ? "https:" + pt : pt;
        } else {
          out.avatar = "https://tb1.bdstatic.com/tb/cms/portrait/" + pt;
        }
      }
    }
    // ③ 昵称: home/main链接 / 用户区文本
    if (!out.name) {
      var link = document.querySelector('a[href*="home/main"][href*="un="]');
      if (link) out.name = (link.textContent || "").trim();
    }
    if (!out.name) {
      var names = document.querySelectorAll('.u_name, .user-name, #tuser a, .userbar a[title]');
      for (var i = 0; i < names.length; i++) {
        var t = (names[i].textContent || "").trim();
        if (t && t.length < 30 && !/首页|主页|我的|登录|注册|关注/.test(t)) { out.name = t; break; }
      }
    }
    // ④ 兜底: 任何 portrait/avatar 图(排除装饰)
    if (!out.avatar) {
      var BAD = /header|banner|logo|topic|default|static|icon|cms|cap|puzzle|emotion|thumb/i;
      var imgs = document.querySelectorAll('img');
      for (var j = 0; j < imgs.length; j++) {
        var src = imgs[j].getAttribute("src") || "";
        if (src.indexOf("data:") === 0) continue;
        if (/portrait|avatar/.test(src) && !BAD.test(src)) { out.avatar = src; break; }
      }
    }
    return out;
  } catch (e) { return {name: "", avatar: "", err: String(e).slice(0, 80)}; }
})()
"""


def tb_profile_from_page(ws_url):
    """导航贴吧列表页(服务端渲染, 含 PageData.user) → 抓昵称/头像。
    重定向环/错误页/滑块验证直接放弃(不阻塞登录)。失败返回 ("", "")。"""
    try:
        _cdp_eval(ws_url, "location.href='https://tieba.baidu.com/f?kw=steam&ie=utf-8&pn=0'; true")
        # 等页面就绪(最多6次×2.5s); chrome-error/wappass重定向/passMod滑块→提前放弃
        for _ in range(6):
            time.sleep(2.5)
            href = _cdp_eval(ws_url, "location.href")
            if href is None:
                continue
            if "chromewebdata" in str(href):
                return "", ""   # 错误页
            has_cap = _cdp_eval(ws_url,
                                "document.querySelector('.passMod_puzzle, .passMod, #passport-verify') ? true : false")
            if has_cap:
                return "", ""   # 滑块验证(非自然访问), 放弃头像
            ready = _cdp_eval(ws_url, "document.readyState === 'complete'")
            if ready:
                probe = _cdp_eval(ws_url, TB_USERINFO_JS)
                if isinstance(probe, dict) and probe.get("name"):
                    return str(probe.get("name") or "").strip()[:40], \
                        str(probe.get("avatar") or "").strip()[:300]
                # PageData 不可用 → 退回 DOM 探测
                probe2 = _cdp_eval(ws_url, TB_PROFILE_JS)
                if isinstance(probe2, dict):
                    if probe2.get("name") or probe2.get("avatar"):
                        return str(probe2.get("name") or "").strip()[:40], \
                            str(probe2.get("avatar") or "").strip()[:300]
                    if probe2.get("name") is not None:   # 页面已就绪但无用户信息(未登录)
                        return "", ""
    except Exception:
        pass
    return "", ""


def tb_browser_grab(ws_url):
    """浏览器辅助采集: 读取登录窗口当前贴吧列表页的DOM, 解析为条目。
    新版贴吧为React渲染, 直接读DOM(thread-card-wrapper结构)。
    等待安全验证滑块通过 + 帖子卡片渲染(最长30s)。"""
    try:
        href = _cdp_eval(ws_url, "location.href") or ""
        if "tieba.baidu.com/f" not in href:
            return [], "", "当前页面不是贴吧列表页(请打开 tieba.baidu.com/f?kw=吧名)"
        m = re.search(r"kw=([^&]+)", href)
        fname = urllib.parse.unquote(m.group(1)) if m else ""
        # 等安全验证滑块通过 + 卡片渲染(最长30s, 每2.5s检查)
        for _ in range(12):
            has_cap = _cdp_eval(ws_url,
                                "document.querySelector('.passMod_puzzle, .passMod, #passport-verify') ? true : false")
            cards = _cdp_eval(ws_url, "document.querySelectorAll('.thread-card-wrapper').length")
            if cards and int(cards) > 0:
                break
            time.sleep(2.5)
        items = _tb_html_to_items_dom(ws_url, fname or "未知名")
        return items, fname, ""
    except Exception as e:
        return [], "", str(e)[:80]


def _tb_html_to_items_dom(ws_url, fname):
    """新版贴吧列表(React): 从DOM提取帖数据(thread-card-wrapper结构)。"""
    data = _cdp_eval(ws_url, """
    (function(){
      var out = [];
      var cards = document.querySelectorAll('.thread-card-wrapper, .top-thread-card');
      for (var i = 0; i < cards.length && out.length < 50; i++) {
        var c = cards[i];
        // 置顶/广告剔除
        if (c.closest('.top-thread-card') || c.closest('.ad-card')) continue;
        var a = c.querySelector('a.thread-content-link, a[href*="/p/"]');
        if (!a) continue;
        var tm = (a.getAttribute('href') || '').match(/\\/p\\/(\\d+)/);
        if (!tm) continue;
        var titleEl = c.querySelector('.thread-title, a.thread-content-link');
        var title = titleEl ? (titleEl.getAttribute('title') || titleEl.textContent || '').trim() : '';
        if (!title) continue;
        var img = c.querySelector('.image-card-with-fallback img, img[src*="tiebapic"], img[src*="forum"]');
        var rep = c.querySelector('.reply-num, .thread-reply, [class*="reply"]');
        var au = c.querySelector('.author, [class*="author"]');
        var isrc = img ? (img.getAttribute('src') || '') : '';
        // 缩略图→大图: wh%3D200%2C90%3B(200×90) → w%3D580%3B(580宽)
        if (isrc.indexOf('wh%3D200%2C90%3B') >= 0) {
          isrc = isrc.replace('wh%3D200%2C90%3B', 'w%3D580%3B');
        }
        out.push({
          tid: tm[1], title: title.slice(0, 120),
          img: isrc,
          reply: rep ? (parseInt((rep.textContent || '').replace(/[^0-9]/g, '')) || 0) : 0,
          author: au ? au.textContent.trim().slice(0, 30) : ''
        });
      }
      return JSON.stringify(out);
    })()
    """)
    if not data:
        return []
    try:
        rows = json.loads(data) if isinstance(data, str) else (data or [])
    except Exception:
        return []
    items = []
    for r in rows or []:
        tid, title = r.get("tid", ""), (r.get("title") or "").strip()
        if not tid or not title:
            continue
        url = "https://tieba.baidu.com/p/%s" % tid
        img = r.get("img") or ""
        items.append({
            "key": url, "platform": "贴吧", "title": title[:120],
            "author": (r.get("author") or "")[:30],
            "desc": "", "cover": img, "covers": [img] if img else [],
            "views": 0, "likes": 0, "danmaku": None,
            "reply": int(r.get("reply") or 0), "duration": 0,
            "pubdate": 0, "url": url,
            "tname": fname, "area": "", "tag": "",
            "gid": "tieba", "src": "贴吧·%s" % fname,
        })
    return items


def _cdp_get_cookies(ws_url):
    """极简 WebSocket 客户端: 发 Network.getAllCookies, 收响应。纯标准库。"""
    try:
        s = socket.create_connection(_cdp_addr(ws_url), timeout=5)
    except Exception:
        return None
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" %
               (_cdp_path(ws_url), *_cdp_addr(ws_url), key))
        s.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                return None
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            return None
        # 发送 enable + getAllCookies
        _cdp_ws_send(s, {"id": 1, "method": "Network.enable", "params": {}})
        _cdp_ws_send(s, {"id": 2, "method": "Network.getAllCookies", "params": {}})
        deadline = time.time() + 8
        s.settimeout(8)
        while time.time() < deadline:
            msg = _cdp_ws_recv(s)
            if not msg:
                continue
            if msg.get("id") == 2:
                return (msg.get("result") or {}).get("cookies") or []
        return None
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def _cdp_addr(ws_url):
    u = urllib.parse.urlparse(ws_url)
    return (u.hostname or "127.0.0.1", u.port or 80)


def _cdp_path(ws_url):
    return urllib.parse.urlparse(ws_url).path or "/"


def _cdp_ws_send(s, obj):
    payload = json.dumps(obj).encode("utf-8")
    header = bytearray([0x81])   # FIN + text
    n = len(payload)
    mask = os.urandom(4)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    s.sendall(bytes(header) + masked)


def _cdp_ws_recv(s):
    try:
        h = s.recv(2)
        if len(h) < 2:
            return None
        opcode = h[0] & 0x0F
        length = h[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", s.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", s.recv(8))[0]
        data = b""
        while len(data) < length:
            chunk = s.recv(length - len(data))
            if not chunk:
                return None
            data += chunk
        if opcode in (0x1, 0x2):
            return json.loads(data.decode("utf-8"))
        return None   # ping/pong/close 忽略
    except Exception:
        return None


TB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------- 小黑盒数据源
#
# 网页版API需 hkey/_time/nonce 签名(防重放) —— 算法已逆向移植(纯标准库):
#   nonce = md5(时间戳+随机).upper() ; hkey = 字符集映射交错MD5前缀 + Km校验和
# 帖子流: /bbs/app/feeds (pull/offset/dw 分页) ; 搜索: /bbs/app/api/search/found

XHH_CHARSET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"


def _xhh_vm(e):
    return ((255 & ((e << 1) ^ 27)) if (e & 128) else (e << 1)) & 255


def _xhh_qm(e):
    return _xhh_vm(e) ^ e


def _xhh_dollar_m(e):
    return _xhh_qm(_xhh_vm(e))


def _xhh_ym(e):
    return _xhh_dollar_m(_xhh_qm(_xhh_vm(e)))


def _xhh_gm(e):
    return _xhh_ym(e) ^ _xhh_dollar_m(e) ^ _xhh_qm(e)


def _xhh_km(e_arr):
    e = list(e_arr)
    t = [0, 0, 0, 0]
    t[0] = _xhh_gm(e[0]) ^ _xhh_ym(e[1]) ^ _xhh_dollar_m(e[2]) ^ _xhh_qm(e[3])
    t[1] = _xhh_qm(e[0]) ^ _xhh_gm(e[1]) ^ _xhh_ym(e[2]) ^ _xhh_dollar_m(e[3])
    t[2] = _xhh_dollar_m(e[0]) ^ _xhh_qm(e[1]) ^ _xhh_gm(e[2]) ^ _xhh_ym(e[3])
    t[3] = _xhh_ym(e[0]) ^ _xhh_dollar_m(e[1]) ^ _xhh_qm(e[2]) ^ _xhh_gm(e[3])
    e[0], e[1], e[2], e[3] = t[0], t[1], t[2], t[3]
    return e


def _xhh_av(s, n):
    i = XHH_CHARSET[:n]
    return "".join(i[ord(c) % len(i)] for c in s)


def _xhh_sv(s):
    return "".join(XHH_CHARSET[ord(c) % len(XHH_CHARSET)] for c in s)


def xhh_hkey(url_path, timestamp, nonce):
    """hkey 生成(与网页版一致, 实测对照抓包ZYP71前缀+校验和全对)。"""
    parts = [p for p in url_path.split("/") if p]
    np = "/" + "/".join(parts) + "/"
    c1 = _xhh_av(str(timestamp), -2)
    c2 = _xhh_sv(np)
    c3 = _xhh_sv(nonce)
    comps = [c1, c2, c3]
    ml = max(len(c) for c in comps)
    it = ""
    for k in range(ml):
        for c in comps:
            if k < len(c):
                it += c[k]
    md = hashlib.md5(it[:20].encode()).hexdigest()
    pref = _xhh_av(md[:5], -4)
    km = _xhh_km([ord(c) for c in md[-6:]])
    return pref + str(sum(km) % 100).zfill(2)


def xhh_nonce():
    return hashlib.md5((str(int(time.time() * 1000)) + str(uuid.uuid4())).encode()).hexdigest().upper()


def xhh_token():
    """网页版tokenid: B + 32位随机hex(urlsafe), 可本地生成(实测接受)。"""
    return "B" + base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")


def xhh_tok():
    """取/建持久化设备tokenid(x_xhh_tokenid)。所有小黑盒请求复用同一tokenid。"""
    tok = sget("xhh_qr_tokenid")
    if not tok:
        tok = xhh_token()
        sset("xhh_qr_tokenid", tok)
    return tok


def xhh_parse_login(res):
    """从登录接口 result 提取 (pkey, heybox_id, nickname, avatar)。
    实测网页端扫码成功返回的 result 含 nickname/avatar/account_detail.userid/heyboxid,
    但无 pkey(会话不由 pkey 承载)。字段: result.heyboxid | account_detail.userid;
    昵称 profile.nickname | account_detail.username | result.nickname;
    头像 profile.avatar | account_detail.avatar | account_detail.avartar(官方typo) | result.avatar。"""
    res = res or {}
    prof = res.get("profile") or {}
    ad = res.get("account_detail") or {}
    pkey = res.get("pkey") or ""
    heybox_id = (res.get("heybox_id") or res.get("heyboxid")
                 or prof.get("heybox_id") or ad.get("userid") or "")
    nickname = prof.get("nickname") or ad.get("username") or res.get("nickname") or ""
    avatar = (prof.get("avatar") or ad.get("avatar")
              or ad.get("avartar") or res.get("avatar") or "")
    return pkey, heybox_id, nickname, avatar


XHH_BASE = {
    "os_type": "web", "app": "heybox", "client_type": "web",
    "version": "999.0.4", "web_version": "2.5", "x_client_type": "web",
    "x_app": "heybox_website", "heybox_id": "", "x_os_type": "Windows",
    "device_info": "Chrome", "device_id": "2c2fef8385ccef915e3b3caf94e3aa06",
}


def xhh_get(path, extra=None, token=""):
    """签名GET小黑盒API。返回 (json_dict, err)。
    token: x_xhh_tokenid(网页版设备标识), 缺省用持久化tokenid。
    登录后自动附 pkey/heybox_id(网页版真实会话凭证)。"""
    t = int(time.time())
    n = xhh_nonce()
    hk = xhh_hkey(path, t, n)
    params = dict(XHH_BASE)
    params.update(extra or {})
    params.update({"hkey": hk, "_time": t, "nonce": n})
    params["x_xhh_tokenid"] = token or xhh_tok()
    pk = sget("xhh_pkey")
    hid = sget("xhh_heybox_id")
    if pk:
        params["pkey"] = pk
    if hid:
        params["heybox_id"] = str(hid)
        params["user_heybox_id"] = str(hid)
    url = "https://api.xiaoheihe.cn" + path + "?" + urllib.parse.urlencode(params)
    hdr = {"User-Agent": HEADERS["User-Agent"], "Referer": "https://www.xiaoheihe.cn/",
           "Accept": "application/json, text/plain, */*"}
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore")), None
    except Exception as e:
        return None, str(e)[:100]


def xhh_unread_messages():
    """读取小黑盒评论消息未读数；上游未明确提供未读字段时返回None。"""
    if not sget("xhh_heybox_id"):
        return None
    d, err = xhh_get("/bbs/app/user/message", {
        "list_type": "0", "offset": "0", "limit": "50", "no_more": "false"})
    if err or not d or d.get("status") != "ok":
        return None
    # 当前接口将评论未读总数直接放在响应顶层，空 result 列表仍是有效响应。
    for key in ("message_unread_num", "unread_count", "unread_num", "unread", "new_count"):
        value = d.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, str) and value.isdigit():
            return max(0, int(value))
    result = d.get("result")
    if isinstance(result, dict):
        for key in ("unread_count", "unread_num", "unread", "new_count"):
            value = result.get(key)
            if isinstance(value, (int, float)):
                return max(0, int(value))
        messages = next((value for value in result.values() if isinstance(value, list)), None)
    elif isinstance(result, list):
        messages = result
    else:
        messages = None
    if messages is None:
        return None
    markers = ("is_read", "read", "unread", "is_unread", "has_read")
    items = [item for item in messages if isinstance(item, dict)]
    if not items or not all(any(marker in item for marker in markers) for item in items):
        return None
    unread = 0
    for item in items:
        if item.get("unread") is True or item.get("is_unread") is True or item.get("is_read") is False or item.get("read") is False or item.get("has_read") is False:
            unread += 1
    return unread


def xhh_feed_items(offset=0, limit=20, pull="0"):
    """小黑盒社区帖子流 → 统一item格式列表。"""
    d, err = xhh_get("/bbs/app/feeds", {"pull": pull, "offset": str(offset), "dw": "844"})
    if err or not d or d.get("status") != "ok":
        return [], err or "接口失败"
    links = (d.get("result") or {}).get("links") or []
    items = []
    for lk in links[:limit]:
        if not lk.get("linkid"):
            continue
        url = ""
        # 帖子真实URL = /app/bbs/link/<link_id>(hex); link_id 取自 share_url 查询参数
        su = lk.get("share_url") or ""
        link_id = (urllib.parse.parse_qs(urllib.parse.urlparse(su).query).get("link_id", [""])[0]
                   or lk.get("link_id") or "")
        if link_id:
            url = "https://www.xiaoheihe.cn/app/bbs/link/%s" % link_id
        else:
            url = "https://www.xiaoheihe.cn/app/detail/%s" % lk.get("linkid", "")
        usr = lk.get("user") or {}
        imgs = [u for u in (lk.get("imgs") or []) if u][:2]
        comm = [t.get("name", "") for t in (lk.get("topics") or []) if t.get("name")]
        htags = [h.get("name", "") for h in (lk.get("hashtags") or []) if h.get("name")]
        tags = comm + htags
        # 点击量: 帖子曝光量藏在 h_src(base64) 的 app_feeds_exposure_count 里, 解码尽力取
        views, views_known = 0, False
        hs = lk.get("h_src")
        if hs:
            try:
                dec = base64.b64decode(hs).decode("utf-8", "ignore")
                exp = re.search(r"app_feeds_exposure_count__(\d+)", dec)
                if exp:
                    views = int(exp.group(1))
                    views_known = True
            except Exception:
                pass
        items.append({
            "key": url, "platform": "小黑盒",
            "title": (lk.get("title") or (lk.get("description") or "")[:30])[:120],
            "author": (usr.get("username") or "")[:30],
            "desc": re.sub(r"\[\w+\]|\[cube_[^\]]*\]", "", lk.get("description") or "")[:500],
            "cover": imgs[0] if imgs else "",
            "covers": imgs,
            "views": views, "views_known": views_known, "likes": int(lk.get("link_award_num") or lk.get("up") or 0),
            "danmaku": None, "reply": int(lk.get("comment_num") or 0),
            "duration": 0, "duration_known": False, "pubdate": int(lk.get("create_at") or 0),
            "xcomm": "、".join(comm)[:200],   # 社区/板块(topics), 供小黑盒社区筛选
            "xtopic": "、".join(htags)[:200],  # 话题(hashtags), 供小黑盒话题筛选
            "url": url, "tname": "、".join(tags[:2])[:20], "area": "", "tag": ",".join(tags)[:100],
            "xlid": str(lk.get("linkid") or ""),
            "ltag": str(lk.get("link_tag") or ""),   # 帖子类型标记, 唤起App时用(heybox://link)
            "gid": "xhh", "src": "小黑盒·%s" % (tags[0] if tags else "社区"),
        })
    return items, ""


_xhh_cursor = 0   # 小黑盒帖流偏移游标
_xhh_fetch_total = 0   # 小黑盒累计条目(状态面板用)
_xhh_last_fetch = 0


def xhh_post(path, query, body=None):
    """签名POST小黑盒API: query+body 均带 x_xhh_tokenid。返回 (json_dict, err)。"""
    t = int(time.time())
    n = xhh_nonce()
    hk = xhh_hkey(path, t, n)
    params = dict(XHH_BASE)
    params.update(query or {})
    params.update({"hkey": hk, "_time": t, "nonce": n})
    tok = xhh_tok()
    params["x_xhh_tokenid"] = tok
    pk = sget("xhh_pkey")
    hid = sget("xhh_heybox_id")
    if pk:
        params["pkey"] = pk
    if hid:
        params["heybox_id"] = str(hid)
        params["user_heybox_id"] = str(hid)
    url = "https://api.xiaoheihe.cn" + path + "?" + urllib.parse.urlencode(params)
    b = dict(body or {})
    b["x_xhh_tokenid"] = tok
    hdr = {"User-Agent": HEADERS["User-Agent"], "Referer": "https://www.xiaoheihe.cn/",
           "Accept": "application/json, text/plain, */*",
           "Content-Type": "application/x-www-form-urlencoded"}
    try:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(b).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore")), None
    except Exception as e:
        return None, str(e)[:100]


def fetch_xhh_pool(quick=False):
    """小黑盒采集: 帖流翻页(每轮偏移+limit, 覆盖更多)。返回 (items, errors)。
    full: 每轮抓2页(offset + offset+30, 各30条), 单轮最多~60条, 满足高权重下小黑盒的供给。"""
    global _xhh_cursor, _xhh_fetch_total, _xhh_last_fetch
    if sget("xhh_weight") == "0":
        return [], []
    items, errs = [], []
    if quick:
        its, err = xhh_feed_items(offset=_xhh_cursor, limit=10)
        if its:
            items = its
            _xhh_cursor = (_xhh_cursor + 10) % 400
        elif err:
            errs.append(err[:200])
    else:
        limit = 30
        offsets = [(_xhh_cursor + k * limit) % 400 for k in range(4)]
        # 独立页请求并发，保留固定合并顺序和部分成功结果。
        for its, err in _FX.map(lambda offset: xhh_feed_items(offset=offset, limit=limit), offsets):
            if its:
                seen = {x["key"] for x in items}
                items += [x for x in its if x["key"] not in seen]
            elif err and not items:
                errs.append(err[:200])
        _xhh_cursor = (_xhh_cursor + limit) % 400
    if not items:
        if errs:
            sset("xhh_error", errs[0])
        return [], [errs[0]] if errs else ["无内容"]
    _xhh_fetch_total += len(items)
    _xhh_last_fetch = int(time.time())
    sset("xhh_last_fetch", _xhh_last_fetch)
    sset("xhh_error", "")
    return items, []


def fetch_pool(quick=False):
    """轮换取源 + 页游标 + 相关推荐链 + 搜索词引擎 + 随机采样 + 贴吧(网络请求全部并行)。
    quick=True: 只取2个源(快速开屏, 后续后台补全)。"""
    global _src_cursor, _page_cursor, _kw_cursor, _kw_page, _fetch_count, _pref_cursor
    errors, seen, items, ok, names = [], {}, [], 0, []
    enabled = enabled_source_ids()
    avail = [s for s in BILI_SOURCES if s[1] in enabled] or BILI_SOURCES
    n = len(avail)
    per = 2 if quick else SOURCES_PER_FETCH
    if n <= per:                        # 开启的分区很少时, 全部取
        picked, _src_cursor = avail[:], 0
    else:                                # 轮转全量列表 → 低产源裁剪(产出率自学习) → 取 per 个
        rotated = [avail[(_src_cursor + i) % n] for i in range(n)]
        picked = _src_yield_prune(rotated, per)
        _src_cursor = (_src_cursor + per) % n

    # --- 榜单源: 并行抓取(严肃模式跳过: 榜单内容非教程类, 抓了也全被滤掉) ---
    ser_mode = sget("mode") == "严肃"
    tb_future = xhh_future = fol_future = local_serious_future = None
    serious_search_futures = []
    quick_search_futures = []
    if ser_mode and not quick:
        ser_terms = serious_kw_pool(enabled)[:10]
        # B站严肃关键词搜索 + 允许吧内搜索统一 fan-out, 结果随后统一去重/门禁
        for i, kw in enumerate(ser_terms):
            serious_search_futures.append(("bili", kw, _SERIOUS_X.submit(search_items, kw, 1)))
            if i < 4:
                serious_search_futures.append(("bili", kw, _SERIOUS_X.submit(search_items, kw, 2)))
        for fname in tb_users():
            for kw in ser_terms[:4]:
                serious_search_futures.append(("tb", fname, _SERIOUS_X.submit(tb_search_items, kw, 20, fname)))
        tb_future = _SERIOUS_X.submit(fetch_tieba_pool, False)
        xhh_future = _SERIOUS_X.submit(fetch_xhh_pool, False)
        local_serious_future = _SERIOUS_X.submit(_local_pool_hits, ser_terms[:8], set(), 160)
        if sget("bili_follow_emph") == "1":
            fol_future = _SERIOUS_X.submit(fetch_following_videos)
    elif quick:
        if tb_weight() > 0:
            tb_future = _FX.submit(fetch_tieba_pool, True)
        if int(sget("xhh_weight") or 15) > 0:
            xhh_future = _FX.submit(fetch_xhh_pool, True)
        # 快速开屏搜索词与榜单阶段同时开跑(此前先等榜单完成再搜, 两段串行拖慢首屏)
        kws = []
        if ser_mode:
            kws = serious_kw_pool(enabled)[:6]
        for gid, words in WORD_BANK.items():
            if gid in enabled and not ser_mode:
                kws += words
        if kws:
            sample_n = min(8 if ser_mode else 2, len(kws))
            picked_kws = kws[:sample_n] if ser_mode else random.sample(kws, sample_n)
            _push = search_pushdown()
            pool = _SERIOUS_X if ser_mode else _FX
            quick_search_futures = [
                (kw, pool.submit(search_items, kw, pg,
                                 order=_push.get("order"), duration=_push.get("duration")))
                for kw in picked_kws
                for pg in ([1, 2] if ser_mode else [random.choice([1, 1, 2])])]
    else:
        # 非严肃完整构建: 与B站榜单阶段同时启动独立平台和关注投稿抓取。
        if tb_weight() > 0:
            tb_future = _FX.submit(fetch_tieba_pool, False)
        if int(sget("xhh_weight") or 15) > 0:
            xhh_future = _FX.submit(fetch_xhh_pool, False)
        if sget("bili_follow_emph") == "1":
            fol_future = _FX.submit(fetch_following_videos)
    cur_gids = []
    jobs = []
    for i, (name, gid, tpl, rid, pages) in enumerate(picked if not ser_mode else []):
        page = random.randint(0, pages - 1)          # 随机页: 打破顺序轮转造成的可预期重复
        url = tpl.format(rid=rid, p=page)
        shown = name if pages == 1 else "%s·第%d页" % (name, page)
        names.append(shown)
        cur_gids.append(gid)
        jobs.append(url)
    for shown, gid, (data, err) in zip(names[len(names) - len(picked):], cur_gids,
                                       _FX.map(_safe_json, jobs)):
        if data and data.get("code") == 0 and (data.get("data") or {}).get("list"):
            for it in data["data"]["list"]:
                if it.get("bvid") and it["bvid"] not in seen:
                    seen[it["bvid"]] = 1
                    item = normalize(it)
                    item["gid"] = gid   # 记录来源分组, 沉淀池按其过滤
                    item["src"] = shown   # 混排桶标记(榜单源按页)
                    if not item_domain_ok(item, enabled):   # 分区严格校验
                        continue
                    items.append(item)
            ok += 1
        else:
            errors.append("%s: %s" % (shown, (data or {}).get("code") or err))

    # --- 快速开屏: 消费与榜单同时发出的搜索词请求(见 quick 分支的提前提交) ---
    if quick:
        for kw, future in quick_search_futures:
            try:
                res = future.result() or []
            except Exception:
                res = []
            made = 0
            for x in res:
                if not item_domain_ok(x, enabled):
                    continue
                bvid = (x.get("url") or "").rsplit("/", 1)[-1]
                if bvid and bvid not in seen and len(items) < 450:
                    seen[bvid] = 1
                    x["src"] = "搜·" + kw[:6]   # 混排桶标记(按词)
                    items.append(x)
                    made += 1
            if made:
                names.append("搜索·%s×%d" % (kw, made))

    if ser_mode and not quick:
        # 严肃模式 fan-out 结果统一汇总: B站严肃词 + 每个允许吧的严肃词。
        for kind, label, future in serious_search_futures:
            try:
                result = future.result() or []
            except Exception:
                result = []
            made = 0
            for x in result:
                if kind == "tb":
                    if not tieba_bar_allowed(x) or not is_serious_ok(x):
                        continue
                    k = x.get("key") or x.get("url") or ""
                else:
                    if not is_serious_ok(x) or not item_domain_ok(x, enabled):
                        continue
                    k = (x.get("url") or "").rsplit("/", 1)[-1]
                if k and k not in seen and len(items) < 450:
                    seen[k] = 1
                    x["src"] = ("严·贴吧·" if kind == "tb" else "严·") + str(label)[:10]
                    items.append(x)
                    made += 1
            if made:
                names.append(("严肃贴吧·" if kind == "tb" else "严肃·") + str(label)[:10] + "×%d" % made)
        if local_serious_future:
            try:
                local_items = local_serious_future.result() or []
            except Exception:
                local_items = []
            made = 0
            for x in local_items:
                if x.get("platform") == "贴吧" and not tieba_bar_allowed(x):
                    continue
                if not is_serious_ok(x):
                    continue
                k = x.get("key") or x.get("url") or ""
                if k and k not in seen and len(items) < 450:
                    seen[k] = 1
                    x["src"] = "严·本地·" + str(x.get("platform") or "")
                    items.append(x)
                    made += 1
            if made:
                names.append("严肃本地×%d" % made)

    # --- 贴吧/小黑盒/关注的人: 放在 B站 相关链/搜索洪水之前抓取,
    #    避免被"相关链×300+"先灌满条目上限而整批丢弃(否则贴吧/小黑盒永远出不来)。
    #    quick 首屏也少量抓取非B站平台, 否则高权重平台只能等 full 补全后才出现。 ---
    if (not quick) or tb_weight() > 0 or int(sget("xhh_weight") or 15) > 0:
        if tb_future:
            tb_items, tb_errs = tb_future.result()
        else:
            tb_items, tb_errs = fetch_tieba_pool(quick=quick)
        for x in tb_items:
            if x["key"] not in seen and len(items) < 400:
                seen[x["key"]] = 1
                items.append(x)
        if tb_items:
            names.append("贴吧×%d" % len(tb_items))
        elif tb_errs and not quick:
            errors += tb_errs

        if xhh_future:
            xhh_items, xhh_errs = xhh_future.result()
        else:
            xhh_items, xhh_errs = fetch_xhh_pool(quick=quick)
        for x in xhh_items:
            if x["key"] not in seen and len(items) < 400:
                seen[x["key"]] = 1
                items.append(x)
        if xhh_items:
            names.append("小黑盒×%d" % len(xhh_items))
        elif xhh_errs and not quick:
            errors += xhh_errs

    if sget("bili_follow_emph") == "1":
        if not quick:
            if fol_future:
                fol_items, fol_err = fol_future.result()
            else:
                fol_items, fol_err = fetch_following_videos()
            for x in fol_items:
                if x["key"] in seen:
                    for existing in items:
                        if existing["key"] == x["key"]:
                            existing.update({"following": True, "author_mid": x.get("author_mid") or existing.get("author_mid") or 0,
                                             "src": "关注"})
                            break
                elif len(items) < 400:
                    seen[x["key"]] = 1
                    items.append(x)
            if fol_items:
                names.append("关注的人×%d" % len(fol_items))
            elif fol_err:
                errors.append("关注的人: " + fol_err)
        # quick 首屏不做同步关注列表扫描；已有UID缓存会即时标注，完整补池随后异步抓取投稿。

    # --- 相关推荐链: 种子 → 40相关 → 再开第二跳(每跳并行) ---
    # 产出率过低时整节跳过(相关链以老视频/重复居多, 常是"超时间窗"漏斗的最大来源);
    # 跳过时缓慢恢复通过率, 几轮后会重新尝试(防永久饿死)。
    if items and not quick and not ser_mode and not _src_yield_ok("related"):
        _src_yield_recover("related")
    if items and not quick and not ser_mode and _src_yield_ok("related"):
        max_age_related = int(sget("m_max_age_months") or 0)
        _rel_cut = (time.time() - max_age_related * 30.44 * 86400) if max_age_related else 0

        def _related_fresh(raw):
            # 抓回后先按发布时间预过滤, 老视频不再占池子名额与筛选预算。
            if not _rel_cut:
                return True
            return (raw.get("pubdate") or 0) >= _rel_cut

        related_n = 0
        for raw in _FX.map(fetch_related, pick_seeds(items)):
            if related_n >= 120:   # 相关链封顶, 防止单节灌满池子挤掉其他来源
                break
            for it in raw:
                if len(items) >= 450:
                    break
                if it.get("bvid") and it["bvid"] not in seen:
                    if not _related_fresh(it) or not item_domain_ok(it, enabled):   # 时间预过滤+分区白名单校验
                        continue
                    seen[it["bvid"]] = 1
                    item = normalize(it)
                    item["gid"] = "related"
                    item["tname"] = it.get("tname", "")
                    item["src"] = "相关"
                    items.append(item)
                    related_n += 1
        if related_n:
            hop2 = sorted([it for it in items if it.get("gid") == "related"],
                          key=lambda x: -x["views"])[:4]
            for raw in _FX.map(fetch_related, hop2):
                for it in raw:
                    if len(items) >= 450:
                        break
                    if it.get("bvid") and it["bvid"] not in seen:
                        if not _related_fresh(it) or not item_domain_ok(it, enabled):
                            continue
                        seen[it["bvid"]] = 1
                        item = normalize(it)
                        item["gid"] = "related"
                        item["tname"] = it.get("tname", "")
                        item["src"] = "相关"
                        items.append(item)
                        related_n += 1
            names.append("相关链×%d" % related_n)

    # --- 搜索词引擎: 词池(LRU) × 多页翻页 × 自学习淘汰(并行; 严肃模式由严肃引擎替代) ---
    if items and not quick and not ser_mode:
        ensure_kw_pool()
        kws = serious_kw_pool(enabled) if ser_mode else pick_kws(6)   # B站供给: 每轮6词(热榜跨构建重叠大, 靠搜索补新)
        _push = search_pushdown()
        jobs2 = [(kw, random.choice([1, 1, 2, 3])) for kw in kws]   # 随机页: 每词独立乱序
        for (kw, page), res in zip(jobs2, _FX.map(lambda a: search_items(a[0], a[1], order=_push.get("order"), duration=_push.get("duration")), jobs2)):
            keys = [x["key"] for x in res]
            if keys:
                ph = ",".join("?" * len(keys))
                known = db().execute("SELECT COUNT(*) FROM items WHERE key IN (%s)" % ph,
                                     tuple(keys)).fetchone()[0]
                new_n = max(0, len(keys) - known)
                with _db_lock:
                    db().execute("UPDATE kw SET last_used=?, new_cnt=new_cnt+?, ok_cnt=ok_cnt+1 WHERE kw=?",
                                 (int(time.time()), new_n, kw))
                    if new_n == 0:   # 连续不出新内容的词淘汰(自学习)
                        row = db().execute("SELECT ok_cnt FROM kw WHERE kw=?", (kw,)).fetchone()
                        if row and row[0] >= 3:
                            db().execute("DELETE FROM kw WHERE kw=?", (kw,))
                    db().commit()
            made = 0
            for x in res:
                if not item_domain_ok(x, enabled):   # 分区白名单(严格)
                    continue
                bvid = (x.get("url") or "").rsplit("/", 1)[-1]
                if bvid and bvid not in seen and len(items) < 450:
                    seen[bvid] = 1
                    items.append(x)
                    made += 1
            if made:
                names.append("搜索·%s×%d" % (kw, made))

    # --- 严肃模式搜索已在函数开头通过 _SERIOUS_X fan-out 并行完成 ---
    if False and not quick and len(items) < 420 and ser_mode:
        terms = serious_kw_pool(enabled)[:10]
        jobs_ser = []
        for i, kw in enumerate(terms):
            jobs_ser.append((kw, 1))
            if i < 4:
                jobs_ser.append((kw, 2))
        for (kw, page), res in zip(jobs_ser, _FX.map(lambda a: search_items(a[0], a[1]), jobs_ser)):
            made = 0
            for x in res:
                if not is_serious_ok(x):
                    continue
                if not item_domain_ok(x, enabled):
                    continue
                bvid = (x.get("url") or "").rsplit("/", 1)[-1]
                if bvid and bvid not in seen and len(items) < 450:
                    seen[bvid] = 1
                    x["src"] = "严·" + kw[:6]
                    items.append(x)
                    made += 1
            if made:
                names.append("严肃·%s×%d" % (kw, made))

    # --- 随机采样: 分区专属词库 × 随机深页 ---
    if not quick and len(items) < 420 and not ser_mode:
        for _ in range(2):
            allw = serious_kw_pool(enabled) if ser_mode else random_word_candidates(enabled)
            candidates = [w for w in allw if w not in _recent_rand]
            if not candidates:
                while _recent_rand:
                    _recent_rand.pop()
                candidates = allw
            kw = random.choice(candidates)
            _recent_rand.append(kw)
            if len(_recent_rand) > 6:
                _recent_rand.pop(0)
            page = random.choice([1, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15])
            made = 0
            for x in search_items(kw, page, **search_pushdown()):
                if not item_domain_ok(x, enabled):   # 分区白名单(严格)
                    continue
                bvid = (x.get("url") or "").rsplit("/", 1)[-1]
                if bvid and bvid not in seen and len(items) < 450:
                    seen[bvid] = 1
                    x["src"] = "随·" + kw[:6]   # 混排桶标记(随机词)
                    items.append(x)
                    made += 1
            if made:
                names.append("随机·%s×%d" % (kw, made))

    # --- 偏好词引擎: 把偏好备注里的具体主题直接搜进信息流(用户"我偏好的怎么刷不到"根因) ---
    if not quick and len(items) < 420:
        prefs = pref_keywords(enabled)
        if prefs:
            pick = prefs[:2]   # 每轮最多搜2个偏好词, 多轮刷新时轮换
            _push = search_pushdown()
            for kw, res in zip(pick, _FX.map(lambda a: search_items(a, 1, order=_push.get("order"), duration=_push.get("duration")), pick)):
                made = 0
                for x in res:
                    if ser_mode and not is_serious_ok(x):   # 严肃模式下偏好词同样过严肃判定
                        continue
                    if not item_domain_ok(x, enabled):   # 分区白名单(严格)
                        continue
                    bvid = (x.get("url") or "").rsplit("/", 1)[-1]
                    if bvid and bvid not in seen and len(items) < 450:
                        seen[bvid] = 1
                        x["src"] = "偏·" + kw[:6]   # 混排桶标记(偏好词)
                        items.append(x)
                        made += 1
                if made:
                    names.append("偏好·%s×%d" % (kw, made))
        if prefs:
            _pref_cursor += 2   # 下轮换下两个偏好词

    # --- 严肃模式旧串行深挖已由上方并行搜索替代, 保留代码路径关闭以便回滚参考 ---
    if False and not quick and len(items) < 420 and ser_mode:
        def _dig_one(kw):
            got = 0
            for pg in range(1, 3):   # 最多2页(单页30条, 60条原始够; 减少full补全耗时)
                try:
                    res = search_items(kw, pg)
                except Exception:
                    break
                fresh = 0
                for x in res:
                    if not is_serious_ok(x):
                        continue
                    if not item_domain_ok(x, enabled):
                        continue
                    bvid = (x.get("url") or "").rsplit("/", 1)[-1]
                    if bvid and bvid not in seen and len(items) < 450:
                        seen[bvid] = 1
                        x["src"] = "严·" + kw[:6]
                        items.append(x)
                        fresh += 1
                got += fresh
                if fresh == 0:
                    break   # 该词到底
                if got >= 20:
                    break   # 该词够20条, 换其他词(更快覆盖更多词)
                time.sleep(0.15)
            return kw, got

        # 8并发深挖; 每批5词, 最多两批(限时, 不让补全拖太久); 单词20条达标即换词
        for batch in range(2):
            pool_kws = serious_kw_pool(enabled)
            if not pool_kws:
                break
            for kw, got in _FX.map(lambda a: _dig_one(a), pool_kws[:5]):
                if got:
                    names.append("严肃·%s×%d" % (kw, got))
            _pref_cursor += 5
            if len(items) >= 300 or batch >= 1:
                break

    _fetch_count += 1
    if not items:
        return {"ok": False, "offline": True, "source": "内置示例(离线)",
                "items": MOCK_ITEMS, "error": "; ".join(errors)}
    return {"ok": True, "offline": False, "source": " + ".join(names),
            "items": items[:400], "error": ""}


def purge_items_by_parts(enabled):
    """分区切换后清理不匹配的沉淀池条目(按 json 内 gid/分区属性判断)。返回清理数。
    贴吧条目豁免(独立平台, 不随B站分区)。"""
    if not _subset_mode(enabled):
        return 0
    keys = []
    for (k, j) in db().execute("SELECT key, json FROM items").fetchall():
        try:
            it = json.loads(j)
        except Exception:
            continue
        if it.get("platform") != "B站":
            continue   # 非B站平台(贴吧/小黑盒等)不清
        if not item_domain_ok(it, enabled):
            keys.append(k)
    if keys:
        with _db_lock:
            db().executemany("DELETE FROM items WHERE key=?", [(k,) for k in keys])
            db().commit()
    return len(keys)


def get_feed_raw(force=False):
    now = time.time()
    if force or _feed_cache["payload"] is None or now - _feed_cache["ts"] > CACHE_TTL:
        payload = fetch_pool()
        payload["updated"] = int(now)
        _feed_cache.update(ts=now, payload=payload)
        sset("fetch_last", int(now))
        sset("fetch_error", payload.get("error", ""))
        if not payload["offline"]:
            with _db_lock:
                for it in payload["items"]:
                    old = db().execute("SELECT json FROM items WHERE key=?", (it["key"],)).fetchone()
                    if old:
                        try:
                            prev = json.loads(old[0])
                        except Exception:
                            prev = {}
                    db().execute("INSERT INTO items(key,json,fetched_at,score_at) VALUES(?,?,?,0) "
                                 "ON CONFLICT(key) DO UPDATE SET json=excluded.json,fetched_at=excluded.fetched_at",
                                 (it["key"], json.dumps(it, ensure_ascii=False), now))
                db().execute("DELETE FROM items WHERE fetched_at < ?", (now - 7 * 86400,))  # 只留7天历史
                db().commit()
    return _feed_cache["payload"]


def backlog_items(exclude_keys, enabled_ids, limit=100):
    """历史沉淀池: 之前几轮抓过、但当前新鲜页没有的条目(严格按分区开关过滤)。
    弱化: 只少量回流(100条), 避免历史内容反复占满首页导致信息重复率高。"""
    seen = set(exclude_keys)
    out = []
    for (j,) in db().execute(
            "SELECT json FROM items ORDER BY fetched_at DESC LIMIT 600").fetchall():
        if len(out) >= limit:
            break
        try:
            it = json.loads(j)
        except Exception:
            continue
        if it.get("key") in seen:
            continue
        if not item_domain_ok(it, enabled_ids):
            continue   # 统一分区校验: 旧池里不属于当前分区的全部不会回流
        seen.add(it["key"])
        out.append(it)
    return out


# ---------------------------------------------------------------- AI 模块 (OpenAI 兼容)

def ai_ok():
    """是否已配置 API Key(总开关关闭不影响此判断, 用于状态显示)。"""
    return bool(sget("ai_key").strip())


def ai_on():
    """是否真正使用 AI: 总开关(ai_enabled) + 已配置 Key。"""
    return sget("ai_enabled") != "0" and ai_ok()


def record_usage(usage):
    if not usage:
        return
    try:
        with _db_lock:
            # IFNULL: 行可能由 bump_stats 先建(NULL), 直接相加会永远 NULL
            db().execute("INSERT INTO stats(day,in_tokens,out_tokens,calls) VALUES(?,?,?,1) "
                         "ON CONFLICT(day) DO UPDATE SET "
                         "in_tokens=IFNULL(in_tokens,0)+excluded.in_tokens,"
                         "out_tokens=IFNULL(out_tokens,0)+excluded.out_tokens,"
                         "calls=IFNULL(calls,0)+1",
                         (today(), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))
            db().commit()
    except Exception:
        pass   # 统计失败不影响聊天; 下次调用继续累计


def llm_body(messages, max_tokens, temperature=0.3):
    # 注意: 非流式请求**不能**带 "stream": False 字段——v4-flash 会返回空content/finish=length(bug)
    return json.dumps({
        "model": sget("ai_model"), "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
    }).encode("utf-8")


def call_llm(messages, max_tokens=1200):
    """调用 OpenAI 兼容接口, 返回 (content, usage) 或抛异常。"""
    body = llm_body(messages, max_tokens)
    req = urllib.request.Request(
        sget("ai_base").rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % sget("ai_key")})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    usage = data.get("usage") or {}
    record_usage(usage)
    return data["choices"][0]["message"]["content"], usage


def llm_stream(messages, max_tokens=1200, on_reasoning=None):
    """流式调用: 逐段 yield 增量文本; 推理模型(如 deepseek-v4-flash)先出
    reasoning_content 再出 content——思考期间通过 on_reasoning 回调通知,
    思考字节忽略(否则 max_tokens 会被思考吃光导致 content 为空)。"""
    body = json.dumps({
        "model": sget("ai_model"), "messages": messages,
        "max_tokens": max_tokens, "temperature": 0.3, "stream": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        sget("ai_base").rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % sget("ai_key"),
                 "Accept": "text/event-stream"})
    usage = {}
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("reasoning_content") and on_reasoning:
                on_reasoning(delta["reasoning_content"])
            c = delta.get("content")
            if c:
                yield c
    record_usage(usage)


def user_context():
    """用户偏好上下文: 偏好备注 + 聊天中的意愿 + 反馈摘要。"""
    parts = []
    note = sget("prefs_note").strip()
    if note:
        parts.append("用户偏好备注: " + note)
    dislikes = sget("dislike_note").strip()
    if dislikes:
        parts.append("用户厌恶/不想看的内容: " + dislikes)
    rows = db().execute(
        "SELECT content FROM chat WHERE role='user' AND cid=? ORDER BY id DESC LIMIT 5",
        (conv_cur(),)).fetchall()
    if rows:
        parts.append("用户近期的聊天意愿: " + " / ".join(r[0][:60] for r in reversed(rows)))
    likes = db().execute("SELECT author FROM feedback WHERE action='like' GROUP BY author ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
    dislikes = db().execute("SELECT author FROM feedback WHERE action='dislike' GROUP BY author ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
    if likes:
        parts.append("用户喜欢的作者: " + ", ".join(r[0] for r in likes))
    if dislikes:
        parts.append("用户不喜欢的作者: " + ", ".join(r[0] for r in dislikes))
    try:   # 隐式信号(感知层): 近期点击内容的主题/标签/分区
        hrows = db().execute("SELECT key FROM history ORDER BY ts DESC LIMIT 30").fetchall()
        hitems = [item_by_key(k) for (k,) in hrows]
        hitems = [it for it in hitems if isinstance(it, dict)]
        if hitems:
            topts = []
            for it in hitems:
                for t in ((it.get("topic") or "").split("、") + (it.get("tag") or "").split(",")
                          + [it.get("area") or "", it.get("tname") or ""]):
                    t = t.strip()
                    if 2 <= len(t) <= 8 and t not in topts:
                        topts.append(t)
            if topts:
                parts.append("用户最近点击内容的主题(隐式兴趣): " + "、".join(topts[:15]))
    except Exception:
        pass
    acct = account_slim()
    if acct.get("ok"):
        if acct["authors"]:
            parts.append("B站账号常看作者: " + ", ".join(a["name"] for a in acct["authors"][:6]))
        if acct["keywords"]:
            parts.append("B站账号历史高频词: " + ", ".join(acct["keywords"][:8]))
        if acct["favs"]:
            parts.append("B站账号收藏夹: " + "、".join(f["title"] for f in acct["favs"][:3]))
    return "\n".join(parts)


# ---------------------------------------------------------------- B站账号(登录后优化推荐)

ACCOUNT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}
STOPWORDS = set("""这个 那个 什么 怎么 为什么 如何 一个 不是 就是 还是 但是 因为 所以 如果 然后 以及
可以 自己 我们 他们 你们 现在 没有 已经 真的 虽然 不过 可能 应该 觉得 知道 看到 都是 都是 我是 爱你 喜欢
视频 动画 游戏 电影 系列 官方 完整 实况 合集 盘点 评测 试玩 解说 攻略 教程 第 第几 原创 转载 资讯 新闻 进 了 过 吧
日常 一天 最后 最新 神作 双人 推荐 高分 短评 剧集 片花 预告 正片 花絮 特辑 合集 未删减 高清 中字 生肉""".split())


def account_cookie():
    return sget("bili_cookie")


def bili_get(url):
    req = urllib.request.Request(url, headers=dict(ACCOUNT_HEADERS, Cookie=account_cookie()))
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))


def bili_is_logged_in():
    """用B站认证接口判断当前Cookie是否有效。"""
    if not account_cookie():
        return False
    try:
        nav = bili_get("https://api.bilibili.com/x/web-interface/nav")
        return nav.get("code") == 0 and bool((nav.get("data") or {}).get("isLogin"))
    except Exception:
        return False


def bili_unread_messages():
    """读取B站账号真实未读消息数; 接口失败或未登录时返回None。"""
    if not bili_is_logged_in():
        return None
    try:
        d = bili_get("https://api.bilibili.com/x/msgfeed/unread")
        if d.get("code") != 0:
            return None
        data = d.get("data") or {}
        # 只统计评论/回复与@我，不将点赞、系统通知或关注动态计入消息红点。
        keys = ("reply", "at")
        values = [data.get(k) for k in keys if isinstance(data.get(k), (int, float))]
        return max(0, int(sum(values))) if values else 0
    except Exception:
        return None


def account_slim():
    """返回给前端/评分用的账号画像摘要(带缓存)。"""
    try:
        profile = json.loads(sget("acct_profile"))
    except Exception:
        profile = None
    if not profile:
        return {"ok": False, "nickname": sget("acct_nickname"), "profile": None,
                "authors": [], "keywords": [], "favs": [], "history_n": 0, "face": ""}
    return {"ok": True, "nickname": profile.get("nickname", ""),
            "synced_at": profile.get("synced_at", 0),
            "authors": profile.get("authors", []),
            "keywords": profile.get("keywords", []),
            "favs": profile.get("favs", []),
            "history_n": profile.get("history_n", 0),
            "face": profile.get("face", "")}


def fetch_account_profile():
    """拉取观看历史/收藏夹 → 统计常看作者与高频关键词。失败抛异常。"""
    nav = bili_get("https://api.bilibili.com/x/web-interface/nav")
    if nav.get("code") != 0 or not (nav.get("data") or {}).get("isLogin"):
        raise RuntimeError("Cookie 无效或已过期，请重新登录")
    data = nav["data"]
    nickname, mid = data.get("uname", ""), data.get("mid", 0)

    authors, titles = [], []
    try:
        h = bili_get("https://api.bilibili.com/x/web-interface/history/cursor?ps=30&type=video")
        for it in (h.get("data") or {}).get("list") or []:
            a = it.get("author_name") or ((it.get("owner") or {}) or {}).get("name", "")
            if a:
                authors.append(a)
            if it.get("title"):
                titles.append(it["title"])
    except Exception:
        pass

    favs = []
    try:
        f = bili_get("https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid=%d" % mid)
        for fo in (f.get("data") or {}).get("list") or []:
            favs.append({"title": fo.get("title", ""), "count": fo.get("media_count", 0)})
        if favs:
            r = bili_get("https://api.bilibili.com/x/v3/fav/resource/list?media_id=%s&pn=1&ps=20&platform=web"
                         % favs[0].get("id", ""))
            for m in ((r.get("data") or {}).get("medias") or [])[:20]:
                if m.get("title"):
                    titles.append(m["title"])
    except Exception:
        pass

    auth_cnt = {}
    for a in authors:
        auth_cnt[a] = auth_cnt.get(a, 0) + 1
    top_authors = [{"name": k, "count": v} for k, v in
                   sorted(auth_cnt.items(), key=lambda x: -x[1])[:8]]
    kw_cnt = {}
    for t in titles:
        for tok in re.split(r"[\s:：|丨_·\-~\[\]【】()（）,，。！？!?\"'“”]+", t):
            tok = tok.strip()
            if 2 <= len(tok) <= 12 and tok not in STOPWORDS:
                kw_cnt[tok] = kw_cnt.get(tok, 0) + 1
    top_kw = [k for k, _ in sorted(kw_cnt.items(), key=lambda x: -x[1])[:12]]

    profile = {"nickname": nickname, "mid": mid, "synced_at": int(time.time()),
               "history_n": len(authors), "authors": top_authors,
               "keywords": top_kw, "favs": favs[:5],
               "face": data.get("face", "")}
    sset("acct_profile", json.dumps(profile, ensure_ascii=False))
    sset("acct_nickname", nickname)
    return profile


def fetch_account_blocklist():
    """读取 B站账号拉黑名单 → 写入本地 ablock 表。返回数量。"""
    d = bili_get("https://api.bilibili.com/x/relation/blacks")
    if d.get("code") != 0:
        if d.get("code") == -101:
            raise RuntimeError("未登录B站账号，无法同步拉黑名单")
        raise RuntimeError("拉黑名单读取失败(code=%s)" % d.get("code"))
    items = (d.get("data") or {}).get("list") or []
    names = [it.get("uname") or it.get("name") for it in items]
    names = [n for n in names if n]
    with _db_lock:
        for n in names:
            db().execute("INSERT OR REPLACE INTO ablock(name, ts) VALUES(?,?)",
                         (n, int(time.time())))
        db().commit()
    return len(names)


def maybe_sync_blocklist():
    """登录后自动同步拉黑名单(仅当设置开启时)。"""
    if sget("m_blocklist_sync") != "1":
        return
    try:
        fetch_account_blocklist()
    except Exception:
        pass


def _norm_space(it, author_mid=0):
    """B站空间投稿 vlist → 统一条目格式(扁平结构, 与推荐接口不同的字段名)。标记 following。"""
    dur = it.get("length") or "0:0"
    try:
        parts = [int(x) for x in str(dur).split(":")]
        dsec = parts[-1] + (parts[-2] * 60 if len(parts) >= 2 else 0)
    except Exception:
        dsec = 0
    pic = it.get("pic") or ""
    if pic.startswith("//"):
        pic = "https:" + pic
    else:
        pic = pic.replace("http://", "https://")
    pubdate = it.get("pubdate") or it.get("created") or 0
    try:
        pubdate = int(pubdate)
        if pubdate > 10 ** 12:      # 毫秒时间戳 → 秒
            pubdate //= 1000
        if pubdate < 0:
            pubdate = 0
    except Exception:
        pubdate = 0
    return {
        "key": "https://www.bilibili.com/video/%s" % it.get("bvid", ""),
        "platform": "B站",
        "title": it.get("title", ""),
        "author": it.get("author", ""),
        "author_mid": author_mid or it.get("mid") or 0,
        "desc": (it.get("description") or "")[:500],
        "cover": pic,
        "views": it.get("play", 0),
        "likes": it.get("like", 0),
        "danmaku": it.get("video_review", 0),
        "reply": it.get("comment", 0),
        "duration": dsec,
        "pubdate": pubdate,
        "url": "https://www.bilibili.com/video/%s" % it.get("bvid", ""),
        "tname": it.get("typename", ""),
        "area": "",
        "tag": "",
        "following": True,   # 关注的人强调标记(前端视觉强调 + 后续豁免机器过滤)
        "src": "关注",
    }


# 关注的人身份缓存: UID 是权威标识; 昵称仅用于旧缓存条目的兼容回退。
_following_names_cache = {"ts": 0, "names": set(), "mids": set()}
_follow_lock = threading.Lock()


def _set_following_identities(names, mids):
    names = {n for n in (names or []) if n}
    mids = {str(m) for m in (mids or []) if str(m).isdigit() and int(m) > 0}
    with _follow_lock:
        _following_names_cache.update(ts=time.time(), names=names, mids=mids)
    try:
        sset("bili_follow_names", json.dumps(sorted(names), ensure_ascii=False))
        sset("bili_follow_mids", json.dumps(sorted(mids)))
    except Exception:
        pass


def following_identities():
    """当前关注UP的 UID 集合与旧昵称集合；UID 匹配优先。"""
    with _follow_lock:
        if time.time() - _following_names_cache["ts"] < 900 and (
                _following_names_cache["mids"] or _following_names_cache["names"]):
            return _following_names_cache["mids"], _following_names_cache["names"]
    try:
        names = set(json.loads(sget("bili_follow_names") or "[]"))
    except Exception:
        names = set()
    try:
        mids = {str(m) for m in json.loads(sget("bili_follow_mids") or "[]") if str(m).isdigit() and int(m) > 0}
    except Exception:
        mids = set()
    if mids or names:
        with _follow_lock:
            _following_names_cache.update(ts=time.time(), names=names, mids=mids)
    return mids, names


def following_names():
    """兼容旧调用方: 返回缓存的关注昵称集合。"""
    return following_identities()[1]


def _annotate_following(items):
    """按UP UID标记关注视频；旧条目缺UID时才退回昵称匹配。"""
    if sget("bili_follow_emph") != "1":
        return
    mids, names = following_identities()
    if not (mids or names) and account_cookie():
        try:
            threading.Thread(target=fetch_following_videos, kwargs={"total_cap": 1}, daemon=True).start()
        except Exception:
            pass
        mids, names = following_identities()
    if not (mids or names):
        return
    months = int(sget("bili_follow_months") or 1)
    cut = 0 if months <= 0 else time.time() - months * 30.44 * 86400
    for it in items:
        if it.get("platform") != "B站":
            continue
        mid = str(it.get("author_mid") or "")
        is_following = mid in mids if mid else bool(it.get("author") and it.get("author") in names)
        if not is_following or not it.get("pubdate") or (cut and it["pubdate"] < cut):
            it.pop("following", None)
            continue
        it["following"] = True


def _space_videos(mid, ps=30):
    """B站 up 空间投稿(按发布时间), wbi 签名; 失败返回空列表。"""
    try:
        qs = wbi_enc({"mid": mid, "pn": 1, "ps": ps, "order": "pubdate"})
        d = bili_get("https://api.bilibili.com/x/space/wbi/arc/search?" + qs)
        if d.get("code") != 0:
            return []
        return ((d.get("data") or {}).get("list") or {}).get("vlist") or []
    except Exception:
        return []


def fetch_following_videos(max_up=20, per_up=30, total_cap=60):
    """抓取「关注的人」投稿入池(强调功能)。返回 (items, err)，失败不抛异常。
    语义: 只保留「强调月数内发布」的关注视频并标 following=True(强调); 超期/无发布时间的一律丢弃,
    不进池。bili_follow_months(0=不限). 未登录/风控 → 空。"""
    if not account_cookie():
        return [], "未登录B站账号"
    try:
        nav = bili_get("https://api.bilibili.com/x/web-interface/nav")
        if nav.get("code") != 0 or not (nav.get("data") or {}).get("isLogin"):
            return [], "未登录或Cookie失效"
        mid = (nav.get("data") or {}).get("mid", 0)
        months = int(sget("bili_follow_months") or 1)
        cut = 0 if months <= 0 else time.time() - months * 30.44 * 86400
    except Exception as e:
        return [], str(e)[:80]
    # 关注列表身份全集(分页): UID 用于标注所有正常池候选；仅前 max_up 位再抓投稿入池。
    ups, names, mids = [], set(), set()
    try:
        for pn in range(1, 21):
            d = bili_get("https://api.bilibili.com/x/relation/followings?vmid=%d&pn=%d&ps=50" % (mid, pn))
            if d.get("code") != 0:
                return [], "关注列表获取失败(code=%s)" % d.get("code")
            rows = (d.get("data") or {}).get("list") or []
            for it in rows:
                if it.get("mid"):
                    ups.append((it["mid"], it.get("uname", "")))
                    mids.add(str(it["mid"]))
                    if it.get("uname"):
                        names.add(it["uname"])
            if len(rows) < 50:
                break
    except Exception as e:
        return [], str(e)[:80]
    _set_following_identities(names, mids)   # 缓存UID供正常视频稳定标注，昵称仅兼容旧条目
    if not ups:
        return [], ""
    ups = ups[:max_up]
    items, seen = [], set()
    try:
        for (umid, _uname), d in zip(ups, _FX.map(
                lambda u: _space_videos(u[0], per_up), ups)):
            for raw in (d or []):
                it = _norm_space(raw, umid)
                if it["key"] in seen:
                    continue
                seen.add(it["key"])
                if not it["pubdate"]:
                    continue   # 无发布时间 → 无法判断, 直接丢弃
                if cut and it["pubdate"] < cut:
                    continue   # 超出强调月数(如1个月) → 直接丢弃, 不进池
                it["following"] = True
                items.append(it)   # 符合月数 → 入池并标 following=True(强调)
    except Exception as e:
        return items, str(e)[:80]
    items.sort(key=lambda x: -x["pubdate"])
    return items[:total_cap], ""


def parse_scores(content):
    """从 LLM 输出中提取评分数组, 容错处理。"""
    try:
        data = json.loads(content)
    except Exception:
        m = re_search_json(content)
        data = json.loads(m.group(0)) if m else None
    if isinstance(data, dict):
        data = data.get("items", [])
    return data if isinstance(data, list) else []


def re_search_json(text):
    import re
    for m in re.finditer(r"\[.*?\]|\{.*?\}", text, re.S):
        try:
            return json.loads(m.group(0)) or None
        except Exception:
            continue
    return None


def score_ttl():
    """评分缓存时长(秒): 省钱法 48h。"""
    return int(sget("ai_cache_hours") or 24) * 3600


def clear_score_cache():
    """清评分缓存(保留内容 json): 画像类规则(厌恶/偏好备注)变更后强制重评, 避免旧语义分数过时。"""
    with _db_lock:
        db().execute("UPDATE items SET score_json='', score_at=0")
        db().commit()


def score_batch():
    """每批评分条目数(省钱法 30/批, 省一半请求)。"""
    n = int(sget("ai_batch") or 15)
    return 30 if n == 30 else 15


SCORER_SKILL = """
【内容筛选技能 v1】你是一名专业内容筛选员，为「洞鉴虚明」工作。你的唯一任务：对每条内容给出质量与匹配度判断，供系统决定是否推送给用户。

【内容形态】条目可能来自两个平台，评分口径略有差异：
- B站视频(platform=B站): 视频内容, 判据照常(标题/作者/简介)。
- 贴吧帖子(platform=贴吧): 文字论坛帖, title=帖子标题, desc=首楼摘要, author=楼主。
  引战分判据: 对线/吵架/挂人/带节奏; 低质分判据: 广告帖/纯水贴/无信息量搬运/引流帖;
  兴趣分判据: 主题是否与用户画像相关(教程/知识/军事解析/数码等), 水贴闲聊给低分。

【评分维度】每条内容必须给出全部 8 项：
1. flame 引战分 0-10：内容挑起对立/拉踩/煽动情绪/恶意碰瓷的程度。判据：地域性别等对立话题、引战标题("XX都看不下去了")、评论区天天吵架的选题、明知争议还要拱火。0=完全平和，10=最恶劣的挑事内容。
2. spam 低质分 0-10：营销广告/无信息量搬运/粗制滥造/货不对板程度。判据：大量水军营销号、AI生成图片配空洞文案骗点击、标题说A内容讲B、纯水时长拼凑、赤裸推销。0=制作认真，10=纯垃圾。
3. interest 兴趣分 0-10：与"用户信息"中画像的匹配度。判据：主题相关、作者熟悉、玩法/内容形式符合偏好；用户明显不感兴趣的主题给低分。无法判断时给 5。
4. density 信息密度 0-10：能否用一句话概括全部有效信息。判据：废话/注水/车轱辘话/空镜凑时长越多分越低；教程干货、数据密集内容高分。0=全是废话，10=句句有信息。
5. emo 情绪净值 -10~10：正数=建设性/正向价值(即使讲坏事，给了解决方案或深度剖析也给正分)，负数=纯负能量/煽动焦虑/贩卖恐慌。0=中性陈述。判据：讲问题但给方法→正；只输出情绪、制造对立焦虑、贩卖恐慌→负。
6. facts 事实锚点 0/1：资讯/评论/事件类内容是否包含具体数据、引用来源、时间地点等可验证锚点。有任一锚点=1；纯情绪输出/空泛观点=0。非资讯类(教程/娱乐/生活)默认1，不算错。
7. topic 主题标签：2-6 字中文，如"独立游戏""数码评测""二次元"。用具体内容概括，不要用"综合""其他"。
8. reason 推荐理由：一句 20-40 字，像朋友安利那样自然口语。要点：说出具体亮点(玩法机制/内容质量/独特卖点/强情绪点)，可结合用户画像针对性推荐，**禁止**负面词汇(别/不/差/烂/争议)，**禁止**空泛套话(推荐/值得一看/不错)。示例："评测只讲干货没废话，上手门槛说得明明白白""这作者考据特别细，你爱看三国应该会喜欢"。

【判定原则】
- 引战与低质是"一票否决"级: flame≥8 或 spam≥8 必须标出; 6-7 属于"边缘", 若与用户画像无关也标出。
- **厌恶是最高优先级**: "用户信息"中的厌恶清单一经语义命中(不要字面匹配, 要理解内容是否属于该类别),
  强制 interest=0 且 topic 加"回避-"前缀(如"回避-二次元游戏")。语义相近但非同类不要误伤
  (如厌恶"二次元游戏"时, 正常的单机/3A大作/电竞内容照常评分; 厌恶"流量明星八卦"时, 正常娱乐向但不炒作的也不误伤)。
- 不冤枉好内容: 有态度但不引战、有观点但不拉踩的内容, flame 给 3-4。
- 系列作品/同一UP内容: 若用户在追该系列, interest 加成。
- 信息不足(纯图片/无简介)给中性分: flame 3, spam 3, interest 5, topic "综合"。
- 每条都必须有结果, 与输入一一对应, 顺序一致。

【输出】只输出 JSON, 不要任何其他文字/注释/代码块围栏:
{"items":[{"key":"...","flame":0,"spam":0,"density":8,"emo":3,"facts":1,"topic":"主题","interest":0,"reason":"..."}]}
""".strip()


_score_lock = threading.Lock()   # 评分全局串行: 并发请求共享同一批评分结果, 不重复烧钱


def _load_cache(items, ttl):
    """从 items 表读取评分缓存, 返回 (cached, pending)。"""
    if not items:
        return {}, []   # 空列表: 防生成 "key IN ()" 非法SQL
    with _db_lock:
        rows = {r[0]: (r[1], r[2]) for r in db().execute(
            "SELECT key,score_json,score_at FROM items WHERE key IN (%s)"
            % ",".join("?" * len(items)), tuple(i["key"] for i in items)).fetchall()}
    now = time.time()
    cached, pending = {}, []
    for it in items:
        scr = rows.get(it["key"])
        if scr and scr[0] and scr[1] and now - scr[1] < ttl:
            cached[it["key"]] = json.loads(scr[0])
        else:
            pending.append(it)
    return cached, pending


def _write_scores(cached):
    global _score_epoch
    with _db_lock:
        for key, sc in cached.items():
            # upsert: 条目不在 items 表时也写入评分缓存(防并发去重失效); json 列仅在新建时用空占位,
            # 真实条目由构建流程 INSERT 覆盖, 已有条目不触碰 json
            db().execute(
                "INSERT INTO items(key,json,fetched_at,score_at,score_json) VALUES(?,?,0,?,?) "
                "ON CONFLICT(key) DO UPDATE SET score_json=excluded.score_json, score_at=excluded.score_at",
                (key, "{}", int(time.time()), json.dumps(sc, ensure_ascii=False)))
        db().commit()
    _score_epoch += 1   # 新评分落库 → 套餐规划缓存失效(全扫模式下新筛内容渐进可见)


def score_items(items, only_cache=False, chunk_size=None):
    """为条目打分(带缓存)。返回 {key: score}。失败不影响主流程。
    only_cache=True: 只读缓存不调LLM(供bake快速过滤用), 未缓存条目后续由serve门禁补评。
    chunk_size: 每批条目数(缺省=score_batch); 首屏用小批可更快出分。
    并发说明: pending 评分段用 _score_lock 串行, 排队线程重查缓存直接复用结果,
    —— 多个并发 feed 请求(用户页面多开/滚动)不会重复评分同一批条目。"""
    ttl = score_ttl()
    cached, pending = _load_cache(items, ttl)
    if pending and ai_on() and not only_cache:
        with _score_lock:
            # 排队线程可能在等待期间已评完 → 重查缓存, 避免重复烧钱
            cached2, pending = _load_cache(items, ttl)
            cached = dict(cached2) or cached
            if not pending:
                _write_scores(cached)
                return cached
            ctx = user_context() or "(无)"
            batch = chunk_size or score_batch()
            chunks = [pending[i:i + batch] for i in range(0, len(pending), batch)]
            _score_progress.update(total=len(pending), done=len(cached), ts=int(time.time()))

            def _score_chunk(chunk):
                last_err = None
                for attempt in range(3):   # 限流/网络抖动重试
                    try:
                        content, _ = call_llm([
                            {"role": "system", "content": SCORER_SKILL + "\n\n【用户信息】\n" + ctx},
                            {"role": "user", "content": json.dumps(
                                {"items": [{"key": it["key"], "platform": it.get("platform", "B站"),
                                            "title": it["title"][:60],
                                            "author": it["author"][:20],
                                            "desc": it["desc"][:60]} for it in chunk]},
                                ensure_ascii=False)},
                        ], max_tokens=8192)
                        parsed = parse_scores(content)
                        if parsed:
                            return parsed
                        last_err = "解析失败(" + str(len(content)) + "字)"
                    except Exception as e:
                        last_err = str(e)[:100]
                    time.sleep(1.5 * (attempt + 1))
                with _db_lock:
                    _score_progress["last_err"] = last_err
                return []

            for sc_list in _SCORE_X.map(_score_chunk, chunks):   # 3并发评分(防限流)
                for sc in sc_list:
                    if isinstance(sc, dict) and sc.get("key"):
                        cached[sc["key"]] = sc
                with _db_lock:   # 进度(线程安全)
                    _score_progress["done"] = min(_score_progress["total"],
                                                  _score_progress["done"] + len(sc_list))
            _write_scores(cached)   # 锁内写库, 后续线程重查即可命中
            return cached
    _write_scores(cached)   # 仅缓存命中或 only_cache
    return cached


# --- 后台渐进评分: 让交互路径(只读缓存)秒回, 评分在后台逐步推进, 避免阻塞 HTTP 请求 ---
# 根因: serve/search 门禁原先用 score_items(chunk) 同步调 LLM, 首次筛查/刷新命中未评分条目
# 会阻塞 30-60s(表现为"获取太长时间/刷新卡住"); 改为只读缓存后, 由本守护线程预评分写库,
# 前端刷新时渐进命中缓存, 首屏秒回, 未评分条目按门禁暂时不展示(不误判"已到底").
_score_bg_q = collections.deque()      # 待评分条目队列(iterable of item dicts)
_score_bg_seen = set()                 # 已入队 key(防重复入队; 失败保留避免无限重评)
_score_bg_lock = threading.Lock()
_score_bg_started = False
_score_bg_gen = 0                      # 世代计数: universe 重建时清掉旧队列


def _needs_score(it, rows, now, ttl):
    """条目是否需要评分: 非 B站(无评分语义)/已有未过期缓存/已被本次入队 → False。"""
    if (it.get("platform") or "B站") != "B站":
        return False
    key = it["key"]
    if key in _score_bg_seen:
        return False
    sat = rows.get(key)
    if sat and now - sat < ttl:
        return False
    return True


def _request_bg_score(items):
    """把需要评分的候选入队, 由后台线程渐进评分写库。只处理 B站(其他平台不过 AI 门禁)。"""
    if not (ai_on() and sget("ai_full_scan") == "1"):
        return
    keys = [it["key"] for it in items if (it.get("platform") or "B站") == "B站"]
    if not keys:
        return
    ttl = score_ttl()
    now = time.time()
    try:
        with _db_lock:
            rows = {r[0]: r[1] for r in db().execute(
                "SELECT key,score_at FROM items WHERE key IN (%s)"
                % ",".join("?" * len(keys)), tuple(keys)).fetchall()}
    except Exception:
        rows = {}
    with _score_bg_lock:
        for it in items:
            if _needs_score(it, rows, now, ttl):
                _score_bg_seen.add(it["key"])
                _score_bg_q.append(it)


_bg_cold = True   # 评分冷启动: 首批用小批(5条)更快出分


def _drain_bg_queue():
    """消费队列给候选评分; 冷启动用小批(5条)更快出首批分, 常规一次取3批并行评(加快补充)。"""
    global _bg_cold
    batch = []
    with _score_bg_lock:
        want = 5 if _bg_cold else score_batch() * 3   # 常规一次45条 → score_items 内部3并发并行
        while _score_bg_q and len(batch) < want:
            batch.append(_score_bg_q.popleft())
    if not batch:
        return
    chunk = 5 if _bg_cold else None   # 冷启动首屏: 5条/请求, 3并发更快出首批分数
    _bg_cold = False
    try:
        scores = score_items(batch, only_cache=False, chunk_size=chunk)
    except Exception:
        scores = {}
    # 成功评分的 key 从 seen 移除(已写库, 未来可因过期再评); 失败保留避免无限重评
    with _score_bg_lock:
        for it in batch:
            if it["key"] in scores:
                _score_bg_seen.discard(it["key"])


def _score_bg_loop():
    """守护线程: 后台渐进评分。AI 关闭/非全扫时空转; 队列空时休眠。"""
    while True:
        if ai_on() and sget("ai_full_scan") == "1":
            _drain_bg_queue()
        else:
            time.sleep(1.0)


def _ensure_score_bg():
    global _score_bg_started
    with _score_bg_lock:
        if _score_bg_started:
            return
        _score_bg_started = True
    threading.Thread(target=_score_bg_loop, daemon=True).start()


# ---------------------------------------------------------------- 封面缩略播放(B站预览)

_tag_done = set()
_tag_lock = threading.Lock()


def ensure_tags(items, limit=60):
    """后台补视频标签(无tag条目, 3并发): 供厌恶备注匹配使用(B站官方标签)。
    写回 items 库并同步到当前世代, 下一批条目不重复查。"""
    todo = []
    for it in (items or [])[:limit]:
        if (it.get("tag") or "").strip():
            continue
        bvid = (it.get("url") or "").rsplit("/", 1)[-1]
        if not bvid.startswith("BV"):
            continue
        with _tag_lock:
            if bvid in _tag_done:
                continue
            _tag_done.add(bvid)
        todo.append(it)
    if not todo:
        return

    def _one(it):
        bvid = (it.get("url") or "").rsplit("/", 1)[-1]
        try:
            d = bili_get("https://api.bilibili.com/x/tag/archive/tags?bvid=" +
                         urllib.parse.quote(bvid))
            names = [x.get("tag_name", "") for x in (d.get("data") or []) if x.get("tag_name")]
        except Exception:
            names = []
        if not names:
            return
        tag = ",".join(names)[:200]
        try:
            with _db_lock:
                row = db().execute("SELECT json FROM items WHERE key=?", (it.get("key"),)).fetchone()
                if row:
                    rec = json.loads(row[0])
                    rec["tag"] = tag
                    db().execute("UPDATE items SET json=? WHERE key=?",
                                 (json.dumps(rec, ensure_ascii=False), it.get("key")))
                else:
                    db().execute("INSERT INTO items(key,json,fetched_at,score_at) VALUES(?,?,0,0)",
                                 (it.get("key"), json.dumps(dict(it, tag=tag),
                                                           ensure_ascii=False)))
                db().commit()
        except Exception:
            pass
        with _uni_lock:   # 同步到当前世代, 即时生效
            for u in _universe["items"]:
                if u.get("key") == it.get("key") and not (u.get("tag") or "").strip():
                    u["tag"] = tag

    try:
        _TAG_X.map(_one, todo)
    except Exception:
        pass


_preview_cache = {}   # bvid -> {"cid": int, "url": str, "ts": float}
_PREVIEW_TTL = 3600


def preview_src(bvid):
    """返回 (proxy_path, None) 或 (None, err)。缓存 1 小时。"""
    if not re.match(r"^BV[0-9A-Za-z]{8,12}$", bvid):
        return None, "bvid格式不对"
    c = _preview_cache.get(bvid)
    now = time.time()
    if c and now - c["ts"] < _PREVIEW_TTL and c.get("url"):
        return "/api/video-proxy?bvid=%s&cid=%s" % (urllib.parse.quote(bvid), c["cid"]), None
    try:
        d = bili_get("https://api.bilibili.com/x/player/pagelist?bvid=" + urllib.parse.quote(bvid))
        data = d.get("data") or []
        if not data:
            return None, "无分P信息"
        cid = data[0].get("cid") or data[0].get("id")
        if not cid:
            return None, "无cid"
        qs = wbi_enc({"bvid": bvid, "cid": cid, "qn": 6, "fnval": 0,
                      "fourk": 0, "platform": "html5"})
        d2 = bili_get("https://api.bilibili.com/x/player/wbi/playurl?" + qs)
        durls = ((d2.get("data") or {}).get("durl")) or []
        if not durls:
            return None, "无mp4预览流"
        # 预览用最低清 qn=6(240P) + 第一段(视频开头), 起播快且流畅
        _preview_cache[bvid] = {"cid": cid, "url": durls[0].get("url", ""), "ts": now}
        return "/api/video-proxy?bvid=%s&cid=%s" % (urllib.parse.quote(bvid), cid), None
    except Exception as e:
        return None, str(e)[:80]


# ---------------------------------------------------------------- B站扫码登录

_LOGIN_QR = {"key": "", "ts": 0}
_avatar_cache = {"ts": 0, "data": None, "ctype": "image/jpeg"}
QR_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")


def login_qr_generate():
    req = urllib.request.Request(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        headers={"User-Agent": HEADERS["User-Agent"],
                 "Referer": "https://passport.bilibili.com/login"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    if d.get("code") != 0 or not (d.get("data") or {}).get("qrcode_key"):
        raise RuntimeError("二维码生成失败(code=%s)" % d.get("code"))
    data = d["data"]
    _LOGIN_QR.update(key=data["qrcode_key"], ts=int(time.time()))
    return {"key": data["qrcode_key"], "url": data["url"]}


def login_qr_poll(key):
    req = urllib.request.Request(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=%s" % key,
        headers={"User-Agent": HEADERS["User-Agent"],
                 "Referer": "https://passport.bilibili.com/login"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
        setcookies = resp.headers.get_all("Set-Cookie") or []
    d = json.loads(raw)
    data = d.get("data") or {}
    code = data.get("code")
    if code == 0:   # 扫码成功 → 从 url 参数 + Set-Cookie 两处收集登录凭证
        parts = {}
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(data.get("url", "")).query)
        for k in QR_KEYS:
            if qs.get(k):
                parts[k] = qs[k][0]
        for sc in setcookies:
            pair = sc.split(";", 1)[0]
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k in QR_KEYS:
                    parts[k] = v
        cookie = "; ".join("%s=%s" % (k, v) for k, v in parts.items())
        if not cookie:
            return {"status": "error", "message": "登录信息解析失败，请重试"}
        sset("bili_cookie", cookie)
        try:
            profile = fetch_account_profile()
        except Exception as e:
            return {"status": "error", "message": "登录成功但画像失败: %s" % str(e)[:100]}
        maybe_sync_blocklist()
        return {"status": "ok", "nickname": profile.get("nickname", ""), "profile": profile}
    if code == 86038:
        return {"status": "expired", "message": "二维码已过期，请点击「刷新二维码」"}
    if code == 86090:
        return {"status": "confirmed", "message": "已扫码，请在手机上确认登录"}
    if code == 86101:
        return {"status": "waiting", "message": "等待手机扫码…"}
    return {"status": "waiting", "message": data.get("message", "") or "等待扫码…"}


GROUP_NAMES = {"全站榜": "rank", "热门": "hot", "动画": "anime", "番剧": "bangumi",
               "国创": "guochuang", "鬼畜": "guichu", "舞蹈": "dance", "生活": "life",
               "美食": "food", "动物圈": "animal", "生活Vlog": "vlog",
               "影视": "film", "电影": "movie", "电视剧": "series", "纪录片": "documentary",
               "音乐": "music", "游戏": "game", "电竞": "esports",
               "知识": "knowledge", "科技": "tech", "数码": "digital",
               "运动": "sports", "赛事": "events", "美妆": "beauty",
               "时尚": "fashion", "汽车": "auto", "娱乐": "ent",
               "漫画": "comic", "资讯": "news", "直播": "live", "军事": "military"}
_ID2NAME = {v: k for k, v in GROUP_NAMES.items()}
SUGGESTIONS = ["今日成本", "只看游戏和数码分区", "最近1个月", "刷随机内容", "关掉热门", "屏蔽引战"]


# ---------------------------------------------------------------- 多会话(AI聊天)

def conv_list():
    """全部会话 + 当前会话id。"""
    cur = sget("cur_conv") or "1"
    rows = db().execute(
        "SELECT c.id, c.title, c.ts, c.updated, "
        "(SELECT COUNT(*) FROM chat WHERE cid=c.id) "
        "FROM conv c ORDER BY c.id").fetchall()
    convs = [{"id": r[0], "title": r[1], "ts": r[2], "updated": r[3], "count": r[4]} for r in rows]
    if not any(c["id"] == int(cur) for c in convs):
        cur = str(convs[0]["id"]) if convs else "1"
    return convs, int(cur)


def conv_cur(cid=None):
    """返回有效会话id: 指定cid 或 当前会话; 不存在则回退默认/新建。"""
    if cid is not None:
        try:
            cid = int(cid)
        except Exception:
            cid = None
    if cid is None or db().execute("SELECT 1 FROM conv WHERE id=?", (cid,)).fetchone() is None:
        _, cur = conv_list()
        cid = cur
    if db().execute("SELECT 1 FROM conv WHERE id=?", (cid,)).fetchone() is None:   # 库空则建默认
        db().execute("INSERT INTO conv(id,title,ts,updated) VALUES(1,'默认对话',?,?)",
                     (int(time.time()), int(time.time())))
        db().commit()
        cid = 1
    return cid


def conv_set_cur(cid):
    cid = conv_cur(cid)
    sset("cur_conv", str(cid))
    return cid


def conv_create(title=None, set_cur=True):
    title = (title or "").strip() or "新对话"
    ts = int(time.time())
    with _db_lock:
        cur = db().execute("INSERT INTO conv(title,ts,updated) VALUES(?,?,?)",
                           (title[:30], ts, ts)).lastrowid
        db().commit()
    if set_cur:
        sset("cur_conv", str(cur))
    return cur


def conv_touch(cid, title_hint=None):
    """更新会话活跃时间; 若标题还是默认名, 用首条消息自动命名。"""
    with _db_lock:
        if title_hint:
            row = db().execute("SELECT title FROM conv WHERE id=?", (cid,)).fetchone()
            if row and row[0] in ("新对话", "默认对话") and len(title_hint) >= 2:
                db().execute("UPDATE conv SET title=?, updated=? WHERE id=?",
                             (title_hint[:24], int(time.time()), cid))
            else:
                db().execute("UPDATE conv SET updated=? WHERE id=?", (int(time.time()), cid))
        else:
            db().execute("UPDATE conv SET updated=? WHERE id=?", (int(time.time()), cid))
        db().commit()


def conv_rename(cid, title):
    title = (title or "").strip()[:30]
    if not title:
        return False
    with _db_lock:
        db().execute("UPDATE conv SET title=? WHERE id=?", (title, conv_cur(cid)))
        db().commit()
    return True


def conv_delete(cid):
    cid = conv_cur(cid)
    with _db_lock:
        db().execute("DELETE FROM chat WHERE cid=?", (cid,))
        db().execute("DELETE FROM conv WHERE id=?", (cid,))
        db().commit()
    if sget("cur_conv") == str(cid):
        convs, _ = conv_list()
        if convs:
            sset("cur_conv", str(convs[-1]["id"]))   # 回退到最近一个
        else:
            conv_create(set_cur=True)


def last_feed_item():
    """用户最近看到/点开的条目, 用于“刚才那条”指代。"""
    try:
        raw = sget("last_feed")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set_last_feed(item):
    if not item:
        return
    try:
        sset("last_feed", json.dumps({"key": item.get("key", ""), "title": item.get("title", ""),
                                      "author": item.get("author", "")}, ensure_ascii=False))
    except Exception:
        pass


# 指代句式: 动作 + 最近条目
REF_ACTIONS = [
    (re.compile(r"收藏|稍后看|留一下|留个|先看|想看"), "saved"),
    (re.compile(r"屏蔽|拉黑|别让我看到|别让我刷到"), "author"),
    (re.compile(r"不喜欢|别推|别刷|不看|删掉|踩一下|不感兴趣"), "dislike"),
    (re.compile(r"点个赞|赞一下|给.*赞|不错|好看|喜欢这条"), "like"),
]
REF_NOUN = re.compile(r"刚才|上一条|那条|这条|刚刚|前面|这个|那个|它|刚才那条")


def handle_reference(m):
    """“刚才那条”→ 收藏/屏蔽/不喜欢/点赞。命中返回命令结果, 否则 None。"""
    if not REF_NOUN.search(m):
        return None
    act = None
    for pat, name in REF_ACTIONS:
        if pat.search(m):
            act = name
            break
    if not act:
        return None
    it = last_feed_item()
    if not it or not it.get("key"):
        return {"reply": "我还没记住你看过的内容，先去左侧刷几条再来指它吧～", "trigger": None, "chips": None}
    key, title, author = it.get("key", ""), it.get("title", "(那条)"), it.get("author", "")
    if act == "saved":
        db().execute("INSERT OR REPLACE INTO saved(key,kind,ts) VALUES(?,'later',?)",
                     (key, int(time.time())))
        db().commit()
        return {"reply": "已稍后看「%s」" % title[:26], "trigger": "saved", "chips": None}
    if act == "author":
        if not author:
            return {"reply": "那条没有作者信息，没法屏蔽～", "trigger": None, "chips": None}
        db().execute("INSERT OR IGNORE INTO blacklist(kind,value) VALUES('author',?)", (author,))
        db().commit()
        return {"reply": "已屏蔽作者「%s」，他的内容不再出现" % author, "trigger": "settings", "chips": None}
    if act == "dislike":
        db().execute("INSERT INTO feedback(key,author,action,ts) VALUES(?,?,?,?)",
                     (key, author, "dislike", int(time.time())))
        db().commit()
        return {"reply": "好，已记录不喜欢「%s」，以后同类内容少推" % title[:26],
                "trigger": "settings", "chips": None}
    if act == "like":
        db().execute("INSERT INTO feedback(key,author,action,ts) VALUES(?,?,?,?)",
                     (key, author, "like", int(time.time())))
        db().commit()
        return {"reply": "已收藏进偏好「%s」，之后会多推这类" % title[:26], "trigger": "settings", "chips": None}
    return None


def build_chips():
    """回复下方的快捷指令(可点击, 点击即作为新消息发送)。"""
    try:
        enabled = enabled_source_ids()
        game_on = "game" in enabled or not _subset_mode(enabled)
    except Exception:
        game_on = False
    mode = sget("mode")
    chips = [
        {"label": "🎲 换一批", "cmd": "随机来一批"},
        {"label": "📊 今日统计", "cmd": "今日统计"},
        {"label": "⚙️ 当前配置", "cmd": "当前配置"},
    ]
    chips.append({"label": "🙅 关掉游戏" if game_on else "🎮 只看游戏",
                  "cmd": "关掉游戏" if game_on else "只看游戏"})
    chips.append({"label": "🔒 更严格点" if mode != "严格" else "🫧 宽松点",
                  "cmd": "更严格点" if mode != "严格" else "宽松点"})
    return chips


def handle_command(msg):
    """聊天框命令引擎(无需AI): 识别并直接执行设置/屏蔽/统计/随机等指令。"""
    m = msg.strip()
    # --- 会话指代("刚才那条" → 收藏/屏蔽/不喜欢/点赞) ---
    ref = handle_reference(m)
    if ref:
        return ref
    # --- 新建/切换对话 ---
    if any(w in m for w in ("新对话", "新建对话", "换个话题", "重新开个", "开个新对话", "开新对话")):
        cid = conv_create()
        return {"reply": "已开新对话。说说这次想看点什么？", "trigger": "conv", "cid": cid}
    # --- 统计 ---
    if any(w in m for w in ("成本", "花了多少", "统计", "调用次数", "花了多少钱", "花了多少")):
        st = day_stats()
        pct = round(st["filtered"] / st["received"] * 100) if st["received"] else 0
        return {"reply": "今日收录 %s 条，剔除 %s 条（%s%%）；AI 调用 %s 次，成本约 ¥%s。" % (
            st["received"], st["filtered"], pct, st["ai_calls"],
            st["cost"] if st["cost"] >= 0.01 else "%.4f" % st["cost"]), "trigger": None}
    # --- 档位 ---
    for w in ("严格", "标准", "宽松", "严肃"):
        if w in m and ("档" in m or "模式" in m or len(m) <= 4):
            sset("mode", w)
            return {"reply": "已切到【%s】档（引战剔除阈值 %s，%s）" % (
                w, MODE_THRESH[w],
                "严肃模式：只推硬核教程与知识" if w == "严肃" else ""), "trigger": "settings"}
    # --- 时间窗 ---
    mm = re.search(r"最近\s*(\d+)\s*个?月", m)
    if mm:
        sset("m_max_age_months", mm.group(1))
        return {"reply": "已设置：只显示最近 %s 个月的内容（0=不限）" % mm.group(1), "trigger": "settings"}
    # --- 分区切换(只认明确指令句式, 避免聊天误触) ---
    found = [g for g in GROUP_NAMES if g in m]
    verbs = ("只看", "只刷", "只要", "只开", "就刷", "就开", "只留", "光刷", "就留",
             "关掉", "关闭", "不要", "打开", "加上", "加个", "开个", "切换", "改成", "设置为", "调成")
    if found and any(v in m for v in verbs):
        want = {GROUP_NAMES[g] for g in found}
        if any(v in m for v in ("只看", "只刷", "只要", "只开", "就刷", "就开", "只留", "光刷", "就留", "切换", "改成", "设置为", "调成")):
            ids = sorted(want)
        elif any(v in m for v in ("关掉", "关闭", "不要")):
            cur = enabled_source_ids()
            base = cur if (cur and cur != {g[0] for g in SOURCE_GROUPS}) else {g[0] for g in SOURCE_GROUPS}
            ids = sorted(base - want)
        else:
            cur = enabled_source_ids()
            base = cur if (cur and cur != {g[0] for g in SOURCE_GROUPS}) else set()
            ids = sorted(base | want)
        if ids:
            sset("m_sources", json.dumps(ids))
            purge_items_by_parts(set(ids))
            names = "、".join(_ID2NAME[i] for i in ids if i in _ID2NAME)
            return {"reply": "已切换分区：%s（%d 个，点击即生效）" % (names, len(ids)), "trigger": "settings"}
    # --- 临时屏蔽(实时干预): "未来1小时内别推游戏" / "接下来30分钟少推二次元" ---
    # 放在厌恶追加之前: 带时限的"别推"是临时屏蔽, 不应写入永久厌恶备注
    mt = re.search(r"(?:未来|接下来|之后)?\s*(\d+)\s*(?:个)?(小时|分钟)(?:内|之内)?\s*(?:都)?(?:别|少|不要)\s*(?:再)?\s*(?:给我)?\s*(?:推|刷|来|看|整)\s*([^，。,\s]{1,12})", m)
    if mt:
        try:
            n = int(mt.group(1))
        except Exception:
            n = 1
        unit = mt.group(2)
        mins = n * 60 if unit == "小时" else n
        kw = mt.group(3).strip("的了啊吧 ，, ")
        if 2 <= len(kw) <= 12 and 1 <= mins <= 24 * 60:
            add_tmp_block(kw, mins)
            until = time.strftime("%H:%M", time.localtime(time.time() + mins * 60))
            return {"reply": "已记下：未来 %s 内不推「%s」，%s 自动解封。" % (
                "%d个%s" % (n, unit), kw, until), "trigger": "random"}
    # --- 探索模式(实时干预): 临时拉高探索区配额并预览新领域 ---
    if any(w in m for w in ("探索", "发现新领域", "来点新东西", "看看别的", "换换领域", "新鲜领域")):
        sset("explore_boost_ts", str(int(time.time() + 30 * 60)))
        preview = _explore_candidates(set(), limit=5)
        lines = " / ".join("《%s》" % ((it.get("title") or "")[:22]) for it in preview) or "暂无跨领域候选"
        return {"reply": "已开启探索模式（30分钟）：探索区配额临时拉高到 30%。候选预览：" + lines,
                "trigger": "random"}
    # --- 厌恶追加(把不想看的内容写进厌恶备注) ---
    # 隐含句式: "我不想看/别看/别推/少来 X" (取动词后的关键词, 到标点为止)
    mdis = re.search(r"(?:别|不要|别再|不想再|不想|少来|别看|别刷|别推)[^。，,\s]*?(?:推|看|刷|来)\s*([^。，,\s]{2,16})", m)
    # 显式句式: "把 X 写到/加入 厌恶备注"
    mex = re.search(r"把\s*([^。，,\s]{2,16})\s*(?:写到|写进|加入|加到|添加到|记到|放入)\s*厌恶备注", m)
    # 直接句式: "少来/别来/别整 X" (无推看刷动词)
    mdir = re.search(r"(?:少来|别来|别整|别看)\s*([^。，,\s]{2,16})", m)
    extra = (mdis.group(1) if mdis else (mex.group(1) if mex else (mdir.group(1) if mdir else "")))
    if extra:
        # 去掉常见后缀(把"男女对立视频"归一成核心词"男女对立", 匹配更通用)
        extra = re.sub(r"(视频|内容|话题|帖子|相关|之类|东西|这类|这些)+$", "", extra).strip("的了啊吧 ，, ")
        if 2 <= len(extra) <= 16:
            cur = sget("dislike_note")
            sset("dislike_note", (cur + "，" + extra).strip("，") if cur else extra)
            clear_score_cache()   # 厌恶画像变化 → 旧语义分数过时, 强制重评
            force_rebuild()       # 非全扫模式下厌恶词靠机器层过滤, 必须重建池子才立即生效
            return {"reply": "已记下：以后不给你看「%s」相关的内容" % extra, "trigger": "settings"}
    # --- 屏蔽作者 ---
    mbl = re.search(r"(?:屏蔽|拉黑|别让我看到)(?:作者|up主|UP主|up|UP)?\s*([\w\u4e00-\u9fa5·]{2,20})", m)
    if mbl:
        name = mbl.group(1).strip("的了 ")
        with _db_lock:
            db().execute("INSERT OR IGNORE INTO blacklist(kind,value) VALUES('author',?)", (name,))
            db().commit()
        force_rebuild()   # 黑名单即刻生效: 重建池子并重排计划
        return {"reply": "已屏蔽作者：%s（他的内容不再出现）" % name, "trigger": "settings"}
    # --- 当前配置回显 ---
    if any(w in m for w in ("现在的设置", "当前设置", "我看下设置", "当前配置", "现在什么配置", "看看设置", "设置是什么")):
        enabled = enabled_source_ids()
        names = "、".join(_ID2NAME[i] for i in sorted(enabled) if i in _ID2NAME) or "默认全部"
        return {"reply": "当前配置| 分区：%s ｜ 档位：%s ｜ 时间窗：%s 个月 ｜ AI：%s" % (
            names, sget("mode"), sget("m_max_age_months"),
            "已开启" if ai_on() else ("已配置(总开关关)" if ai_ok() else "未配置")), "trigger": None}
    # --- 随机 ---
    if any(w in m for w in ("随机", "随便", "惊喜", "换换口味", "来点新鲜", "随便来点")):
        return {"reply": "好，随机来一批！已刷新为新内容。", "trigger": "random"}
    return None


def extract_intent(msg):
    """从聊天消息提取搜索意图: {"action":"search","keywords":[..]} 或 {"action":"reply","keywords":[]}"""
    msg = (msg or "").strip()
    if not msg:
        return {"action": "reply", "keywords": []}
    # 指代句式("刚才那条")由命令引擎处理, 不触发搜索
    if REF_NOUN.search(msg) and any(p.search(msg) for p, _ in REF_ACTIONS):
        return {"action": "reply", "keywords": []}
    if ai_on():
        try:
            content, _ = call_llm([
                {"role": "system", "content":
                    "用户在洞鉴虚明(内容筛选器)的聊天框说话, 判断他想让系统做什么, 只输出JSON(不要多余文字):\n"
                    "1) 想刷/推/看/找某类内容 → {\"action\":\"search\",\"keywords\":[\"1-2个搜索词\"]}\n"
                    "2) 想把某些词/内容加进厌恶备注(不想看/不喜欢/屏蔽/别再推) → "
                    "{\"action\":\"add_dislike\",\"keywords\":[\"词1\",\"词2\",...]}\n"
                    "   关键词要具体(如:性别对立、两性对立、彩礼纠纷), 可补充相关词, 2-15个, 每个2-16字\n"
                    "3) 想切换档位(严格/标准/宽松/严肃) → {\"action\":\"set_mode\",\"mode\":\"严格\"}\n"
                    "4) 其他闲聊/提问 → {\"action\":\"reply\",\"keywords\":[]}"},
                {"role": "user", "content": msg[:300]}], max_tokens=1500)
            d = json.loads(content)
            if isinstance(d, dict):
                act = d.get("action")
                if act == "search":
                    kws = [k.strip() for k in d.get("keywords", []) if k.strip()]
                    if kws:
                        return {"action": "search", "keywords": kws[:2]}
                    return {"action": "reply", "keywords": []}
                if act == "add_dislike":
                    kws = [k.strip() for k in d.get("keywords", [])
                           if k.strip() and 2 <= len(k.strip()) <= 16]
                    if kws:
                        return {"action": "add_dislike", "keywords": kws[:15]}
                    return {"action": "reply", "keywords": []}
                if act == "set_mode":
                    md = (d.get("mode") or "").strip()
                    if md in MODE_THRESH:
                        return {"action": "set_mode", "mode": md}
                    return {"action": "reply", "keywords": []}
                if act == "reply":
                    return {"action": "reply", "keywords": []}
        except Exception:
            pass
    # 无AI规则的句式兜底: 前置词 → 动词 → 尾缀 逐层剥离
    m = msg
    for pre in ("帮我刷", "给我刷", "帮我看", "给我看", "帮我找", "给我找", "帮我", "给我",
                "我想", "我要", "想要", "请", "现在"):
        if m.startswith(pre):
            m = m[len(pre):]
            break
    for v in ("多推点", "多来点", "来点", "多推", "多来", "搜索一下", "搜索", "推荐", "找点",
              "刷刷", "刷", "看", "推", "找", "来", "搜"):
        if m.startswith(v):
            m = m[len(v):]
            break
    for suf in ("视频", "内容", "一下吧", "一下", "吧", "啊", "的", "些", "点"):
        if m.endswith(suf):
            m = m[:-len(suf)]
            break
    m = m.strip(" ，,、的")
    # 疑问句/日常闲聊/寒暄不触发搜索
    if any(w in m for w in ("怎么样", "如何", "怎么", "吗", "呢", "啥", "哪", "为什么", "什么",
                            "好不好", "行不行", "可以吗", "有没有", "你觉得", "你认为", "谁")):
        return {"action": "reply", "keywords": []}
    if any(w in m for w in ("你好", "谢谢", "再见", "哈哈", "在吗", "早安", "午安", "晚安",
                            "嗯嗯", "666", "好的", "讲个故事", "讲个笑话", "废话", "聊聊")):
        return {"action": "reply", "keywords": []}
    if 2 <= len(m) <= 30:
        return {"action": "search", "keywords": [m]}
    return {"action": "reply", "keywords": []}


# ---------------------------------------------------------------- 分拣管线

def negative_feedback_filter(items):
    """负反馈硬排除(所有出口通用): 已点"不喜欢"的条目 + 黑名单作者/关键词 + 临时屏蔽。
    这三类用户信号此前只写入不读取, 是"不想看的内容反复出现"的直接原因。"""
    disliked = {r[0] for r in db().execute(
        "SELECT DISTINCT key FROM feedback WHERE action='dislike'").fetchall()}
    bl_authors = {r[0] for r in db().execute("SELECT value FROM blacklist WHERE kind='author'")}
    bl_kws = [r[0].lower() for r in db().execute("SELECT value FROM blacklist WHERE kind='keyword'")]
    tmp_kws = tmp_block_kws()
    if not (disliked or bl_authors or bl_kws or tmp_kws):
        return items
    out = []
    for it in items:
        if it.get("key") in disliked:
            continue
        if it.get("author") in bl_authors:
            continue
        text = ((it.get("title") or "") + " " + (it.get("desc") or "") + " " +
                (it.get("author") or "") + " " + (it.get("tag") or "") + " " +
                (it.get("tname") or "") + " " + (it.get("topic") or "")).lower()
        if bl_kws and any(k in text for k in bl_kws):
            continue
        if tmp_kws and any(k in text for k in tmp_kws):
            continue
        out.append(it)
    return out


def apply_filters(items, q=""):
    out = []
    for it in negative_feedback_filter(items):
        if q:   # q 非空=搜索子集过滤; 空 q 时只做负反馈过滤, 保持世代顺序
            text = (it["title"] + " " + it["desc"] + " " + it["author"]).lower()
            if q.lower() not in text:
                continue
        out.append(it)
    return out


_last_top = {"key": None}   # 上次顶部条目(防连续刷新顶部重复)


DISLIKE_EXPAND = [   # 厌恶词含"二次元"时启用: 常见二游IP/题材识别词(机器层, 无需AI)
    "崩坏", "星穹铁道", "星穹", "原神", "米哈游", "明日方舟", "碧蓝", "绝区零", "战双",
    "少女前线", "型月", "fgo", "东方project", "魔法禁书", "约战", "蔚蓝档案", "nikke",
    "恋与深空", "光与夜", "乙女游戏", "galgame", "二游", "抽卡", "二次元游戏", "米游", "崩3"]


def dislike_keywords():
    """从厌恶备注提取屏蔽关键词(逗号分隔/去前缀, 机器层直接过滤)。
    厌恶词含"二次元"时自动并入二游IP/题材扩展词, 弥补字面匹配盲区。"""
    raw = sget("dislike_note") or ""
    if not raw.strip():
        return []
    prefix = ("讨厌", "不喜欢", "不要", "别推", "别", "反感", "烦", "拒绝", "屏蔽", "尽量别", "少推", "避免")
    out = []
    for part in re.split(r"[，,、;；\n。！？!?]+", raw):
        p = part.strip()
        for pre in prefix:
            if p.startswith(pre):
                p = p[len(pre):]
                break
        p = p.strip("的了和与及，,。 ")
        if 2 <= len(p) <= 12:
            out.append(p.lower())
    if any("二次元" in k or "动漫" in k for k in out):
        out += [k.lower() for k in DISLIKE_EXPAND]
    return list(dict.fromkeys(out))


def machine_filter(items):
    """纯程序规则过滤(不耗AI): 时间范围/最低播放/最大时长/相似标题去重/账号拉黑名单。
    贴吧条目无播放/时长语义(views=0), 跳过播放量/时长规则, 仅保留时间/去重/黑名单;
    求助帖过滤(tb_allow_help=0): 剔除"求助/求教/请问"类提问帖。"""
    min_views = int(sget("m_min_views") or 0)
    max_views = int(sget("m_max_views") or 0)
    max_dur = int(sget("m_max_duration") or 0)
    min_dur = int(sget("m_min_duration") or 0)
    if sget("mode") == "严肃":
        max_age = int(sget("ser_max_age_months") or 0)   # 严肃模式独立时间窗(默认30月, 知识内容历史长)
    else:
        max_age = int(sget("m_max_age_months") or 0)
    dedup = sget("m_dedup") == "1"
    emotion = sget("m_emotion") != "0"
    news_fresh = sget("m_news_fresh") != "0"
    sync_bl = sget("m_blocklist_sync") == "1"
    ablock_authors = {r[0] for r in db().execute("SELECT name FROM ablock")} if sync_bl else set()
    dkws = dislike_keywords()
    allow_help = sget("tb_allow_help") != "0"
    xhcomm = [k.strip() for k in (sget("xhh_comm") or "").split(",") if k.strip()]
    xhtop = [k.strip() for k in (sget("xhh_topic") or "").split(",") if k.strip()]
    n_before_fb = len(items)
    items = negative_feedback_filter(items)   # 已不喜欢/黑名单/临时屏蔽: 入池前硬排除
    if len(items) != n_before_fb:
        _funnel_bump("dislike", n_before_fb - len(items))
    if sget("mode") == "严肃":
        items = sorted(items, key=lambda x: (-serious_relevance(x),
                                              -(x.get("pubdate") or 0), -x.get("views", 0)))
    else:
        items = sorted(items, key=lambda x: (0 if x.get("platform") != "B站" else 1, -x["views"]))   # 非B站先保留, B站再按播放量去重留优质
    now = time.time()
    fm = int(sget("bili_follow_months") or 1)   # 关注的人强调时间窗(0=不限)
    _funnel_bump("fetched", len(items))
    seen_titles, out = set(), []
    for it in items:
        if it.get("following"):
            if not it["pubdate"]:
                _funnel_bump("follow_window")
                continue   # 关注视频无发布时间 → 直接丢弃
            if fm and now - it["pubdate"] > fm * 30.44 * 86400:
                _funnel_bump("follow_window")
                continue   # 超出强调月数 → 直接丢弃(单独机制, 不符合规则不进池)
        if max_age and not it.get("following") and it["pubdate"] and now - it["pubdate"] > max_age * 30.44 * 86400:
            _funnel_bump("age")
            continue   # 只保留最近 N 个月发布的内容(关注的人强调豁免一般窗, 由强调窗单独约束)
        if news_fresh and it.get("pubdate") and _is_newsy(it) and now - it["pubdate"] > 86400:
            _funnel_bump("news")
            continue   # 时效分层: 资讯/热点只推24小时内(干货/教程不受此限)
        if not it.get("following"):
            views_known = it.get("views_known") is True or it.get("platform") == "B站"
            duration_known = it.get("duration_known") is True or it.get("platform") == "B站"
            if views_known:
                if min_views and it.get("views", 0) < min_views:
                    _funnel_bump("views")
                    continue
                if max_views and it.get("views", 0) > max_views:
                    _funnel_bump("views")
                    continue
            if duration_known:
                if max_dur and it.get("duration", 0) > max_dur:
                    _funnel_bump("duration")
                    continue
                if min_dur and it.get("duration", 0) < min_dur:
                    _funnel_bump("duration")
                    continue
        if it.get("platform") == "贴吧" and not allow_help:
            if is_help_post(it):
                _funnel_bump("help")
                continue   # 求助帖(用户关闭允许)
        if it.get("platform") == "小黑盒":   # 社区/话题筛选(allow-list, 命中任一保留)
            if xhcomm and not any(k in (it.get("xcomm") or "") for k in xhcomm):
                _funnel_bump("xhh")
                continue
            if xhtop and not any(k in (it.get("xtopic") or "") for k in xhtop):
                _funnel_bump("xhh")
                continue
        if it["author"] in ablock_authors:
            _funnel_bump("blocklist")
            continue
        if dkws and any(k in (it["title"] + " " + it["desc"] + " " + it["author"] +
                              " " + (it.get("tag") or "")).lower() for k in dkws):
            _funnel_bump("dislike")
            continue   # 厌恶备注关键词命中(含tag) → 直接剔除
        if emotion and _emo_bad(it):
            _funnel_bump("emotion")
            continue   # 情绪过滤器: 感叹号轰炸/引战黑话/标题党句式(纯规则零token)
        if dedup:
            fp = _title_fp(it["title"])
            if fp in seen_titles:
                _funnel_bump("dedup")
                continue   # 规则②: 本次推送内同指纹标题只留最高热一条
            seen_titles.add(fp)
        out.append(it)
        _funnel_bump("kept")
    return out


# 贴吧求助帖判定词(标题/摘要命中即为求助/提问帖; 保守词表防误伤)
HELP_POST_KWS = ("求助", "求教", "求大佬", "求指导", "求推荐", "求解答", "求帮助",
                 "请教", "帮帮我", "在线等", "急!", "急！", "有懂", "有没有人",
                 "新手求助", "新人求助", "求解", "求助帖", "萌新", "小白求",
                 "求个", "求了", "谁能教", "谁能帮", "哪位大佬", "怎么解决",
                 "有没有懂", "帮忙看看", "求问",
                 # 咨询/求教意图(防"问前景/值得学/想问问"这类求助帖漏过)
                 "想问问", "想问", "求建议", "求指教", "求指点", "求经验", "求意见",
                 "有前景", "前景怎么样", "前景如何", "值得学", "值得入", "怎么入门",
                 "零基础", "新手小白", "刚想学")


# 贴吧求助/提问帖语义模式(标题+摘要): 覆盖 "求建议/求支招/有没有大佬/请帮忙/求解惑/想问问/怎么解决" 等
HELP_RE = re.compile(
    r"(求(?:教|大佬|大神|推荐|建议|解答|支招|指点|经验|帮助|方法|解决|问|指导|个|了|解惑)|"
    r"有没有(?:大佬|人|懂|谁)|谁知道|哪位(?:大佬|高人)|"
    r"想问问|想问下|想问|请问|请(?:帮忙|教|高人|大佬)|帮忙|帮帮我|帮我看看|"
    r"怎么(?:解决|弄)|如何(?:解决|弄)|怎么办|解惑)")


def is_help_post(it):
    """求助/提问帖判定:
    ① 帖子求助分类标签(文本/【求助】) → 直接判求助;
    ② 标题/摘要命中求助语义模式(求X/有没有大佬/想问问/请帮忙/怎么解决等) 或求助词表。"""
    tclass = (it.get("tclass") or "").lower()
    if any(k in tclass for k in ("求助", "求教", "提问", "咨询")):
        return True
    text = ((it.get("title") or "") + " " + (it.get("desc") or "")).lower()
    if HELP_RE.search(text):
        return True
    return any(k.lower() in text for k in HELP_POST_KWS)


def rules_filter(items, skip_age=False):
    """机器规则过滤(保留原顺序, 用于显式搜索): 时间窗/播放量/时长/拉黑名单。
    skip_age=True 时跳过时间窗(主动搜索想找特定主题, 老内容也应出现)。
    负反馈(不喜欢/黑名单/临时屏蔽)同样生效: 搜索也不该递回用户明确拒绝的内容。"""
    items = negative_feedback_filter(items)
    min_views = int(sget("m_min_views") or 0)
    max_views = int(sget("m_max_views") or 0)
    max_dur = int(sget("m_max_duration") or 0)
    min_dur = int(sget("m_min_duration") or 0)
    if sget("mode") == "严肃":
        max_age = int(sget("ser_max_age_months") or 0)   # 严肃模式独立时间窗(默认30月)
    else:
        max_age = int(sget("m_max_age_months") or 0)
    sync_bl = sget("m_blocklist_sync") == "1"
    ablock_authors = {r[0] for r in db().execute("SELECT name FROM ablock")} if sync_bl else set()
    dkws = dislike_keywords()
    allow_help = sget("tb_allow_help") != "0"
    now = time.time()
    fm = int(sget("bili_follow_months") or 1)   # 关注的人强调时间窗(0=不限)
    out = []
    for it in items:
        if it.get("following"):
            if not it["pubdate"]:
                continue   # 关注视频无发布时间 → 直接丢弃
            if fm and now - it["pubdate"] > fm * 30.44 * 86400:
                continue   # 超出强调月数 → 直接丢弃(单独机制, 不符合规则不进池)
        if not skip_age and max_age and not it.get("following") and it["pubdate"] and now - it["pubdate"] > max_age * 30.44 * 86400:
            continue
        if not it.get("following"):
            views_known = it.get("views_known") is True or it.get("platform") == "B站"
            duration_known = it.get("duration_known") is True or it.get("platform") == "B站"
            if views_known:
                if min_views and it.get("views", 0) < min_views:
                    continue
                if max_views and it.get("views", 0) > max_views:
                    continue
            if duration_known:
                if max_dur and it.get("duration", 0) > max_dur:
                    continue
                if min_dur and it.get("duration", 0) < min_dur:
                    continue
        if it.get("platform") == "贴吧" and not allow_help:
            if is_help_post(it):
                continue   # 求助帖(用户关闭允许) — 搜索路径也生效
        if it["author"] in ablock_authors:
            continue
        if dkws and any(k in (it["title"] + " " + it["desc"] + " " + it["author"] +
                              " " + (it.get("tag") or "")).lower() for k in dkws):
            continue   # 厌恶备注关键词命中(含tag) → 直接剔除
        out.append(it)
    return out


_search_deep_cache = {}   # 近义词缓存(进程内, 1h): q -> {"terms": [...], "ts": ts}
_search_wave = {}         # 搜索波次缓存: q -> {"items","ts","used_terms","deep_terms","waves","end","building"}
_search_wave_lock = threading.Lock()
SEARCH_WAVE_TTL = 3600    # 搜索波次缓存1h(与近义词缓存同频)
SEARCH_MAX_WAVES = 3      # 近义词扩波上限(首波+3波, 防B站/贴吧风控+LLM无限绕圈)


def search_expand_terms(q, exclude=None):
    """为搜索主题生成近义/相关搜索词(LLM)。无AI时返回空(不做深挖)。
    exclude: 已搜过的词(扩波时排除防原地打转); 带exclude的调用不缓存(每次要新词)。"""
    q = (q or "").strip()
    if not q or not ai_on():
        return []
    key = "__terms__" + q
    if not exclude:
        c = _search_deep_cache.get(key)
        if c and time.time() - c["ts"] < 3600:
            return c["terms"]
    terms = []
    try:
        sys_p = ("给定一个内容搜索主题(视频/帖子/软件/游戏/教程/话题等), 输出5-8个"
                 "近义词/相关搜索词(用于在视频站和贴吧扩搜更多内容), "
                 "只输出JSON数组字符串, 例如[\"A\",\"B\",\"C\"]")
        if exclude:
            sys_p += "。以下词已搜过, 不要再输出: " + "、".join(list(exclude)[-18:])
        content, _ = call_llm([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": q[:100]}], max_tokens=1500)
        arr = json.loads(content)
        if isinstance(arr, list):
            terms = [str(t).strip() for t in arr
                     if str(t).strip() and str(t).strip() != q][:8]
    except Exception:
        terms = []
    if exclude:
        terms = [t for t in terms if t not in exclude]
    else:
        _search_deep_cache[key] = {"terms": terms, "ts": time.time()}
    return terms


def _interleave3(a, b):
    """a每3条插1条b(搜索结果的跨平台交错, 保证贴吧/小黑盒在首屏可见)。"""
    out, i, j = [], 0, 0
    while i < len(a) or j < len(b):
        for _ in range(3):
            if i < len(a):
                out.append(a[i]); i += 1
        if j < len(b):
            out.append(b[j]); j += 1
    return out


def _local_pool_hits(terms, seen, limit=100):
    """本地沉淀池(贴吧/小黑盒)分词命中: 任一token命中即算, 命中数多者优先/标题命中加权。
    旧版要求整句连续子串命中 → 多词/短词查询几乎必空(跨平台搜索形同虚设)。"""
    toks = [t for t in {str(x).strip().lower() for x in (terms or [])} if len(t) >= 2][:12]
    if not toks:
        return []
    try:
        rows = db().execute(
            "SELECT json FROM items WHERE (json LIKE ? OR json LIKE ?) "
            "ORDER BY fetched_at DESC LIMIT 3000",
            ('%"platform": "贴吧"%', '%"platform": "小黑盒"%')).fetchall()
    except Exception:
        return []
    scored = []
    for (js,) in rows:
        try:
            it = json.loads(js)
        except Exception:
            continue
        if not isinstance(it, dict) or not it.get("key") or it["key"] in seen:
            continue
        if it.get("platform") == "贴吧" and not tieba_bar_allowed(it):
            continue
        text = ((it.get("title") or "") + " " + (it.get("desc") or "") + " " +
                (it.get("author") or "") + " " + (it.get("tag") or "") + " " +
                (it.get("tname") or "") + " " + (it.get("xcomm") or "") + " " +
                (it.get("xtopic") or "") + " " + (it.get("src") or "")).lower()
        hits = [t for t in toks if t in text]
        if not hits:
            continue
        score = len(hits)
        if any(t in (it.get("title") or "").lower() for t in hits):
            score += 2
        for f in ("views", "likes", "reply", "pubdate", "duration"):
            it.setdefault(f, 0)
        it.setdefault("danmaku", None)
        scored.append((score, it))
        seen.add(it["key"])
    scored.sort(key=lambda p: -p[0])
    return [it for _, it in scored[:limit]]


def _search_first_wave(q):
    """搜索第一波: B站WBI直搜(≤5页, 空页即止) + 贴吧全站实时搜索(与B站翻页并行)
    + 本地沉淀池分词命中 → 跨平台3:1交错 → 机器规则过滤。"""
    seen, bili = set(), []
    tb_x = _FX.submit(tb_search_items, q)
    for pg in range(1, 6):
        page = search_items(q, pg)
        for x in page:
            bv = (x.get("url") or "").rsplit("/", 1)[-1]
            if bv and bv not in seen:
                seen.add(bv)
                bili.append(x)
        if not page:
            break   # 空页=直搜见底, 不再空翻
    tb = []
    try:
        for x in tb_x.result() or []:
            if x.get("key") and x["key"] not in seen:
                seen.add(x["key"])
                tb.append(x)
    except Exception:
        pass
    local = _local_pool_hits([q], seen)
    candidates = _interleave3(bili, tb + local)
    _annotate_following(candidates)
    return rules_filter(candidates, skip_age=True)


def _search_expand_wave(q, used_terms):
    """近义词深挖扩波: LLM生成新近义词(排除已用) → 每词并行搜B站1页+贴吧10条
    + 本地池分词命中 → 去重过滤。返回 (new_items, terms)。terms为空=深挖到头。"""
    terms = search_expand_terms(q, exclude=list(used_terms))
    if not terms:
        return [], []
    terms = terms[:6]

    def _one(t):
        out = []
        try:
            out.extend(search_items(t, 1))
        except Exception:
            pass
        try:
            out.extend(tb_search_items(t, rn=10))
        except Exception:
            pass
        return out

    try:
        per = _FX.map(_one, terms)
    except Exception:
        per = []
    seen, out = set(), []
    for lst in per:
        for x in lst:
            k = x.get("key") or (x.get("url") or "")
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(x)
    out.extend(_local_pool_hits(terms, seen))
    _annotate_following(out)
    return rules_filter(out, skip_age=True), terms


def _prune_search_waves():
    """波次缓存容量上限(保留最近12个查询, 防长驻进程内存膨胀)。调用方需持锁。"""
    if len(_search_wave) <= 12:
        return
    for k in sorted(_search_wave, key=lambda k: _search_wave[k]["ts"])[:len(_search_wave) - 12]:
        _search_wave.pop(k, None)


def search_all(q, offset=0, limit=30, refresh=False):
    """显式搜索(波次制+无限深挖):
    第1波 = B站直搜 + 贴吧实时搜索 + 本地池; 滚到底(offset+limit≥total)自动触发
    近义词深挖扩波(LLM生成新词, 每词B站+贴吧+本地池), 直到无新词/无新内容/达波次上限。
    波次缓存1h: 翻页不再重打B站接口(旧版每滚一屏全量重搜, 既慢又喂风控)。
    refresh=True: 丢弃该词的波次与近义词缓存, 强制重搜(前端↻刷新)。"""
    q = (q or "").strip()
    if not q:
        return {"ok": True, "q": q, "items": [], "offset": 0, "total": 0,
                "end": True, "deep_terms": [], "building": False, "platform_hits": 0}
    with _search_wave_lock:
        if refresh:
            _search_wave.pop(q, None)
            _search_deep_cache.pop("__terms__" + q, None)
    work = False   # 本请求是否负责干活(建波/扩波, 其余并发请求仅读快照)
    with _search_wave_lock:
        w = _search_wave.get(q)
        if w and time.time() - w["ts"] > SEARCH_WAVE_TTL:
            _search_wave.pop(q, None)
            w = None
        if w is None:
            w = {"items": [], "ts": time.time(), "used_terms": [q], "deep_terms": [],
                 "waves": 0, "end": False, "building": False}
            _search_wave[q] = w
            _prune_search_waves()
        if not w["building"]:
            w["building"] = True
            work = True
        building = w["building"]
    if work:
        try:
            if not w["items"]:   # 首建: 第一波
                w["items"] = _search_first_wave(q)
                w["ts"] = time.time()
            while (not w["end"] and w["waves"] < SEARCH_MAX_WAVES
                   and offset + limit > len(w["items"])):
                new_items, terms = _search_expand_wave(q, w["used_terms"])
                terms = [t for t in terms if t not in w["used_terms"]]
                if terms:
                    exist = {it.get("key") for it in w["items"]}
                    new_items = [x for x in new_items if x.get("key") and x["key"] not in exist]
                    w["used_terms"].extend(terms)
                    w["deep_terms"].extend(terms)
                    w["waves"] += 1
                    w["items"].extend(new_items)
                    w["ts"] = time.time()
                if not terms or not new_items:
                    w["end"] = True   # 无新词或新词全撞重复 → 到底(防前端空转循环)
            if not w["end"] and offset + limit > len(w["items"]):
                w["end"] = True   # 波次用尽仍不够 → 到底(否则前端会无限重试)
        except Exception:
            w["end"] = True
        finally:
            with _search_wave_lock:
                w["building"] = False
    with _search_wave_lock:
        raw_chunk = list(w["items"][offset:offset + limit])
        chunk = list(raw_chunk)
        consumed = len(raw_chunk)
        total = len(w["items"])
        end = w["end"]
        deep_terms = list(w["deep_terms"])
        building = w["building"]
        cross = sum(1 for it in w["items"] if it.get("platform") != "B站")
    if chunk:   # 入库, 供本地沉淀池复用(搜索结果回流)
        now = time.time()
        try:
            with _db_lock:
                for it in chunk:
                    db().execute("INSERT INTO items(key,json,fetched_at,score_at) VALUES(?,?,?,0) "
                                 "ON CONFLICT(key) DO UPDATE SET json=excluded.json,fetched_at=excluded.fetched_at",
                                 (it["key"], json.dumps(it, ensure_ascii=False), now))
                db().commit()
        except Exception:
            pass
        # 搜索结果交给后台评分(兜底): 若同步评分失败, 后台线程重试写库, 后续命中
        _request_bg_score(chunk)
        if ai_on() and sget("ai_full_scan") == "1":   # 搜索=显式动作, 保留同步补评(与历史一致); feed主路径已改只读缓存
            try:
                cut = MODE_THRESH.get(sget("mode"), 6)
                imin = int(sget("ai_interest_min") or 0)
                dkws = dislike_keywords()
                scored = score_items(chunk)
                kept, dropped = [], 0
                for it in chunk:
                    if it.get("platform") != "B站":   # 贴吧/小黑盒不走AI评分门禁(无评分语义)
                        kept.append(it)
                        continue
                    sc = scored.get(it["key"])
                    if not sc:
                        continue   # 门禁: 无分数不展示
                    if sc.get("flame", 0) >= cut or sc.get("spam", 0) >= SPAM_CUT:
                        dropped += 1
                        continue
                    if (sc.get("interest", 5) or 0) == 0:   # AI语义厌恶命中(评分器强制0分) → 一票剔除
                        dropped += 1
                        continue
                    if imin and sc.get("interest", 5) < imin:
                        dropped += 1
                        continue
                    if dkws and any(k in (it.get("title", "") + sc.get("topic", "") +
                                          it.get("desc", "")) for k in dkws):
                        dropped += 1   # 标题/主题字面命中厌恶词(未拆词, 防误伤普通游戏)
                        continue
                    it["reason"] = sc.get("reason", "") or ""
                    it["topic"] = sc.get("topic", "") or ""
                    it["interest"] = sc.get("interest", 5)
                    kept.append(it)
                if dropped:
                    bump_stats(filtered=dropped)
                    _funnel_bump("ai_gate", dropped)
                chunk = kept
            except Exception:
                pass
    return {"ok": True, "q": q, "items": chunk, "offset": offset + consumed,
            "next_offset": offset + consumed, "total": total, "end": end,
            "has_more": offset + consumed < total or not end, "deep_terms": deep_terms,
            "building": building, "platform_hits": cross}


# ---------------------------------------------------------------- 信息流世代(无限滚动)

_universe = {"gen": 0, "items": [], "source": "", "offline": True, "ai": False,
             "mode": "", "account": False, "building": False, "built_at": 0,
             "partitions": "全部"}
_uni_lock = threading.Lock()
_uni_token = 0   # 设置版本令牌: 切分区等变更时+1, 旧构建结果作废
_score_progress = {"done": 0, "total": 0, "ts": 0, "full": False}
MAX_UNIVERSE = 2000   # 信息池上限(防内存无限增长); 持续追加到达上限后裁最旧头部


def bake_universe(payload, quick=False):
    """候选池 → 机器筛选 → AI评分(后台补全时) → LRU排序, 产出定型队列。"""
    # 关注标记必须先于机器筛选, 否则关注视频会先按普通播放量/时长规则被剔除。
    for it in payload["items"]:
        if it.get("src") == "关注":
            it["following"] = True
    _annotate_following(payload["items"])
    items = machine_filter(payload["items"])   # 时间窗/播放量/时长/去重/拉黑名单
    # 源产出率自学习: 统计各可裁剪源"抓回→筛选存活"通过率, 低产源后续少抓/跳过。
    _g_fetched, _g_kept = {}, {}
    for it in payload["items"]:
        g = it.get("gid")
        if g in ("rank", "hot", "related"):
            _g_fetched[g] = _g_fetched.get(g, 0) + 1
    for it in items:
        g = it.get("gid")
        if g in _g_fetched:
            _g_kept[g] = _g_kept.get(g, 0) + 1
    for g, f in _g_fetched.items():
        _src_yield_update(g, f, _g_kept.get(g, 0))
    _annotate_following(items)   # 正常刷到的视频里, 作者是关注的人且月内 → 标 following(作者名强调色+标签)
    acct = account_slim()
    acct_authors = {a["name"] for a in (acct.get("authors") or [])}
    acct_kws = [k for k in (acct.get("keywords") or []) if k]
    ai = ai_on()
    full = sget("ai_full_scan") == "1"
    _score_progress.update(full=full)
    pool_items = items
    if full and ai and not quick:
        # 开启模式: 只读缓存快速过滤(评分在serve门禁做, 不占评分池/不阻塞世代替换)
        order = sorted(items, key=lambda x: -x["views"])
        scores = score_items(order, only_cache=True)
        for i, it in enumerate(order):
            it["_range_ok"] = True
    else:
        pool_items = items
        scores = {}   # 关闭模式: 不做AI筛查, 零token消耗
    mode = sget("mode")
    cut = MODE_THRESH.get(mode, 6)
    imin = int(sget("ai_interest_min") or 0)
    # 短时间内看到过的不再出现(小时数可调, 0=不限); 仅当剔除后不足一页时保留(最后手段)
    seen_h = int(sget("m_seen_hours") or 6)
    if seen_h > 0:
        recent_seen = {r[0] for r in db().execute(
            "SELECT key FROM shown WHERE ts > ?", (int(time.time()) - seen_h * 3600,)).fetchall()}
        fresh_pool = [it for it in pool_items if it.get("key") not in recent_seen]
        if len(fresh_pool) >= 30:
            pool_items = fresh_pool
    ser = sget("mode") == "严肃"
    if ser:
        # 严肃模式先筛出合格内容; 候选不足一批时恢复近期看过的合格条目, 避免首屏只剩个位数。
        fresh_serious = [it for it in pool_items if is_serious_ok(it)]
        if len(fresh_serious) < 30:
            fresh_keys = {it.get("key") for it in fresh_serious}
            fresh_serious += [it for it in items if it.get("key") not in fresh_keys and is_serious_ok(it)]
        pool_items = fresh_serious
    kept, filtered = [], 0
    for it in pool_items:
        sc = scores.get(it["key"])
        if full and ai:
            if sc is None:
                it["_scored"] = False
                it.setdefault("reason", "")
                it.setdefault("topic", "")
                it.setdefault("interest", 5)
                kept.append(it)   # 无分数: 保留进池供后台评分, 展示门禁在 serve_chunk
                continue
            if sc.get("flame", 0) >= cut or sc.get("spam", 0) >= SPAM_CUT:
                filtered += 1
                continue
            if sc.get("interest", 5) == 0:
                continue   # AI语义厌恶命中 → 一票剔除
            if imin and sc.get("interest", 5) < imin:
                continue   # 推荐标准: 兴趣分门槛
            if "emo" in sc and (sc.get("emo") or 0) <= -6:
                filtered += 1
                continue   # 情绪净值极负(纯负能量/贩卖焦虑) → 一票剔除
            if ser and "density" in sc and (sc.get("density") or 0) < 4:
                filtered += 1
                continue   # 严肃模式: 信息密度门槛(废话多不放行)
            if "facts" in sc and not sc.get("facts") and _is_newsy(it) and (sc.get("density") or 0) < 6:
                filtered += 1
                continue   # 资讯无事实锚点+低密度 → 纯情绪输出
            it["_scored"] = True
            it["reason"] = sc.get("reason", "") or ""
            it["topic"] = sc.get("topic", "") or ""
            it["interest"] = sc.get("interest", 5)
        else:
            it["reason"] = ""
            it["topic"] = ""
            it["interest"] = 5
        kept.append(it)
    shown_map = {r[0]: r[1] for r in db().execute("SELECT key, ts FROM shown").fetchall()}
    if full and ai:
        # 已评分内容排前, 未评分垫底(供后台评分; 前端按序加载先展示已筛查内容)
        kept.sort(key=lambda x: (0 if x.get("_scored") else 1, -x["interest"], -x["views"]))
    else:
        kept.sort(key=lambda x: (
            0 if x["author"] in acct_authors else 1,
            0 if x["key"] not in shown_map else 1,
            shown_map.get(x["key"], 0),
            0 if acct_kws and any(k in x["title"] for k in acct_kws) else 1,
            -x["views"]))
    kept = mix_sources(kept)   # 混排: 来源交错, 同类(词/源)不扎堆
    if len(kept) > 1 and kept[0]["key"] == _last_top["key"]:   # 防重复保险
        for j in range(1, len(kept)):
            if kept[j]["key"] != _last_top["key"]:
                kept[0], kept[j] = kept[j], kept[0]
                break
    _last_top["key"] = kept[0]["key"] if kept else None
    bump_stats(filtered=filtered)
    return kept


SEEN_DEDUP_H = 6   # 已展示去重窗口(小时)

# 严肃模式: 只推教程、技能学习与硬核知识; 不靠泛"解析/解说"放行娱乐内容
LEARNING_KWS = ("教程", "教学", "课程", "公开课", "讲座", "学堂", "入门", "自学", "学习",
                "从零", "实操", "精讲", "训练", "练手", "练习", "讲义", "课堂",
                "系统课", "手把手", "初学者", "进阶", "实战", "网课", "培训")
MAKING_KWS = ("编程", "代码", "游戏开发", "unity", "虚幻引擎", "ue5", "ue4", "godot", "blender", "源码", "工程实践")
DEPTH_KWS = ("详解", "原理", "机制", "系统", "架构", "底层", "万字", "深度", "全史",
             "全流程", "拆解", "硬核", "论文", "研究", "实验", "源码", "源码解析")
SERIOUS_AREAS = ("知识", "科技", "纪录片", "人文历史", "军事")
SERIOUS_BLOCK_AREAS = ("番剧", "动画", "国创", "影视", "电影", "电视剧", "娱乐", "鬼畜", "舞蹈", "音乐", "漫画")
SERIOUS_BLOCK_TNAMES = ("番剧", "连载动画", "完结动画", "资讯", "国产动画", "国产原创相关", "MAD·AMV",
                        "短片·手书", "同人·手书", "影视杂谈", "影视剪辑", "电影", "短剧")
SERIOUS_EXTRA = ["教程", "入门", "自学", "从零开始", "精讲", "系统课", "手把手", "初学者",
                 "进阶", "实战", "网课", "公开课", "技能", "学习", "科普", "编程",
                 "剪辑教程", "绘画教程", "音乐制作教程", "健身课程",
                 "科学", "物理", "化学", "生物", "天文", "历史", "哲学", "经济", "心理学",
                 "人工智能", "机器学习", "计算机科学", "法律", "医学", "数学", "社会学"]
ENTERTAINMENT_KWS = ("娱乐", "搞笑", "整活", "鬼畜", "reaction", "反应", "名场面", "高能", "爆笑",
                     "吐槽", "锐评", "吃瓜", "盘点", "剪辑合集", "混剪", "二创", "直播切片",
                     "解说", "评测", "游戏杂谈", "开箱", "皮肤", "抽卡", "氪金", "挑战", "经典镜头")
LOW_VALUE_SERIOUS_KWS = ("招聘", "招募", "有偿", "求一个", "求个", "有没有好用", "推荐一手", "不得不出手", "疑问",
                         "肥羊", "拜鬼", "反思")
TECHNICAL_HELP_KWS = ("教程", "教学", "课程", "建模", "渲染", "拓扑", "蒙皮", "骨骼", "材质", "贴图",
                      "编程", "代码", "开发", "源码", "算法", "原理", "性能", "配置", "硬件",
                      "网络", "服务器", "引擎", "unity", "unreal", "ue5", "godot", "blender")
XHH_TECHNICAL_KWS = ("开发", "源码", "编程", "代码", "引擎", "unity", "unreal", "ue5", "godot",
                     "dlss", "fsr", "显卡", "硬件", "性能", "帧率", "配置", "驱动", "网络", "服务器")
GAME_DOMAIN_KWS = ("游戏", "手游", "端游", "主机", "steam", "绝地潜兵", "helldivers", "战争雷霆",
                   "war thunder", "rimworld", "环世界", "原神", "星穹铁道", "鸣潮", "王者荣耀")
GAME_TASTE_KWS = ("游戏实况", "实况", "通关", "攻略", "配装", "走位", "速通", "身法",
                  "武斗", "开黑", "排位", "上分", "教学视频", "最阴间", "高难", "boss", "BOSS打法", "关卡")
SUBJECT_KWS = ("物理", "化学", "生物", "天文", "历史", "哲学", "经济", "心理", "编程",
               "医学", "法律", "数学", "机器学习", "人工智能", "计算机", "工程", "论文",
               "研究", "分子", "量子", "相对论", "基因", "细胞", "金融", "社会学",
               "结构", "原理", "系统", "机制", "军事", "军工", "装备", "战术")


def serious_relevance(it):
    """严肃模式抓取前排序: 用户偏好和学习/硬核信号优先, 减少抓后过滤损耗。"""
    title = (it.get("title") or "").lower()
    desc = (it.get("desc") or "").lower()
    text = title + " " + desc
    score = 0
    for kw in pref_all_kws():
        k = kw.lower()
        if k and k in text:
            score += 100 + min(len(k), 12)
    score += 30 * sum(1 for k in LEARNING_KWS if k in title)
    score += 24 * sum(1 for k in MAKING_KWS if k in title)
    score += 20 * sum(1 for k in DEPTH_KWS if k in title)
    score += 8 * sum(1 for k in SUBJECT_KWS if k in title)
    score -= 80 * sum(1 for k in ENTERTAINMENT_KWS if k in title)
    score -= 100 * sum(1 for k in GAME_TASTE_KWS if k in title)
    return score


def is_serious_ok(it):
    """严肃模式: 只放教程/制作/开发/硬核知识; 娱乐、实况、泛评测优先剔除。"""
    title = (it.get("title") or "").lower()
    desc = (it.get("desc") or "").lower()
    body = title + " " + desc
    meta = ((it.get("tag") or "") + " " + (it.get("tname") or "") + " " +
            (it.get("area") or "") + " " + (it.get("xcomm") or "") + " " +
            (it.get("xtopic") or "")).lower()
    text = body + " " + meta
    if (it.get("platform") or "B站") == "B站":
        area0 = it.get("area") or ""
        tname0 = it.get("tname") or ""
        if area0 in SERIOUS_BLOCK_AREAS or tname0 in SERIOUS_BLOCK_TNAMES:
            return False
    has_title_learning = any(k in title for k in LEARNING_KWS) or any(k in title for k in MAKING_KWS)
    has_learning = has_title_learning or any(k in body for k in ("公开课", "系统课", "课程", "教程", "教学"))
    game_dev = any(k in title for k in MAKING_KWS) and not any(k in title for k in ("开发者", "制作人", "工作室"))
    title_hard = any(k in title for k in DEPTH_KWS)
    has_hard = title_hard and any(k in title + " " + meta for k in SUBJECT_KWS)
    game_domain = (it.get("area") or "") == "游戏" or any(k in text for k in GAME_DOMAIN_KWS)
    if any(k in body for k in LOW_VALUE_SERIOUS_KWS):
        return False
    if any(k in title for k in GAME_TASTE_KWS):
        return False
    if game_domain and not game_dev:
        return False
    if it.get("platform") == "贴吧":
        has_technical = any(k in title for k in TECHNICAL_HELP_KWS)
        if not has_technical and not has_hard:
            return False
        if has_technical and not any(k in title for k in ENTERTAINMENT_KWS):
            return True
    if it.get("platform") == "小黑盒":
        has_xhh_technical = any(k in title for k in XHH_TECHNICAL_KWS)
        if not has_xhh_technical:
            return False
        if not any(k in title for k in ENTERTAINMENT_KWS):
            return True
    if any(k in title for k in ENTERTAINMENT_KWS) and not has_learning:
        return False
    if has_learning or has_hard:
        return True
    return False


def _bucket_mix(kept):
    """按来源桶加权交错混排: 同词/同源/同平台内容按占比均匀散布, 避免整屏同类扎堆。
    旧版普通轮转的缺陷: 桶大小不均时小桶先耗尽, 大桶剩余内容尾部连片输出(扎堆根源);
    现改为 credit 加权轮转(权重=桶大小), 大桶按比例散布而非堆积; 桶内先洗牌打散抓取顺序。"""
    buckets = {}
    for it in kept:
        buckets.setdefault(it.get("src") or it.get("gid") or "other", []).append(it)
    for b in buckets.values():
        random.shuffle(b)
    if len(buckets) <= 1:
        return list(next(iter(buckets.values()), []))
    live = dict(buckets)
    W = float(sum(len(b) for b in live.values()))
    credit = {k: 0.0 for k in live}
    out = []
    for _ in range(len(kept)):
        cand = [k for k in live if live[k]]
        if not cand:
            break
        for k in cand:
            credit[k] += len(live[k])
        best = max(credit[k] for k in cand)
        ties = [k for k in cand if credit[k] == best]
        pick = random.choice(ties) if len(ties) > 1 else ties[0]
        out.append(live[pick].pop())
        credit[pick] -= W
        if not live[pick]:
            del live[pick]
    return out


# 平台注册表: (platform显示名, 权重设置key, 默认权重); 新增平台在此注册即可(如小黑盒)
PLATFORMS = [
    ("B站", "bili_weight", 90),
    ("贴吧", "tb_weight", 25),
    ("小黑盒", "xhh_weight", 15),
]
_PLATFORM_WKEYS = {p[1] for p in PLATFORMS}


def platform_weights():
    """当前所有平台权重 dict(name→w)。0=该平台不出。"""
    out = {}
    for name, key, default in PLATFORMS:
        try:
            w = int(sget(key) or default)
        except Exception:
            w = default
        out[name] = w if w in {0, 25, 50, 75, 100} else default
    return out


def _enforce_platform_spacing(items, max_run=2):
    """最终出口防平台连发: 只要存在其他平台候选, 同平台最多连续 max_run 条。
    通过向后寻找其他平台并交换, 不丢条目、不改变各平台总量。"""
    out = list(items)
    for i in range(len(out)):
        pname = out[i].get("platform") or "B站"
        start = max(0, i - max_run + 1)
        if i - start + 1 < max_run + 1:
            continue
        if all((out[j].get("platform") or "B站") == pname for j in range(start, i + 1)):
            swap = next((j for j in range(i + 1, len(out))
                         if (out[j].get("platform") or "B站") != pname), None)
            if swap is not None:
                out[i], out[swap] = out[swap], out[i]
    return out


def mix_sources(kept):
    """N平台加权混排(credit加权轮转): 每个平台累计信用额 = 权重, 每步取信用最高的平台,
    取后扣减总权重 → 严格按权重比例均匀交错, 同平台不会扎堆。
    权重语义: 占比 = w_platform / Σw; 0=该平台不出; 全0兜底用默认。"""
    weights = platform_weights()
    groups = {}
    for it in kept:
        pname = it.get("platform") or "B站"
        groups.setdefault(pname, []).append(it)
    if len(groups) <= 1:
        return _enforce_platform_spacing(_bucket_mix(kept))
    active = {n: w for n, w in weights.items() if n in groups and w > 0}
    if not active:
        active = {n: weights.get(n, 5) for n in groups}
    if len(active) == 1:
        return _enforce_platform_spacing(_bucket_mix(groups[next(iter(active))]))
    mixed = {n: _bucket_mix(g) for n, g in groups.items() if n in active}
    total = sum(len(v) for v in mixed.values())
    if total == 0:
        return []
    # credit 加权轮转: 每步 credit[n] += w[n], 选信用最高且有货的平台, 取后 credit -= Σw
    # 平局时随机(不固定顺序), 让混排更"随机散布"
    W = float(sum(active.values()))
    credit = {n: 0.0 for n in mixed}
    pos = {n: 0 for n in mixed}
    out = []
    last_platform, same_run = None, 0
    for _ in range(total):
        for n in mixed:
            credit[n] += active[n]
        cand = [n for n in mixed if pos[n] < len(mixed[n])]
        if not cand:
            break
        # 平台比例不能以连续堆叠的方式体现: 只要还有其他平台候选, 同平台最多连2条。
        spaced = [n for n in cand if not (n == last_platform and same_run >= 2)]
        if spaced:
            cand = spaced
        best = max(credit.get(n, 0) for n in cand)
        ties = [n for n in cand if credit[n] == best]
        pick = random.choice(ties) if len(ties) > 1 else ties[0]
        out.append(mixed[pick][pos[pick]])
        pos[pick] += 1
        credit[pick] -= W
        if pick == last_platform:
            same_run += 1
        else:
            last_platform, same_run = pick, 1
    return _enforce_platform_spacing(out)


# 同作者降重: 同一作者在单个计划窗口内最多出现 AUTHOR_PLAN_QUOTA 条, 超出的留在池中
# 不入计划 —— 随着已看内容被消费, 后续构建的全局重混会把它们分批"填回来"(视觉降重)。
AUTHOR_PLAN_QUOTA = 3
_AUTHOR_SPACING_GAP = 6   # 同作者/同指纹标题在计划中的最小间隔(尽力满足, 不丢失条目)


def _spacing_keys(it):
    """视觉降重的间距键(多维): 同作者任意标题算同类; 同指纹标题跨作者也算同类。"""
    keys = ["a:" + (it.get("author") or "")]
    fp = _title_fp(it.get("title") or "")
    if fp:
        keys.append("f:" + fp)
    return keys


def _author_quota_hold(order):
    """同作者配额: 超出配额的条目本轮不进计划(留在池中, 等后续构建重混分批放出)。
    池子本身作者极少(≤3)时跳过配额 —— 否则会掏空供给, 此时只靠间距打散。"""
    authors = {it.get("author") or "" for it in order}
    if len(authors) <= 3:
        return order
    counts = {}
    out = []
    for it in order:
        a = it.get("author") or ""
        if a:
            counts[a] = counts.get(a, 0) + 1
            if counts[a] > AUTHOR_PLAN_QUOTA:
                continue   # 留池: 不丢弃, 后续构建达到配额空位时自然重新进入计划
        out.append(it)
    return out


def _space_same_author(items, gap=_AUTHOR_SPACING_GAP):
    """同作者/相似标题打散: 贪心重排, 优先取"间距键不在近期窗口"的条目;
    实在避不开时重置窗口放行(宁相邻不丢失), 保持各平台混排比例不变。"""
    recent = collections.deque()
    recent_set = set()
    pool = list(items)
    out = []
    while pool:
        pick_idx = None
        for i, it in enumerate(pool):
            if not (set(_spacing_keys(it)) & recent_set):
                pick_idx = i
                break
        if pick_idx is None:
            pick_idx = 0       # 剩余全是同作者/相似标题: 放最前并重置窗口, 避免死循环
            recent.clear()
            recent_set.clear()
        it = pool.pop(pick_idx)
        for k in _spacing_keys(it):
            recent.append(k)
            recent_set.add(k)
        while len(recent) > gap * 2:
            old = recent.popleft()
            recent_set.discard(old)
        out.append(it)
    return out


# ================================================================
# 推送套餐引擎(依据 规范.txt 五层蓝图落地):
#   规则① 平台+一级分类 插空轮询打散(在槽位交错内实现)
#   规则② 标题指纹去重(本次推送 + 2h 跨构建窗口)
#   槽位套餐: 舒适60% / 拓展20% / 探索10% / 热点10%(候选不足自动重分配, 宁多勿空)
#   时效分层: 资讯24h保鲜(machine_filter), 干货常青(探索区可复现老干货)
#   跨会话防重复: 记住上次会话末尾5条类型, 新会话前5条避开
#   时段适应: 晚间(22-6)多轻内容, 周末早晨(6-10)多深度内容
#   实时干预: 聊天框"未来X小时内别推Y"临时屏蔽 / "探索"临时拉高探索区配额
# ================================================================

SLOT_WEIGHTS = {"comfort": 60, "expand": 20, "explore": 10, "social": 10}
SLOT_NAMES = {"comfort": "舒适区", "expand": "拓展区", "explore": "探索区", "social": "热点"}

NEWS_KWS = ("新闻", "资讯", "突发", "官宣", "发布", "上线", "更新公告", "版本更新", "降价", "涨价",
            "发布会", "首曝", "宣布", "上新", "开售", "发售", "停售", "下架", "翻车", "道歉", "回应",
            "热搜", "热点", "实锤", "最新进展", "辟谣", "通报", "公示", "直播", "预告")
CLICKBAIT_KWS = ("惊了", "居然", "竟然", "万万没想到", "99%的人", "不看后悔", "看完惊呆",
                 "炸裂", "颠覆认知", "彻底改变", "一夜之间", "背后真相", "细思极恐", "你绝对",
                 "必看", "秒懂", "天塌了", "全网疯传", "刷屏", "爆火", "神反转", "当场", "惊呆了",
                 "最后悔", "血赚", "血亏", "逆天", "还能这样", "直接看傻", "不会吧不会吧")
FLAME_SLURS = ("开喷", "对线", "孝子", "结晶", "薄纱", "暴杀", "滚出", "别洗了", "急了", "破防了",
               "带节奏", "拉踩", "碰瓷", "饭圈", "脑残粉", "黑子", "水军", "恰烂钱", "割韭菜", "智商税")
LIGHT_AREAS = ("娱乐", "搞笑", "日常", "生活", "音乐", "鬼畜", "综艺", "影视")
DEEP_AREAS = ("纪录片", "知识", "科技", "人文历史")

_fp_recent = {}          # 标题指纹 → 时间戳(2h 跨构建窗口, 防同一条新闻反复重推)
_fp_lock = threading.Lock()
_plan_lock = threading.Lock()
_plan_cache = {"key": None, "order": [], "token": -1, "epoch": -1, "stail": None}   # 套餐规划缓存(分页顺序稳定)
_last_serve_ts = [0.0]   # 上次出流时间(30分钟无请求 = 新会话)
_score_epoch = 0         # 评分写入世代: 每次有新评分落库 +1(规划缓存随之失效, 全扫模式渐进出新内容)


def _title_fp(title):
    """标题指纹(规则②): 拉丁词/数字 + 中文二元组, 去重排序取前24字符。
    同主题不同措辞的标题共享大量二元组 → 指纹相同被合并, 杜绝"同一新闻换个说法反复推"。"""
    t = (title or "").lower()
    toks = re.findall(r"[a-z0-9]{2,}", t)
    zh = re.sub(r"[^\u4e00-\u9fff]", "", t)
    toks += [zh[i:i + 2] for i in range(max(0, len(zh) - 1))]
    if not toks:
        return "x" + t[:20]
    return "".join(sorted(set(toks)))[:24]


def _fp_dup(fp, now):
    """指纹是否在2h窗口内已推过(顺带记录与清理过期指纹)。"""
    with _fp_lock:
        for k in [k for k, ts in _fp_recent.items() if now - ts > 7200]:
            _fp_recent.pop(k, None)
        hit = fp in _fp_recent
        _fp_recent[fp] = now
        return hit


def _fp_clear():
    """清空指纹记忆: 信息池清空(刷新/清池)时同步调用,
    否则重建会拉回同样内容 → 被旧指纹全部拦截 → 信息流永远为空。"""
    with _fp_lock:
        _fp_recent.clear()


def _cat_key(it):
    """一级分类键(规则①的桶键): 平台 + 粗分类。"""
    p = it.get("platform") or "B站"
    cat = (it.get("area") or it.get("tname") or it.get("xcomm")
           or (it.get("topic") or "").split("、")[0] or (it.get("tag") or "").split(",")[0]
           or it.get("src") or "").strip()
    return "%s|%s" % (p, cat[:10] or "综合")


def _item_tags(it):
    vals = []
    if it.get("platform") == "贴吧":
        vals = [it.get("tname"), it.get("tclass"), it.get("tag")]
    elif it.get("platform") == "小黑盒":
        vals = [it.get("xtopic"), it.get("xcomm"), it.get("tag"), it.get("tname")]
    else:
        vals = [it.get("tag"), it.get("tname"), it.get("area"), it.get("topic")]
    out = []
    for v in vals:
        for part in re.split(r"[,，、/|;；\s]+", str(v or "")):
            p = part.strip("#[]【】()（） ")
            if p and p not in out:
                out.append(p)
            if len(out) >= 3:
                return " / ".join(out)
    return " / ".join(out) if out else (it.get("platform") or "综合")


def _is_newsy(it):
    """资讯/热点判定(时效分层用): 标题或来源命中新闻词。"""
    t = ((it.get("title") or "") + (it.get("tname") or "")).lower()
    return any(k in t for k in NEWS_KWS)


def _is_evergreen(it):
    """常青干货判定: 教程/深度词或知识类来源(探索区可跨时间窗复现)。"""
    text = ((it.get("title") or "") + (it.get("tag") or "") + (it.get("tname") or "")).lower()
    if any(k in text for k in SERIOUS_KWS + DEPTH_KWS):
        return True
    return (it.get("area") or it.get("tname") or "") in DEEP_AREAS


def _emo_bad(it):
    """情绪过滤器(纯规则零token): 感叹号轰炸/引战黑话/标题党句式 → 返回原因, 通过返回''。
    只对标题判定(简介可能引用原文, 防误伤)。"""
    t = (it.get("title") or "").lower()
    ex = t.count("!") + t.count("！")
    if ex >= 3 or (ex >= 2 and len(t) <= 12):
        return "情绪"
    if any(k in t for k in FLAME_SLURS):
        return "引战"
    if any(k in t for k in CLICKBAIT_KWS):
        return "标题党"
    return ""


def tmp_block_kws():
    """临时屏蔽词(聊天指令写入, 到点自动解封并清理)。"""
    raw = sget("tmp_blocks") or ""
    try:
        blocks = json.loads(raw) if raw else []
    except Exception:
        blocks = []
    now = time.time()
    alive = [b for b in blocks if isinstance(b, dict) and b.get("k")
             and float(b.get("until") or 0) > now]
    if len(alive) != len(blocks):
        sset("tmp_blocks", json.dumps(alive, ensure_ascii=False))
    return [str(b["k"]).lower() for b in alive]


def add_tmp_block(kw, minutes):
    """写入一条临时屏蔽(与旧块合并去重)。"""
    raw = sget("tmp_blocks") or ""
    try:
        blocks = json.loads(raw) if raw else []
    except Exception:
        blocks = []
    blocks = [b for b in blocks if isinstance(b, dict) and b.get("k") and b.get("k") != kw]
    blocks.append({"k": kw, "until": time.time() + max(1, minutes) * 60})
    sset("tmp_blocks", json.dumps(blocks, ensure_ascii=False))


def _profile_signals():
    """舒适区信号(感知层): 偏好词 + 喜欢作者 + 近期点击主题/作者/领域(隐式信号)。"""
    prefs = [k.lower() for k in pref_all_kws()]
    authors = {r[0] for r in db().execute(
        "SELECT author FROM feedback WHERE action='like'").fetchall() if r[0]}
    topics, areas = set(), set()
    try:
        rows = db().execute("SELECT key FROM history ORDER BY ts DESC LIMIT 60").fetchall()
    except Exception:
        rows = []
    for (k,) in rows:
        it = item_by_key(k)
        if not it:
            continue
        for t in ((it.get("topic") or "").split("、") + (it.get("tag") or "").split(",")
                  + [it.get("area") or "", it.get("tname") or ""]):
            t = t.strip()
            if 2 <= len(t) <= 8:
                topics.add(t)
        if it.get("author"):
            authors.add(it["author"])
        areas.add(_cat_key(it))
    return {"prefs": prefs, "authors": authors,
            "topics": {t.lower() for t in topics}, "areas": areas}


def _slot_of(it, sig, med, now):
    """槽位分类: 舒适(画像命中) / 热点(资讯+显著高热) / 拓展(优质) / 兜底舒适。"""
    text = ((it.get("title") or "") + " " + (it.get("desc") or "") + " " +
            (it.get("tag") or "") + " " + (it.get("tname") or "") +
            " " + (it.get("topic") or "") + " " + (it.get("author") or "")).lower()
    if any(k in text for k in sig["prefs"]) or (it.get("author") or "") in sig["authors"]:
        return "comfort"
    topic = (it.get("topic") or "").strip()
    if topic and topic.lower() in sig["topics"]:
        return "comfort"
    if _cat_key(it) in sig["areas"]:
        return "comfort"
    if _is_newsy(it) and it.get("pubdate") and now - it["pubdate"] < 86400:
        return "social"
    if (it.get("views") or 0) >= med * 2:
        return "social"
    if (it.get("interest") or 5) >= 7 or (it.get("views") or 0) >= med:
        return "expand"
    return "comfort"


def _explore_candidates(exclude, limit=30):
    """探索区候选池(进化层): 库里7天前入库的老而优干货(播放≥10w 或常青判定),
    打破茧房跨领域复现; 排除近期已看/厌恶/情绪/资讯类与全部负反馈内容。"""
    dkws = dislike_keywords()
    neg_disliked = {r[0] for r in db().execute(
        "SELECT DISTINCT key FROM feedback WHERE action='dislike'").fetchall()}
    neg_authors = {r[0] for r in db().execute("SELECT value FROM blacklist WHERE kind='author'")}
    neg_kws = [r[0].lower() for r in db().execute("SELECT value FROM blacklist WHERE kind='keyword'")]
    neg_tmp = tmp_block_kws()
    out = []
    try:
        rows = db().execute(
            "SELECT json FROM items WHERE fetched_at < ? AND key NOT IN "
            "(SELECT key FROM shown WHERE ts > ?) ORDER BY RANDOM() LIMIT 120",
            (time.time() - 7 * 86400, time.time() - 6 * 3600)).fetchall()
    except Exception:
        return out
    for (j,) in rows:
        try:
            it = json.loads(j)
        except Exception:
            continue
        if not isinstance(it, dict) or it.get("key") in exclude or not it.get("views"):
            continue
        if _emo_bad(it) or _is_newsy(it):
            continue
        text = ((it.get("title") or "") + " " + (it.get("desc") or "") +
                " " + (it.get("tag") or "")).lower()
        if dkws and any(k in text for k in dkws):
            continue
        # 探索区同样执行负反馈: 已不喜欢/黑名单/临时屏蔽的内容不得借"探索"回流。
        if it.get("key") in neg_disliked or it.get("author") in neg_authors:
            continue
        if (neg_kws or neg_tmp) and (any(k in text for k in neg_kws) if neg_kws else False):
            continue
        if neg_tmp and any(k in (text + " " + (it.get("author") or "")).lower() for k in neg_tmp):
            continue
        if it["views"] >= 100000 or _is_evergreen(it):
            out.append(it)
        if len(out) >= limit:
            break
    return out


def _time_boost(it, now):
    """时段适应(短周期): 晚间轻内容加权, 周末早晨深度内容加权(桶内稳定排序用)。"""
    lt = time.localtime(now)
    h, wd = lt.tm_hour, lt.tm_wday
    b = 0
    if h >= 22 or h < 6:
        dur = it.get("duration") or 0
        if 0 < dur <= 360 or (it.get("tname") or "") in LIGHT_AREAS:
            b += 3
    if wd >= 5 and 6 <= h < 10:
        if (it.get("duration") or 0) >= 1200 or (it.get("tname") or "") in DEEP_AREAS or _is_evergreen(it):
            b += 3
    return b


def _interleave_slots(buckets, weights):
    """槽位 credit 加权交错(同 mix_sources): 平局随机, 保证各槽按比例均匀散布不扎堆。"""
    total = sum(len(v) for v in buckets.values())
    if total == 0:
        return []
    keys = [k for k in buckets if buckets[k]]
    if len(keys) == 1:
        return list(buckets[keys[0]])
    random.shuffle(keys)
    wsum = float(sum(weights.get(k, 0) for k in keys))
    credit = {k: 0.0 for k in keys}
    pos = {k: 0 for k in keys}
    out = []
    for _ in range(total):
        for k in keys:
            credit[k] += weights.get(k, 0)
        cand = [k for k in keys if pos[k] < len(buckets[k])]
        if not cand:
            break
        best = max(credit[k] for k in cand)
        ties = [k for k in cand if credit[k] == best]
        pick = random.choice(ties) if len(ties) > 1 else ties[0]
        out.append(buckets[pick][pos[pick]])
        pos[pick] += 1
        credit[pick] -= wsum
    return out


def planned_feed(acts, new_session=False, exclude=None):
    """信息流套餐规划(策略层): 门禁(严肃/AI新维度/临时屏蔽) → 槽位分类 → 加权交错。
    返回 (有序列表, 槽位统计); 池太薄时按需退化, 绝不丢条目。
    exclude: 已在上游规划中的 key(追加规划时用, 防探索候选重复进入)。"""
    exclude = exclude or set()
    mode = sget("mode")
    full = ai_on() and sget("ai_full_scan") == "1"
    cut = MODE_THRESH.get(mode, 6)
    imin = int(sget("ai_interest_min") or 0)
    now = time.time()
    # --- 门禁(信息流路径总入口; 搜索路径仍走原逻辑) ---
    if mode == "严肃":
        acts = [it for it in acts if is_serious_ok(it)]
    keep, dropped = [], 0
    tmp = tmp_block_kws()
    scores = {}
    dkws = dislike_keywords() if full else []
    if full:
        try:
            cached, _ = _load_cache(acts, score_ttl())
            scores = cached
        except Exception:
            scores = {}
        if not scores:
            full = False   # 冷启动降级: 尚无任何评分 → 临时按热度放行, 首批分出来后再恢复门禁(避免长时间空白)
    sig = _profile_signals()
    for it in acts:
        if it.get("_range_ok") is False:
            continue
        if tmp:
            text = ((it.get("title") or "") + (it.get("tag") or "") + (it.get("tname") or "")
                    + (it.get("topic") or "") + (it.get("desc") or "")).lower()
            if any(k in text for k in tmp):
                dropped += 1
                continue
        if full:
            sc = scores.get(it["key"])
            if not sc:
                dropped += 1
                continue   # 门禁: 未评分不展示(后台渐进补评分)
            if sc.get("flame", 0) >= cut or sc.get("spam", 0) >= SPAM_CUT:
                dropped += 1
                continue
            if (sc.get("interest", 5) or 0) == 0:
                dropped += 1
                continue
            if imin and sc.get("interest", 5) < imin:
                dropped += 1
                continue
            if "emo" in sc and (sc.get("emo") or 0) <= -6:
                dropped += 1
                continue   # 情绪净值极负(纯负能量/贩卖焦虑) → 一票剔除
            if mode == "严肃" and "density" in sc and (sc.get("density") or 0) < 4:
                dropped += 1
                continue   # 严肃模式: 信息密度门槛(废话多不放行)
            if "facts" in sc and not sc.get("facts") and _is_newsy(it) and (sc.get("density") or 0) < 6:
                dropped += 1
                continue   # 资讯无事实锚点+低密度 → 纯情绪输出
            if dkws and any(k in (it.get("title", "") + sc.get("topic", "") + it.get("desc", "")) for k in dkws):
                dropped += 1
                continue
            it["reason"] = sc.get("reason", "") or ""
            it["topic"] = sc.get("topic", "") or ""
            it["interest"] = sc.get("interest", 5)
        keep.append(it)
    if dropped:
        bump_stats(filtered=dropped)
        _funnel_bump("ai_gate", dropped)
    acts = keep
    # 冷却是硬性排除，不因候选池变薄而放宽。
    cooling = cooldown_keys(now)
    if cooling:
        dropped_cool = sum(1 for it in acts if it.get("key") in cooling)
        if dropped_cool:
            _funnel_bump("cooldown", dropped_cool)
        acts = [it for it in acts if it.get("key") not in cooling]
    # 已看窗口过滤: 近期看过的条目不再进规划(池子太薄时自动放宽, 与 bake 同策略)
    seen_h = int(sget("m_seen_hours") or 6)
    if seen_h > 0:
        try:
            recent = {r[0] for r in db().execute(
                "SELECT key FROM shown WHERE ts > ?", (now - seen_h * 3600,)).fetchall()}
        except Exception:
            recent = set()
        if recent:
            fresh = [it for it in acts if it["key"] not in recent]
            # 已看窗口默认严格执行; 仅当剔除后不足一页(最后手段)才保留近期已看, 避免空页。
            if len(fresh) >= 30:
                acts = fresh
    # 候选不足一页时，按最近一次展示时间最早的冷却项少量复用，避免冷却策略把信息流锁空。
    if len(acts) < 30 and cooling:
        active_keys = {it.get("key") for it in acts}
        cooled = [it for it in keep if it.get("key") in cooling and it.get("key") not in active_keys]
        if cooled:
            state_rows = {r[0]: r[1] for r in db().execute(
                "SELECT key,last_presented_at FROM item_exposure_state WHERE key IN (%s)" %
                ",".join("?" * len(cooled)), tuple(it["key"] for it in cooled)).fetchall()}
            cooled.sort(key=lambda it: state_rows.get(it.get("key"), 0))
            for it in cooled[:30 - len(acts)]:
                it["_cooldown_relaxed"] = True
                acts.append(it)
                _funnel_bump("cooldown_relaxed")
    if len(acts) <= 8:   # 池太薄: 不强行套餐, 保持原有排序(宁多勿空)
        for it in acts:
            it.pop("_slot", None)
        return acts, {}
    # --- 槽位分类 ---
    views = sorted(it.get("views", 0) for it in acts)
    med = views[len(views) // 2] if views else 0
    avoid = set()
    if new_session:
        try:
            avoid = set(json.loads(sget("last_types") or "[]"))
        except Exception:
            avoid = set()
    buckets = {"comfort": [], "expand": [], "explore": [], "social": []}
    for it in acts:
        if it.get("key") in exclude:
            continue   # 上游规划已含该条目, 不重复进套餐
        slot = _slot_of(it, sig, med, now)
        it["_slot"] = SLOT_NAMES[slot]
        buckets[slot].append(it)
    for it in _explore_candidates({x["key"] for x in acts} | exclude):
        if it.get("key") in cooling:
            continue
        it["_slot"] = SLOT_NAMES["explore"]
        buckets["explore"].append(it)
    # 时段适应: 桶内按 (时段加权, 原顺序) 稳定排序(保持热度大序, 只做轻推)
    for k in buckets:
        order = list(enumerate(buckets[k]))
        order.sort(key=lambda p: (-_time_boost(p[1], now), p[0]))
        buckets[k] = [b for _, b in order]
    # 权重自适应: 候选不足的槽位把额度分给有货的槽位; 探索指令生效时临时拉高探索区
    weights = dict(SLOT_WEIGHTS)
    if float(sget("explore_boost_ts") or 0) > now:
        weights = {"comfort": 40, "expand": 15, "explore": 30, "social": 15}
    active = {k: v for k, v in weights.items() if buckets.get(k)}
    if not active:
        return acts, {}
    total_w = float(sum(active.values()))
    weights = {k: (active[k] / total_w * 100.0) for k in active}
    order = _interleave_slots(buckets, weights)
    order = mix_sources(order)   # 最终出口再按平台权重重排, 避免槽位套餐冲淡贴吧/小黑盒比例
    order = _author_quota_hold(order)   # 同作者配额: 超出的留池, 后续构建分批放出(视觉降重)
    order = _space_same_author(order)   # 同作者/相似标题打散: 不再相邻出现
    # 跨会话防重复: 新会话前5条强制避开上次会话末尾的类型
    if avoid and len(order) > 5:
        head, rest, used = [], [], set()
        for it in order:
            if len(head) < 5 and _cat_key(it) not in avoid:
                head.append(it)
                used.add(id(it))
        for it in order:
            if id(it) not in used:
                rest.append(it)
        order = head + rest
    slots = {}
    for k, name in SLOT_NAMES.items():
        n = sum(1 for it in order if it.get("_slot") == name)
        if n:
            slots[name] = n
    return order, slots


def _slot_reason(it):
    """可解释性: 槽位标签 + AI理由; 无AI理由时显示内容标签/吧名/话题。"""
    slot = it.get("_slot") or ""
    reason = (it.get("reason") or "").strip()
    if not slot:
        return reason or _item_tags(it)
    if reason.startswith(slot + "·"):
        return reason
    return "%s·%s" % (slot, reason or _item_tags(it))


def cooldown_keys(now=None):
    now = int(now or time.time())
    try:
        return {r[0] for r in db().execute(
            "SELECT key FROM item_exposure_state WHERE cooldown_until > ?", (now,)).fetchall()}
    except Exception:
        return set()


def record_feed_presentations(items, request_id, now=None):
    """只在卡片实际返回给信息流时累计展示；同一请求重试不会重复计数。"""
    if not request_id:
        return
    now = int(now or time.time())
    try:
        limit = max(1, int(sget("m_repeat_limit") or 4))
        hours = max(0, int(sget("m_repeat_cooldown_hours") or 72))
    except Exception:
        limit, hours = 4, 72
    with _db_lock:
        for it in items:
            key = it.get("key")
            if not key:
                continue
            cur = db().execute("INSERT OR IGNORE INTO item_presentation(key,request_id,presented_at) VALUES(?,?,?)",
                               (key, request_id, now))
            if cur.rowcount == 0:
                continue
            row = db().execute("SELECT run_count,cooldown_until FROM item_exposure_state WHERE key=?", (key,)).fetchone()
            if it.get("_cooldown_relaxed"):
                # 冷却期间被复用: 计数继续递增, 严格保留原冷却截止(既不清零也不续期)。
                run = (row[0] if row else 1) + 1
                base_cooldown = row[1] if row else 0
                db().execute("INSERT INTO item_exposure_state(key,run_count,last_presented_at,cooldown_until) VALUES(?,?,?,?) "
                             "ON CONFLICT(key) DO UPDATE SET run_count=excluded.run_count,last_presented_at=excluded.last_presented_at,cooldown_until=excluded.cooldown_until",
                             (key, run, now, base_cooldown))
                continue
            run = 1 if not row or (row[1] > 0 and row[1] <= now) else row[0] + 1
            cooldown = now + hours * 3600 if hours and run >= limit else 0
            db().execute("INSERT INTO item_exposure_state(key,run_count,last_presented_at,cooldown_until) VALUES(?,?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET run_count=excluded.run_count,last_presented_at=excluded.last_presented_at,cooldown_until=excluded.cooldown_until",
                         (key, run, now, cooldown))
        db().commit()


def _feed_plan(acts, new_session, gen):
    """套餐规划带缓存, 返回有序列表(无限滚动分页顺序稳定):
    - 设置令牌/评分epoch/设置项都未变, 且规划条目仍是当前池子集 → 视为纯追加:
      新条目单独规划后接在尾部, 已有顺序不动(offset 不错位);
    - 令牌变化(刷新/清池/切分区)/评分落库/设置变化/新会话 → 全量重规划(应用跨会话防重复)。
    seq: 重规划代数; 每次全量重规划 +1, 会话游标据此归零重扫(已返回 key 由会话集合跳过)。"""
    stail = (sget("mode"), ai_on(), sget("ai_full_scan"),
             sget("ai_interest_min"), sget("m_seen_hours"),
             sget("dislike_note") or "", sget("tmp_blocks") or "",
             sget("explore_boost_ts") or "0",
             sget("feedback_sig") or "0")   # 负反馈信号: 点踩后强制重排, 计划中的不喜欢条目立即剔除
    key = (len(acts), _uni_token, _score_epoch, stail)
    with _plan_lock:
        c = _plan_cache
        if c["key"] == key:
            return c["order"]
        plan_keys = {it["key"] for it in c["order"]} if c.get("order") else set()
        act_keys = {it["key"] for it in acts}
        appended_only = (not new_session and c.get("token") == _uni_token
                         and c.get("epoch") == _score_epoch and c.get("stail") == stail
                         and plan_keys and plan_keys <= act_keys)
        if appended_only:
            # 追加时不做"新批拼尾": 未消费后段整体重新混排。
            # 原因: B站热榜跨构建重叠大(新批B站越来越少), 贴吧/小黑盒游标轮换持续出新,
            # 逐批拼尾会让非B站沉积在计划末尾形成扎堆带; 全局重混让新到的B站插回中间。
            with _session_lock:
                latest = max(_session_served.values(), key=lambda s: s["ts"]) if _session_served else None
                boundary = latest["cursor"] if latest else 0
                served_union = set(latest["keys"]) if latest else set()
            prefix = c["order"][:boundary]   # 已消费前缀: 最新会话游标之前的内容保持不动
            suffix_in = [it for it in acts if it["key"] not in served_union
                         and it["key"] not in {p["key"] for p in prefix}]
            t, _ = planned_feed(suffix_in, False)
            if len(prefix) != boundary:   # 旧计划被裁剪等异常: 回退全量重排
                t, _ = planned_feed(acts, False)
                prefix = []
            c["order"] = prefix + t
            c["seq"] = c.get("seq", 0) + 1   # 后段已重混 → 会话游标归零重扫(已返回 key 被跳过)
        else:
            c["order"], _ = planned_feed(acts, new_session)
            c["seq"] = c.get("seq", 0) + 1   # 重规划代数+1 → 会话游标归零重扫(防重复/防漏)
        c.update(key=key, token=_uni_token, epoch=_score_epoch, stail=stail)
        return c["order"]


# 会话级已返回集合: sid → {"keys":已返回key, "cursor":计划游标, "seq":重规划代数, "ts"}
_session_served = {}
_session_lock = threading.Lock()


def _session_state(sid):
    """取/建会话状态(容量8, 淘汰最旧); 无 sid 返回 None。"""
    if not sid:
        return None
    with _session_lock:
        s = _session_served.get(sid)
        if s is None:
            if len(_session_served) >= 8:
                for old in sorted(_session_served, key=lambda k: _session_served[k]["ts"])[:len(_session_served) - 7]:
                    del _session_served[old]
            s = {"keys": set(), "cursor": 0, "seq": 0, "ts": time.time()}
            _session_served[sid] = s
        s["ts"] = time.time()
        return s


_last_full_build_ts = 0   # 上次完整补池完成时间(节流冗余全量抓取)


def build_universe(quick=False):
    """构建新世代(绑定当前设置令牌)。quick=True 只取2个源秒开屏, 之后后台线程补全。
    令牌变更(切分区等)后, 正在跑的旧构建结果自动作废, 不会覆盖新设置。
    注: 原"首批≥10条立即发布"的特殊首页机制已删除——刷新/启动时全部内容统一
    走同一套 抓取→入库→bake→发布 管线, 不再有前几条的捷径(它导致刷新后内容不变)。"""
    with _uni_lock:
        if _universe["building"]:
            return None
        _universe["building"] = True
        _universe["mode"] = "quick" if quick else "full"   # full 构建期间不被 quick 打断
        token = _uni_token
        _funnel_bump("build_quick" if quick else "build_full")

    try:
        payload = fetch_pool(quick=quick)
        if not payload["offline"]:
            now = time.time()
            keys = [it["key"] for it in payload["items"]]
            ph = ",".join("?" * len(keys)) if keys else ""
            exist = {r[0] for r in db().execute(
                "SELECT key FROM items WHERE key IN (%s)" % ph, tuple(keys))} if ph else set()
            new_n = len(set(keys) - exist)   # 只计真正新增的条目
            with _db_lock:   # 入库: 沉淀池基础 + 7天清理
                for it in payload["items"]:
                    old = db().execute("SELECT json FROM items WHERE key=?", (it["key"],)).fetchone()
                    if old:
                        try:
                            prev = json.loads(old[0])
                        except Exception:
                            prev = {}
                    db().execute("INSERT INTO items(key,json,fetched_at,score_at) VALUES(?,?,?,0) "
                                 "ON CONFLICT(key) DO UPDATE SET json=excluded.json,fetched_at=excluded.fetched_at",
                                 (it["key"], json.dumps(it, ensure_ascii=False), now))
                db().execute("DELETE FROM items WHERE fetched_at < ?", (now - 7 * 86400,))
                db().commit()
            bump_stats(received=new_n)   # 今日收录只累计新增
            payload["items"] = payload["items"] + backlog_items(
                {it["key"] for it in payload["items"]}, enabled_source_ids())
        kept = bake_universe(payload, quick=quick)
        threading.Thread(target=ensure_tags, kwargs={"items": kept}, daemon=True).start()   # 后台补标签(厌恶匹配用)
        offline, source = payload["offline"], payload["source"]
        if token != _uni_token:   # 设置已变, 本构建作废
            return None
        with _uni_lock:
            first_fill = not _universe["items"]
            if first_fill:
                new_items, added = kept, kept   # 池空: 首次构建直接填充
            else:
                # 持续追加(去重): 无限滚动不清空不重置; 超上限裁最旧头部(offset 极深时才触发)
                existing = {it["key"] for it in _universe["items"]}
                _now = time.time()
                added = [it for it in kept if it["key"] not in existing
                         and not _fp_dup(_title_fp(it["title"]), _now)]   # 2h同指纹只推一次
                new_items = list(_universe["items"]) + added
                if len(new_items) > MAX_UNIVERSE:
                    new_items = new_items[len(new_items) - MAX_UNIVERSE:]
            # built_at 仅在产出新条目时刷新: 空 quick 构建(抓回全是重复)不再推迟补池,
            # 否则薄池状态会被"空构建刷新时间戳"饿死, 用户始终等不到全量补池。
            _universe.update(gen=_universe["gen"] + 1, items=new_items, source=source,
                             offline=offline, ai=ai_on(), mode=sget("mode"),
                             account=account_slim().get("ok", False),
                             partitions=current_partition_names(),
                             built_at=int(time.time()) if (added or first_fill) else _universe["built_at"])
        _request_bg_score(kept)   # 当前批次候选入队, 后台渐进评分写库(交互路径只读缓存秒回)
        persist_cold_cache()   # 冷启动预缓存(5分钟节流)
        if not quick:
            global _last_full_build_ts
            _last_full_build_ts = time.time()
        if quick:   # 开屏后立即后台补全完整世代(榜单+相关链+搜索+随机)
            # 防堆叠: 刚做过完整补池时跳过这次全量(60s 内); 否则正常补全。
            if time.time() - _last_full_build_ts < 60:
                return _universe["gen"]

            def _full():
                # 等父线程 finally 清 building 标志, 消除启动竞态(否则补全线程入口即放弃)
                for _ in range(200):
                    with _uni_lock:
                        if not _universe["building"]:
                            break
                    time.sleep(0.05)
                build_universe(quick=False)
            threading.Thread(target=_full, daemon=True).start()
        return _universe["gen"]
    except Exception:
        return None
    finally:
        with _uni_lock:
            if token == _uni_token:   # 仍是最新设置版本才允许清标志
                _universe["building"] = False


def feed_refresh_item(key, q=""):
    """把 key 标记为「已看」(复用 shown 表 + m_seen_hours 机制), 然后从池子随机返回一条
    未看过的内容作为替换; 池子没有可用内容时先 fetch_pool 补池再抽。返回 item dict 或 None。
    q 非空(搜索模式): 只从「与搜索词相关(标题/摘要分词命中)」的未看内容里换, 不串台到无关内容。"""
    now_ts = int(time.time())
    with _db_lock:
        db().execute("INSERT OR REPLACE INTO shown(key, ts) VALUES(?,?)", (key, now_ts))
        db().execute("DELETE FROM shown WHERE ts < ?", (now_ts - 3 * 86400,))
        db().commit()
    seen_h = int(sget("m_seen_hours") or 6)
    seen_cut = now_ts - seen_h * 3600
    q = (q or "").strip()

    def _recent_seen():
        if seen_h <= 0:
            return set()
        try:
            with _db_lock:
                return {r[0] for r in db().execute(
                    "SELECT key FROM shown WHERE ts > ?", (seen_cut,)).fetchall()}
        except Exception:
            return set()

    def _pick():
        # 负反馈硬排除: 已不喜欢/已屏蔽/临时屏蔽的 key 与作者, "换一条"绝不递回。
        disliked = {r[0] for r in db().execute(
            "SELECT DISTINCT key FROM feedback WHERE action='dislike'").fetchall()}
        bl_rows = db().execute("SELECT kind,value FROM blacklist").fetchall()
        bl_authors = {v for k, v in bl_rows if k == "author"}
        bl_kws = [v.lower() for k, v in bl_rows if k == "keyword"]
        tmp_kws = tmp_block_kws()
        try:
            with _db_lock:
                if q:
                    # 搜索模式: 拉候选(最近入库) → python 分词过滤 → 随机换同主题未看内容
                    rows = db().execute(
                        "SELECT json FROM items WHERE key != ? ORDER BY fetched_at DESC LIMIT 600",
                        (key,)).fetchall()
                elif seen_h > 0:
                    row = db().execute(
                        "SELECT json FROM items WHERE key != ? AND key NOT IN "
                        "(SELECT key FROM shown WHERE ts > ?) AND key NOT IN "
                        "(SELECT key FROM item_exposure_state WHERE cooldown_until > ?) ORDER BY RANDOM() LIMIT 1",
                        (key, seen_cut, now_ts)).fetchone()
                    return row
                else:
                    row = db().execute(
                        "SELECT json FROM items WHERE key != ? ORDER BY RANDOM() LIMIT 1",
                        (key,)).fetchone()
                    return row
        except Exception:
            return None
        if not q:
            return None
        toks = [t for t in re.split(r"[\s,，。！？!?、;；]+", q.lower()) if len(t) >= 2]
        if not toks:
            return None
        rse = _recent_seen()
        cand = []
        for (j,) in rows:
            try:
                it = json.loads(j)
            except Exception:
                continue
            if it.get("key") == key or it.get("key") in rse or it.get("key") in disliked:
                continue
            if it.get("author") in bl_authors:
                continue
            text = ((it.get("title") or "") + " " + (it.get("desc") or "") +
                    " " + (it.get("tag") or "") + " " + (it.get("tname") or "") +
                    " " + (it.get("author") or "")).lower()
            if bl_kws and any(k in text for k in bl_kws):
                continue
            if tmp_kws and any(k in text for k in tmp_kws):
                continue
            if any(t in text for t in toks):
                cand.append(it)
        if not cand:
            return None
        it = random.choice(cand)
        return (json.dumps(it, ensure_ascii=False),)

    row = _pick()
    if not row:
        # 池子没有未看过的 → 先补池(抓新内容入池), 再抽一次
        try:
            fetch_pool(quick=True)
        except Exception:
            pass
        row = _pick()
    if not row:
        return None
    try:
        it = json.loads(row[0])
    except Exception:
        return None
    if not isinstance(it, dict):
        return None
    # 保证前端卡片渲染所需字段齐全
    for f in ("views", "likes", "reply", "pubdate", "duration", "author", "tname",
              "title", "desc", "cover", "url", "platform", "key", "reason"):
        it.setdefault(f, "" if f in ("author", "tname", "title", "desc", "cover",
                                     "url", "platform", "key", "reason") else (0 if f != "duration" else 0))
    _annotate_following([it])
    # 替换卡片同样记入 shown(否则它仍留在计划原位, 用户滚到那里会再看到一次)。
    try:
        with _db_lock:
            db().execute("INSERT OR REPLACE INTO shown(key, ts) VALUES(?,?)", (it["key"], int(time.time())))
            db().commit()
    except Exception:
        pass
    it.setdefault("danmaku", None)
    it.setdefault("covers", [it["cover"]] if it.get("cover") else [])
    it.setdefault("key", key)
    return it


def force_rebuild():
    """设置变更(切分区)时调用: 作废旧构建并在最新设置下立即重建。"""
    global _uni_token
    with _uni_lock:
        _uni_token += 1
        _universe["building"] = False   # 让新构建插队
    build_universe(quick=True)


_cold_last_save = [0]   # 预缓存节流游标


def persist_cold_cache(force=False):
    """把池首未展示条目按设置上限存库(冷启动预缓存)。
    持续持久化而非退出时保存: 手机上进程随时可能被系统杀掉。"""
    n = int(sget("m_cold_start_n") or 0)
    if n <= 0:
        if sget("cold_start_cache"):
            sset("cold_start_cache", "")
        return
    if not force and time.time() - _cold_last_save[0] < 300:   # 5分钟节流
        return
    with _uni_lock:
        items = list(_universe["items"])
    if not items:
        return
    try:
        shown = {r[0] for r in db().execute("SELECT key FROM shown")}
    except Exception:
        shown = set()
    keep = [it for it in items if it.get("key") not in shown][:n]
    if not keep:
        keep = items[:n]   # 兜底: 全部已展示过(极少)也缓存池首
    fields = ("key", "platform", "title", "author", "desc", "cover", "covers", "views",
              "views_known", "likes", "danmaku", "reply", "duration", "duration_known",
              "pubdate", "url", "tname", "area", "tag", "tclass", "gid", "src",
              "reason", "topic", "interest", "following", "xcomm", "xtopic")
    slim = [{k: it.get(k) for k in fields} for it in keep]
    sset("cold_start_cache", json.dumps({"ts": int(time.time()), "items": slim}, ensure_ascii=False))
    _cold_last_save[0] = time.time()


def _cold_start_preheat():
    """冷启动: 设置开启且有缓存 → 缓存条目注入空池立即展示。返回是否注入。
    注入后由后台 quick 构建补全完整池, 构建合并逻辑天然去重(按 key)。
    缓存用后不清空(手机进程随时可能死在"消费→重建保存"之间, 清了下次就空白):
    注入时按 shown 表过滤已看条目, 没看过的排前面, 全看过才原样注入(空屏比重复更糟)。
    下次 build_universe 末尾的 persist_cold_cache 会用新快照覆盖。"""
    n = int(sget("m_cold_start_n") or 0)
    if n <= 0:
        return False
    raw = sget("cold_start_cache")
    if not raw:
        return False
    try:
        cached = json.loads(raw)
    except Exception:
        return False
    if isinstance(cached, list):          # 旧格式(无时间戳): 视为已过期, 不再使用
        return False
    if not isinstance(cached, dict) or not isinstance(cached.get("items"), list):
        return False
    if time.time() - int(cached.get("ts") or 0) > 7 * 86400:   # 超7天: 内容太旧, 弃用重建
        sset("cold_start_cache", "")
        return False
    items = cached["items"]
    if not items:
        return False
    with _uni_lock:
        if _universe["items"]:
            return False
        # 已看过滤: 上次会话展示过的排后面(内容池去重按 key, 注入不会造成双份)
        try:
            seen = {r[0] for r in db().execute("SELECT key FROM shown")}
        except Exception:
            seen = set()
        fresh = [it for it in items if it.get("key") not in seen]
        old = [it for it in items if it.get("key") in seen]
        _universe["items"] = (fresh + old)[:max(n, len(items))]
        _universe["built_at"] = int(time.time())
    return True


def serve_chunk(offset=0, limit=30, q="", request_id=""):
    """取当前世代的 [offset:offset+limit]; 到底时自动触发新生代构建。"""
    with _uni_lock:
        uni = dict(_universe)
        items = list(_universe["items"])
        building = _universe["building"]
    if not items and not building:
        # 冷启动预缓存: 有缓存 → 注入空池并立即吐出(后台同时构建完整池); 没有才纯等待
        preheated = _cold_start_preheat()
        threading.Thread(target=build_universe, kwargs={"quick": True}, daemon=True).start()
        if not preheated:
            return {"ok": True, "items": [], "building": True, "gen": uni["gen"],
                    "source": uni["source"], "offline": uni["offline"],
                    "ai": uni["ai"], "mode": uni["mode"], "account": uni["account"],
                    "partitions": uni.get("partitions", "全部"), "offset": offset,
                    "next_offset": offset, "has_more": True}
        with _uni_lock:
            uni = dict(_universe)
            items = list(_universe["items"])
            building = _universe["building"]   # 构建线程已接管, 正常服务缓存条目
    acts = apply_filters(items, q)   # 黑名单/不喜欢/临时屏蔽始终生效; q 非空时额外做子集过滤
    new_session = (time.time() - _last_serve_ts[0]) > 1800   # 30分钟无请求 = 新会话(跨会话防重复)
    _last_serve_ts[0] = time.time()
    if not q:
        acts = _feed_plan(acts, new_session, uni["gen"])   # 推送套餐规划(含门禁)
    # 会话级游标分页(仅信息流): 同会话已返回的 key 永不重复; 计划重排后游标归零重扫,
    # 已返回 key 被跳过、未返回 key 绝不漏 —— 彻底消除"重排导致 offset 漂移"的重复/丢失。
    sid = (request_id or "").split(":")[0]
    sess = _session_state(sid) if not q else None
    if sess is not None:
        with _plan_lock:
            plan_seq = _plan_cache.get("seq", 0)
        if sess["seq"] != plan_seq:
            sess["seq"] = plan_seq
            sess["cursor"] = 0   # 计划已重排: 从头重扫(已返回 key 在下方循环中跳过)
        chunk, idx = [], sess["cursor"]
        while idx < len(acts) and len(chunk) < limit:
            it = acts[idx]
            if it.get("key") not in sess["keys"]:
                chunk.append(it)
            idx += 1
        consumed_end = idx
        sess["cursor"] = consumed_end
        sess["keys"].update(it.get("key") for it in chunk)
    else:
        chunk = acts[offset:offset + limit]
        consumed_end = min(offset + limit, len(acts))
    # 提前补池: 剩余未看内容不多时后台就开始抓, 用户滚到底时新内容已经就位(不必干等)。
    # 阈值=60(约两页): 全量构建需10-30s, 半页缓冲常被快刷追上而卡顿, 两页缓冲足够构建完成。
    # 快/全动态决策: 缺得多(距上次全量>90s)直接跑全量, 否则轻量 quick(防止小水慢添)。
    if not q and not building and chunk and len(acts) - consumed_end < 60 \
            and time.time() - uni["built_at"] > 10:
        need_full = time.time() - _last_full_build_ts > 90
        threading.Thread(target=build_universe,
                         kwargs={"quick": not need_full}, daemon=True).start()
    if not chunk:
        # 到底: 未在构建且距上次构建>5s → 后台抓新内容追加到池(持续无限滚动, 不清空);
        # 同样按缺量决定 quick/全量(池子见底时缺得多, 优先全量)。
        if not building and time.time() - uni["built_at"] > 5:
            need_full = time.time() - _last_full_build_ts > 90
            threading.Thread(target=build_universe,
                             kwargs={"quick": not need_full}, daemon=True).start()
            building = True
        if not chunk:
            if building and not q:   # 正在构建(池尚未发布): 让前端继续轮询, 不误报"已到底"
                return {"ok": True, "items": [], "building": True, "gen": uni["gen"],
                        "source": uni["source"], "offline": uni["offline"],
                        "ai": uni["ai"], "mode": uni["mode"], "account": uni["account"],
                        "partitions": uni.get("partitions", "全部"),
                        "offset": offset}
            # 真到底(抓取后仍无新内容) → 已到底, 不再无限 building
            return {"items": [], "building": False, "gen": uni["gen"],
                    "source": uni["source"], "offline": uni["offline"],
                    "ai": uni["ai"], "mode": uni["mode"], "account": uni["account"],
                    "partitions": uni.get("partitions", "全部"),
                    "offset": offset}
    if q:   # --- 搜索路径: 原逻辑(严肃兜底 + AI门禁), 只对当前 chunk 生效 ---
        if chunk and sget("mode") == "严肃":
            chunk = [it for it in chunk if is_serious_ok(it)]
        if chunk and ai_on() and sget("ai_full_scan") == "1":
            cut = MODE_THRESH.get(sget("mode"), 6)
            imin = int(sget("ai_interest_min") or 0)
            dkws = dislike_keywords()
            scores = score_items(chunk, only_cache=True)   # 只读缓存, 秒回不阻塞; 未评分条目由后台评分线程渐进补齐
            kept_chunk, dropped = [], 0
            for it in chunk:
                if not it.get("_range_ok", True):   # 关闭模式: 超出热度前120范围→不展示
                    continue
                sc = scores.get(it["key"])
                if not sc:
                    continue   # 门禁: 必须筛查后才显示, 无分数不展示
                if sc.get("flame", 0) >= cut or sc.get("spam", 0) >= SPAM_CUT:
                    dropped += 1
                    continue
                if (sc.get("interest", 5) or 0) == 0:   # AI语义厌恶命中(评分器强制0分) → 一票剔除
                    dropped += 1
                    continue
                if imin and sc.get("interest", 5) < imin:
                    dropped += 1
                    continue
                if "emo" in sc and (sc.get("emo") or 0) <= -6:
                    dropped += 1
                    continue   # 情绪净值极负(纯负能量/贩卖焦虑)
                if sget("mode") == "严肃" and "density" in sc and (sc.get("density") or 0) < 4:
                    dropped += 1
                    continue   # 严肃模式: 信息密度门槛
                if "facts" in sc and not sc.get("facts") and _is_newsy(it) and (sc.get("density") or 0) < 6:
                    dropped += 1
                    continue   # 资讯无事实锚点+低密度 → 纯情绪输出
                if dkws and any(k in (it.get("title", "") + sc.get("topic", "") +
                                      it.get("desc", "")) for k in dkws):
                    dropped += 1   # 标题/主题字面命中厌恶词(未拆词, 防误伤普通游戏)
                    continue
                it["reason"] = sc.get("reason", "") or ""
                it["topic"] = sc.get("topic", "") or ""
                it["interest"] = sc.get("interest", 5)
                kept_chunk.append(it)
            if dropped:
                bump_stats(filtered=dropped)
                _funnel_bump("ai_gate", dropped)
            chunk = kept_chunk
        # 门禁后 chunk 可能被清空: 开启全扫但评分未就绪 → 让前端稍后重拉, 不误判"已到底"
        if not chunk and ai_on() and sget("ai_full_scan") == "1":
            return {"ok": True, "items": [], "building": True, "gen": uni["gen"],
                    "source": uni["source"], "offline": uni["offline"],
                    "ai": uni["ai"], "mode": uni["mode"], "account": uni["account"],
                    "partitions": uni.get("partitions", "全部"),
                    "offset": offset}
    else:   # --- 信息流路径: 门禁已在套餐规划内完成; 空规划=评分未就绪 → 稍后重拉 ---
        if not chunk and ai_on() and sget("ai_full_scan") == "1":
            return {"ok": True, "items": [], "building": True, "gen": uni["gen"],
                    "source": uni["source"], "offline": uni["offline"],
                    "ai": uni["ai"], "mode": uni["mode"], "account": uni["account"],
                    "partitions": uni.get("partitions", "全部"),
                    "offset": offset}
        chunk = [dict(it, reason=_slot_reason(it)) for it in chunk]   # 槽位理由前缀(可解释性)
    # 本页槽位统计(供前端"套餐"行显示)
    slots_info = {}
    for it in chunk:
        s = it.get("_slot")
        if s:
            slots_info[s] = slots_info.get(s, 0) + 1
    now_ts = int(time.time())
    if chunk and not q:
        record_feed_presentations(chunk, request_id or uuid.uuid4().hex, now_ts)
    with _db_lock:
        for it in chunk:
            db().execute("INSERT OR REPLACE INTO shown(key, ts) VALUES(?,?)", (it["key"], now_ts))
        db().execute("DELETE FROM shown WHERE ts < ?", (now_ts - 3 * 86400,))
        db().commit()
    if chunk:
        set_last_feed(chunk[-1])   # “刚才那条”指代目标
        try:   # 跨会话防重复: 记住本次会话末尾5条的一级分类(下次启动前5条避开)
            cats = [_cat_key(it) for it in chunk[-5:]]
            sset("last_types", json.dumps(cats, ensure_ascii=False))
        except Exception:
            pass
    next_offset = consumed_end
    return {"ok": True, "items": chunk, "building": building, "gen": uni["gen"],
            "source": uni["source"], "offline": uni["offline"],
            "ai": uni["ai"], "mode": uni["mode"], "account": uni["account"],
            "partitions": uni.get("partitions", "全部"),
            "slots": slots_info, "offset": next_offset, "next_offset": next_offset,
            "has_more": next_offset < len(acts) or building}


def current_partition_names():
    """当前启用分区名(用于界面显示, 杜绝"以为切了实际没切"的困惑)。"""
    enabled = enabled_source_ids()
    if not _subset_mode(enabled):
        return "全部"
    names = [_ID2NAME[i] for i in sorted(enabled) if i in _ID2NAME]
    return "仅" + "、".join(names) if names else "全部"


# ---------------------------------------------------------------- HTTP 服务

def read_json_body(handler):
    n = int(handler.headers.get("Content-Length", 0))
    return json.loads(handler.rfile.read(n).decode("utf-8") or "{}")


def _page_lease_shutdown_check(generation):
    global _PAGE_LEASE_GENERATION
    with _PAGE_LEASE_LOCK:
        if generation != _PAGE_LEASE_GENERATION or _PAGE_LEASES or _SHUTDOWN_REQUESTED.is_set():
            return
        _SHUTDOWN_REQUESTED.set()
        server = _SERVER
    if server:
        server.shutdown()


def page_lease_alive(token):
    with _PAGE_LEASE_LOCK:
        if _SHUTDOWN_REQUESTED.is_set():
            return False
        _PAGE_LEASES.add(token)
    return True


def page_lease_leaving(token):
    global _PAGE_LEASE_GENERATION
    with _PAGE_LEASE_LOCK:
        if _SHUTDOWN_REQUESTED.is_set():
            return False
        _PAGE_LEASES.discard(token)
        _PAGE_LEASE_GENERATION += 1
        generation = _PAGE_LEASE_GENERATION
    timer = threading.Timer(PAGE_LEASE_GRACE_SECONDS, _page_lease_shutdown_check, args=(generation,))
    timer.daemon = True
    timer.start()
    return True


def ui_version():
    """前端文件指纹(内容hash): 前端据此检测服务端已更新并自动刷新页面。"""
    import hashlib
    h = hashlib.md5()
    for name in ("index.html", "app.js", "style.css"):
        try:
            with open(web_path(name), "rb") as f:
                h.update(f.read())
        except Exception:
            pass
    return h.hexdigest()[:12]


def item_by_key(key):
    row = db().execute("SELECT json FROM items WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, name, ctype):
        try:
            with open(web_path(name), "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 无验证器时 no-cache = 每次都重新获取, 防止浏览器启发式缓存旧前端文件
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path in ("/", "/index.html"):
            return self.send_file("index.html", "text/html")
        if path == "/favicon.ico":
            return self.send_file("favicon.ico", "image/x-icon")
        if path == "/app.js":
            return self.send_file("app.js", "application/javascript")
        if path == "/qrcode.js":
            return self.send_file("qrcode.js", "application/javascript")
        if path == "/style.css":
            return self.send_file("style.css", "text/css")
        if path == "/api/search":
            q = qs.get("q", [""])[0].strip()
            offset = int(qs.get("offset", ["0"])[0] or 0)
            limit = min(int(qs.get("limit", ["30"])[0] or 30), 50)
            refresh = qs.get("refresh", ["0"])[0] == "1"
            return self.send_json(search_all(q, offset, limit, refresh=refresh))
        if path == "/api/feed":
            q = qs.get("q", [""])[0]
            refresh = qs.get("refresh", ["0"])[0] == "1"
            offset = int(qs.get("offset", ["0"])[0] or 0)
            limit = min(int(qs.get("limit", ["30"])[0] or 30), 50)
            request_id = qs.get("rid", [""])[0][:128]
            if refresh:
                global _uni_token
                with _uni_lock:   # 作废旧世代并清空, 交给 serve_chunk 异步重建(不阻塞HTTP)
                    _uni_token += 1
                    _universe["building"] = False
                    _universe["items"] = []
                    _fp_clear()   # 指纹阻止的是"进池资格", 清池后必须失效, 否则重建永远为空;
                                  # 刷新后的去重由 shown 窗口 + 随机页/源 + 新会话已返回集合承担
            return self.send_json(serve_chunk(offset, limit, q, request_id=request_id))
        if path == "/api/messages":
            bili_logged_in = bili_is_logged_in()
            bili_unread = bili_unread_messages() if bili_logged_in else None
            tb_state = tb_login_check()
            tb_logged_in = bool(tb_state.get("logged_in"))
            tb_unread = tb_unread_messages() if tb_logged_in else None
            xhh_logged_in = bool(sget("xhh_heybox_id"))
            xhh_unread = xhh_unread_messages() if xhh_logged_in else None
            return self.send_json({"bili": {"logged_in": bili_logged_in,
                                             "unread": bili_unread,
                                             "url": "https://message.bilibili.com/"},
                                   "tieba": {"logged_in": tb_logged_in,
                                             "unread": tb_unread,
                                             "url": "https://tieba.baidu.com/"},
                                   "xhh": {"logged_in": xhh_logged_in,
                                           "unread": xhh_unread,
                                           "url": "https://www.xiaoheihe.cn/message/home/comment"}})
        if path == "/api/ui_version":
            return self.send_json({"version": ui_version()})
        if path == "/api/status":
            last = sget("fetch_last")
            st = day_stats()
            tb_bars_n = len(tb_users())
            tb_login = tb_login_check()
            tb_last = int(sget("tb_last_fetch") or 0)
            sources = [{"name": "B站", "status": "正常" if last else "未抓取",
                        "last_fetch": int(last or 0), "fetched_today": st["received"],
                        "filtered_today": st["filtered"], "error": sget("fetch_error")}]
            tb_status = ("未配置吧列表" if not tb_bars_n
                         else "正常" if tb_last else "未抓取")
            sources.append({"name": "贴吧", "status": tb_status,
                            "last_fetch": tb_last,
                            "fetched_today": _tb_fetch_total, "filtered_today": 0,
                            "error": sget("tb_error"),
                            "bars": tb_bars_n,
                            "logged_in": tb_login.get("logged_in", False)})
            xhh_last = int(sget("xhh_last_fetch") or 0)
            sources.append({"name": "小黑盒", "status": "正常" if xhh_last else "未抓取",
                            "last_fetch": xhh_last,
                            "fetched_today": _xhh_fetch_total, "filtered_today": 0,
                            "error": sget("xhh_error") or "",
                            "logged_in": bool(sget("xhh_heybox_id"))})
            return self.send_json({"ai": ai_ok(), "ai_on": ai_on(), "sources": sources,
                                   "summary": st, "ai_scoring": dict(_score_progress),
                                   "funnel": {"labels": _FUNNEL_LABELS, "counts": funnel_stats()}})
        if path == "/api/blacklist":
            rows = db().execute("SELECT kind,value FROM blacklist").fetchall()
            return self.send_json({"authors": [r[1] for r in rows if r[0] == "author"],
                                   "keywords": [r[1] for r in rows if r[0] == "keyword"]})
        if path == "/api/history":
            rows = db().execute("SELECT key,ts FROM history ORDER BY ts DESC LIMIT 200").fetchall()
            items = [dict(item_by_key(k) or {"key": k, "title": "(已下架)", "platform": "-"},
                          **{"ts": t}) for k, t in rows]
            return self.send_json({"items": items})
        if path == "/api/saved":
            kind = qs.get("kind", ["later"])[0]
            rows = db().execute("SELECT key,ts FROM saved WHERE kind=? ORDER BY ts DESC LIMIT 200", (kind,)).fetchall()
            items = [dict(item_by_key(k) or {"key": k, "title": "(已下架)", "platform": "-"},
                          **{"ts": t}) for k, t in rows]
            return self.send_json({"items": items})
        if path == "/api/conv":
            convs, cur = conv_list()
            return self.send_json({"convs": convs, "cur": cur})
        if path == "/api/chat":
            cid = conv_cur(qs.get("cid", [None])[0])
            rows = db().execute("SELECT role,content FROM chat WHERE cid=? ORDER BY id DESC LIMIT 200",
                                (cid,)).fetchall()
            title = db().execute("SELECT title FROM conv WHERE id=?", (cid,)).fetchone()
            return self.send_json({"messages": [{"role": r[0], "content": r[1]} for r in reversed(rows)],
                                   "cid": cid, "title": title[0] if title else ""})
        if path == "/api/preview":
            bvid = (qs.get("bvid", [""])[0] or "").strip()
            src, err = preview_src(bvid)
            if src:
                return self.send_json({"ok": True, "src": src, "bvid": bvid})
            return self.send_json({"ok": False, "error": err or "无预览地址"})
        if path == "/api/video-proxy":
            bvid = (qs.get("bvid", [""])[0] or "").strip()
            cid = (qs.get("cid", [""])[0] or "").strip()
            c = _preview_cache.get(bvid)
            if not c or str(c.get("cid")) != cid or not c.get("url"):
                self.send_error(404, "预览地址不存在或已过期")
                return
            headers = dict(ACCOUNT_HEADERS)
            rng = self.headers.get("Range")
            if rng:
                headers["Range"] = rng
            try:
                req = urllib.request.Request(c["url"], headers=headers)
                up = urllib.request.urlopen(req, timeout=20)
            except Exception as e:
                self.send_error(502, "预览流获取失败: %s" % str(e)[:100])
                return
            self.send_response(up.status)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                v = up.headers.get(h)
                if v:
                    self.send_header(h, v)
            self.end_headers()
            try:
                while True:
                    buf = up.read(65536)
                    if not buf:
                        break
                    self.wfile.write(buf)
            except Exception:
                pass
            finally:
                up.close()
            return
        if path == "/api/avatar":
            face = account_slim().get("face", "")
            if not face:
                self.send_error(404)
                return
            if _avatar_cache["data"] is None or time.time() - _avatar_cache["ts"] > 300:
                try:
                    req = urllib.request.Request(face, headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": "https://www.bilibili.com/"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        _avatar_cache.update(ts=time.time(), data=resp.read(),
                                             ctype=resp.headers.get("Content-Type", "image/jpeg"))
                except Exception:
                    _avatar_cache.update(ts=time.time(), data=None, ctype="image/jpeg")
            if not _avatar_cache["data"]:
                self.send_error(404)
                return
            body = _avatar_cache["data"]
            self.send_response(200)
            self.send_header("Content-Type", _avatar_cache["ctype"])
            self.send_header("Cache-Control", "max-age=300")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        if path == "/api/machine":
            keys = ("m_min_views", "m_max_views", "m_max_duration", "m_min_duration", "m_repeat_limit", "m_repeat_cooldown_hours", "m_dedup", "m_blocklist_sync",
                    "m_max_age_months", "ser_max_age_months", "m_seen_hours", "m_emotion",
                    "m_news_fresh", "xhh_comm", "xhh_topic", "bili_follow_emph", "bili_follow_months",
                    "m_cold_start_n")
            enabled = enabled_source_ids()
            blocked = [{"name": r[0], "ts": r[1]} for r in
                       db().execute("SELECT name, ts FROM ablock ORDER BY ts DESC").fetchall()]
            return self.send_json({"settings": {k: sget(k) for k in keys},
                                   "source_groups": [{"id": g[0], "name": g[1], "on": g[0] in enabled}
                                                     for g in SOURCE_GROUPS],
                                   "blocked": blocked,
                                   "account_ok": account_slim().get("ok", False)})
        if path == "/api/account":
            return self.send_json(account_slim() | {"has_cookie": bool(account_cookie())})
        if path == "/api/tieba":
            login = tb_login_check()
            return self.send_json({
                "ok": True, "bars": tb_users(),
                "tb_weight": tb_weight(), "bili_weight": bili_weight(),
                "allow_help": sget("tb_allow_help") != "0",
                "platforms": [{"name": n, "key": k, "default": d,
                               "weight": platform_weights().get(n, d)}
                              for n, k, d in PLATFORMS],
                "logged_in": login.get("logged_in", False),
                "nickname": login.get("nickname", ""),
                "avatar": login.get("avatar", ""),
                "cookie_set": bool(tb_cookie()),
                "last_fetch": int(sget("tb_last_fetch") or 0),
                "error": sget("tb_error") or "",
                "fetch_total": _tb_fetch_total,
                "login_status": dict(_CDP_LOGIN),
            })
        if path == "/api/tieba/login/poll":
            return self.send_json(tb_login_poll())
        if path == "/api/tieba/browse":
            # 浏览器辅助采集: 打开(或复用)登录窗口并导航到指定吧列表页
            kw = (qs.get("kw", [""])[0] or "").strip()
            if not kw:
                return self.send_json({"ok": False, "error": "缺少吧名参数"})
            r = tb_login_start()
            if not r.get("ok"):
                return self.send_json(r)
            # 导航到吧列表页(浏览器无风控, 能加载)
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/list" % _CDP_PORT, timeout=3) as resp:
                targets = json.loads(resp.read().decode())
            page = next((t for t in targets if t.get("type") == "page"), None)
            if not page:
                return self.send_json({"ok": False, "error": "窗口未就绪"})
            # 注入已有登录cookie(若有), 让贴吧页面正常渲染帖子列表
            cookie = tb_cookie()
            if cookie:
                for part in cookie.split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        try:
                            _cdp_set_cookie(page["webSocketDebuggerUrl"], k, v,
                                            "https://tieba.baidu.com/")
                        except Exception:
                            pass
            _cdp_eval(page["webSocketDebuggerUrl"],
                      "location.href='https://tieba.baidu.com/f?kw=%s&ie=utf-8&pn=0'; true"
                      % urllib.parse.quote(kw))
            return self.send_json({"ok": True, "message": "已打开贴吧列表页, 稍后点击「抓取此页」"})
        if path == "/api/tieba/mylike":
            bars, err = tb_mylike()
            if bars:
                return self.send_json({"ok": True, "bars": bars})
            return self.send_json({"ok": False, "error": err or "获取失败"})
        if path == "/api/tieba/avatar":
            # 贴吧头像本地代理: 规避图床防盗链(与B站 /api/avatar 同模式)
            url = sget("tb_avatar") or ""
            if not url:
                self.send_error(404)
                return
            if _tb_avatar_cache["data"] is None or time.time() - _tb_avatar_cache["ts"] > 300:
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": "https://tieba.baidu.com/"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        _tb_avatar_cache.update(ts=time.time(), data=resp.read(),
                                                ctype=resp.headers.get("Content-Type", "image/jpeg"))
                except Exception:
                    _tb_avatar_cache.update(ts=time.time(), data=None, ctype="image/jpeg")
            if not _tb_avatar_cache["data"]:
                self.send_error(404)
                return
            body = _tb_avatar_cache["data"]
            self.send_response(200)
            self.send_header("Content-Type", _tb_avatar_cache["ctype"])
            self.send_header("Cache-Control", "max-age=300")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/xhh/qr":
            # 小黑盒扫码登录: 生成二维码(app=web=网页端通道)。用固定tokenid(登录态绑定发码方)。
            try:
                # 若无持久化tokenid则生成并保存(生成与轮询必须同一tokenid, 否则登录态对不上)
                if not sget("xhh_qr_tokenid"):
                    sset("xhh_qr_tokenid", xhh_token())
                qtok = sget("xhh_qr_tokenid")
                d, err = xhh_get("/account/get_qrcode_url/", {"app": "web"}, token=qtok)
                if err or not d or d.get("status") != "ok":
                    return self.send_json({"ok": False, "error": err or d or "生成失败"})
                url = (d.get("result") or {}).get("qr_url", "")
                import urllib.parse as _up
                code = _up.parse_qs(_up.urlparse(url).query).get("qr", [""])[0]
                sset("xhh_qr_code", code)
                return self.send_json({"ok": True, "url": url, "code": code,
                                       "expire": (d.get("result") or {}).get("expire", 120)})
            except Exception as ex:
                return self.send_json({"ok": False, "error": str(ex)[:100]})
        if path == "/api/xhh/qr/poll":
            code = sget("xhh_qr_code")
            if not code:
                return self.send_json({"status": "error", "message": "请先生成二维码"})
            # 复用生成时的固定tokenid(登录态绑定发码方, 轮询须一致)
            qtok = sget("xhh_qr_tokenid") or xhh_token()
            d, err = xhh_get("/account/qr_state/", {"qr": code, "app": "web"}, token=qtok)
            if err:
                return self.send_json({"status": "waiting", "message": "轮询: %s" % err[:60]})
            res = d.get("result") or {}
            err_st = res.get("error", "wait")
            err_msg = res.get("error_msg", "")
            # 状态细分(实测: wait=未扫码, ready=已扫码待确认, ok=确认成功, cancel=过期/取消)
            if err_st == "wait":
                return self.send_json({"status": "waiting", "message": "等待扫码…"})
            if err_st == "ready":
                return self.send_json({"status": "waiting", "message": "已扫码，请在手机上点「登录」确认"})
            if err_st == "ok":
                # 确认成功! 实测 result 直接带 nickname/avatar/heyboxid/account_detail.userid
                # 网页版登录不返回 pkey → 用 heybox_id(用户ID)作为"已登录"判据
                pkey, hid, nick, avatar = xhh_parse_login(res)
                sset("xhh_cookie", qtok)
                sset("xhh_pkey", "")
                sset("xhh_heybox_id", str(hid) if hid else "")
                sset("xhh_user", nick or "已登录")
                sset("xhh_avatar", avatar)
                return self.send_json({"status": "ok", "message": "小黑盒登录成功",
                                       "nickname": nick or "已登录", "avatar": avatar})
            if err_st in ("cancel", "expired") or "过期" in err_msg or "取消" in err_msg:
                return self.send_json({"status": "expired", "message": "二维码已过期，请点「生成登录二维码」重新扫码"})
            # 其余未知状态: 透传出真实进度(不吞状态)
            return self.send_json({"status": "waiting", "message": err_msg or err_st})
        if path == "/api/xhh":
            return self.send_json({"ok": True, "logged_in": bool(sget("xhh_heybox_id")),
                                   "nickname": sget("xhh_user") or "",
                                   "avatar": sget("xhh_avatar") or ""})
        if path == "/api/xhh/avatar":
            # 小黑盒头像本地代理: 规避防盗链
            url = sget("xhh_avatar") or ""
            if not url:
                self.send_error(404)
                return
            if _xhh_avatar_cache["data"] is None or time.time() - _xhh_avatar_cache["ts"] > 300:
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": "https://www.xiaoheihe.cn/"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        _xhh_avatar_cache.update(ts=time.time(), data=resp.read(),
                                                 ctype=resp.headers.get("Content-Type", "image/jpeg"))
                except Exception:
                    _xhh_avatar_cache.update(ts=time.time(), data=None, ctype="image/jpeg")
            if not _xhh_avatar_cache["data"]:
                self.send_error(404)
                return
            body = _xhh_avatar_cache["data"]
            self.send_response(200)
            self.send_header("Content-Type", _xhh_avatar_cache["ctype"])
            self.send_header("Cache-Control", "max-age=300")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/login/qr/poll":
            key = qs.get("key", [""])[0]
            if not key:
                return self.send_json({"status": "error", "message": "缺少参数"}, 400)
            try:
                return self.send_json(login_qr_poll(key))
            except Exception as e:
                return self.send_json({"status": "error", "message": "轮询失败: %s" % str(e)[:100]})
        if path == "/api/xhh/thread":
            lid = (qs.get("link_id", [""])[0] or "").strip()
            if not lid:
                return self.send_json({"ok": False, "error": "link_id无效"}, 400)
            return self.send_json(xhh_thread_content(lid))
        if path == "/api/tieba/thread":
            tid = (qs.get("tid", [""])[0] or "").strip()
            if not tid.isdigit():
                return self.send_json({"ok": False, "error": "tid无效"}, 400)
            return self.send_json(tieba_thread_content(tid))
        if path == "/api/settings":
            return self.send_json({k: sget(k) for k in ("ai_base", "ai_key", "ai_model", "mode",
                                                        "prefs_note", "dislike_note",
                                                        "price_in", "price_out", "ai_full_scan",
                                                        "ai_interest_min", "ai_batch", "ai_cache_hours",
                                                        "ai_enabled", "disclaimer_version")}
                                  | {"ai_ok": ai_ok(), "ai_on": ai_on()})
        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            body = read_json_body(self)
        except Exception:
            return self.send_json({"ok": False, "error": "请求体不是合法JSON"}, 400)

        if path == "/api/shutdown":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send_json({"ok": False, "error": "仅允许本机请求"}, 403)
            action = body.get("action")
            token = body.get("token")
            if action not in ("alive", "leaving") or not isinstance(token, str) or not (16 <= len(token) <= 128):
                return self.send_json({"ok": False, "error": "无效的页面租约请求"}, 400)
            ok = page_lease_alive(token) if action == "alive" else page_lease_leaving(token)
            return self.send_json({"ok": ok, "grace_seconds": PAGE_LEASE_GRACE_SECONDS})

        if path == "/api/login/qr":
            try:
                return self.send_json({"ok": True, **login_qr_generate()})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:150]})

        if path == "/api/account":
            sset("bili_cookie", (body.get("cookie") or "").strip())
            try:
                profile = fetch_account_profile()
                maybe_sync_blocklist()
                return self.send_json({"ok": True, "profile": profile})
            except Exception as e:
                if str(e).startswith("Cookie"):   # Cookie 无效时清掉, 避免残留
                    sset("bili_cookie", "")
                return self.send_json({"ok": False, "error": str(e)[:200]})
        if path == "/api/account/sync":
            try:
                profile = fetch_account_profile()
                maybe_sync_blocklist()
                return self.send_json({"ok": True, "profile": profile})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:200]})
        if path == "/api/account/logout":
            sset("bili_cookie", "")
            sset("acct_profile", "")
            sset("acct_nickname", "")
            return self.send_json({"ok": True})

        if path == "/api/xhh/logout":
            sset("xhh_cookie", "")
            sset("xhh_pkey", "")
            sset("xhh_heybox_id", "")
            sset("xhh_user", "")
            sset("xhh_avatar", "")
            return self.send_json({"ok": True})

        if path == "/api/tieba":
            if "bars" in body:
                bars = body.get("bars") or []
                if not isinstance(bars, list):
                    return self.send_json({"ok": False, "error": "bars 需为数组"}, 400)
                tb_set_bars([str(b).strip()[:30] for b in bars if str(b).strip()])
            if "allow_help" in body:
                sset("tb_allow_help", "1" if body["allow_help"] else "0")
            # 平台权重通用保存(PLATFORMS 注册的所有平台, 含小黑盒)
            for _, key, _d in PLATFORMS:
                if key in body:
                    try:
                        w = int(body[key])
                    except Exception:
                        continue
                    if w in {0, 25, 50, 75, 100}:
                        sset(key, str(w))
            force_rebuild()   # 吧列表/权重变更 → 立即重建
            return self.send_json({"ok": True})
        if path == "/api/tieba/grab":
            global _tb_last_fetch, _tb_fetch_total
            # 从窗口抓当前贴吧列表页内容(浏览器通道, 绕开服务端403封锁)
            if not _CDP_PROC or _CDP_PROC.poll() is not None:
                return self.send_json({"ok": False, "error": "窗口未打开(请先点「在窗口中打开贴吧页」)"})
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/list" % _CDP_PORT, timeout=3) as resp:
                targets = json.loads(resp.read().decode())
            page = next((t for t in targets if t.get("type") == "page"), None)
            if not page:
                return self.send_json({"ok": False, "error": "窗口未就绪"})
            items, fname, err = tb_browser_grab(page["webSocketDebuggerUrl"])
            if not items:
                return self.send_json({"ok": False, "error": err or "该页未解析到帖子"})
            now = int(time.time())
            with _db_lock:
                for it in items:
                    db().execute(
                        "INSERT INTO items(key,json,fetched_at,score_at) VALUES(?,?,?,0) "
                        "ON CONFLICT(key) DO UPDATE SET json=excluded.json,fetched_at=excluded.fetched_at",
                        (it["key"], json.dumps(it, ensure_ascii=False), now))
                db().commit()
            _tb_last_fetch = now
            _tb_fetch_total += len(items)
            sset("tb_last_fetch", now)
            sset("tb_error", "")
            return self.send_json({"ok": True, "items": len(items), "fname": fname,
                                   "message": "已从浏览器抓取 %s×%d" % (fname, len(items))})
        if path == "/api/tieba/login":
            return self.send_json(tb_login_start())
        if path == "/api/tieba/login/stop":
            return self.send_json(tb_login_stop())
        if path == "/api/tieba/login/manual":
            cookie = (body.get("cookie") or "").strip()
            if not cookie or "BDUSS=" not in cookie:
                return self.send_json({"ok": False, "error": "Cookie 不含 BDUSS（需完整贴吧登录凭据）"})
            sset("tb_cookie", cookie)
            check = tb_login_check()
            if check.get("logged_in"):
                # 顺路拉真实昵称/头像(手机版登录没有CDP DOM可抓, 走newmoindex接口)
                nick, av = tb_profile_from_cookie()
                sset("tb_user", nick or check.get("nickname") or "已登录")
                if av:
                    sset("tb_avatar", av)
                return self.send_json({"ok": True, "nickname": nick, "avatar": av})
            sset("tb_cookie", "")   # 无效凭证清掉
            return self.send_json({"ok": False, "error": "Cookie 无效或已过期（需含有效 BDUSS）"})
        if path == "/api/tieba/logout":
            sset("tb_cookie", "")
            sset("tb_user", "")
            sset("tb_avatar", "")
            return self.send_json({"ok": True})

        if path == "/api/machine":
            for k in ("m_min_views", "m_max_views", "m_max_duration", "m_min_duration", "m_repeat_limit", "m_repeat_cooldown_hours", "m_dedup", "m_blocklist_sync",
                      "m_max_age_months", "ser_max_age_months", "m_seen_hours", "m_emotion",
                      "m_news_fresh", "xhh_comm", "xhh_topic", "bili_follow_emph", "bili_follow_months",
                    "m_cold_start_n"):
                if k in body:
                    sset(k, body[k])
            if "m_cold_start_n" in body:
                persist_cold_cache(force=True)   # 上限改动立即重存当前池快照
            if "m_sources" in body:   # 允许的来源/分区 id 列表
                sset("m_sources", json.dumps(body["m_sources"]))
                n = purge_items_by_parts(enabled_source_ids())   # 切分区后清旧池
                force_rebuild()   # 作废旧构建, 立即按新分区重建(不等待)
                return self.send_json({"ok": True, "purged": n})
            return self.send_json({"ok": True})
        if path == "/api/machine/blocklist-sync":
            if not account_cookie():
                return self.send_json({"ok": False, "error": "请先登录B站账号（个人 → B站账号）"})
            try:
                n = fetch_account_blocklist()
                return self.send_json({"ok": True, "count": n})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:150]})
        if path == "/api/machine/blocklist-remove":
            db().execute("DELETE FROM ablock WHERE name=?", (body.get("name", ""),))
            db().commit()
            return self.send_json({"ok": True})

        if path == "/api/feedback":
            key = body.get("key", "")
            action = body.get("action", "")
            changed = False
            if action in ("undolike", "undodislike"):
                cur = db().execute("DELETE FROM feedback WHERE key=? AND action=?",
                                   (key, action[4:]))
                changed = cur.rowcount > 0
            elif key and action:
                it = item_by_key(key) or {}
                # 去重: 同一条目重复点踩只记一次(重复行只会污染画像统计)
                exists = db().execute("SELECT 1 FROM feedback WHERE key=? AND action=?",
                                      (key, action)).fetchone()
                if not exists:
                    db().execute("INSERT INTO feedback(key,author,action,ts) VALUES(?,?,?,?)",
                                 (key, it.get("author", ""), action, int(time.time())))
                    changed = True
            db().commit()
            if changed and action in ("dislike", "undodislike"):
                # 负反馈生效信号: 计划缓存键含 feedback_sig → 下次规划强制重排,
                # 已在计划中的不喜欢条目立即被 planned_feed 的负反馈过滤剔除。
                sset("feedback_sig", str(int(time.time())))
            return self.send_json({"ok": True})
        if path == "/api/feed/refresh":
            key = (body.get("key") or "").strip()
            if not key:
                return self.send_json({"ok": False, "error": "缺少参数"}, 400)
            it = feed_refresh_item(key, q=(body.get("q") or "").strip())   # 搜索模式限定同主题
            if not it:
                return self.send_json({"ok": False, "error": "暂无可用内容替换"})
            if not (body.get("q") or "").strip():
                record_feed_presentations([it], uuid.uuid4().hex)
            return self.send_json({"ok": True, "item": it})

        if path == "/api/blacklist":
            kind, value, remove = body.get("kind"), (body.get("value") or "").strip(), body.get("remove")
            if not kind or not value:
                return self.send_json({"ok": False, "error": "参数不完整"}, 400)
            if remove:
                db().execute("DELETE FROM blacklist WHERE kind=? AND value=?", (kind, value))
            else:
                db().execute("INSERT OR IGNORE INTO blacklist(kind,value) VALUES(?,?)", (kind, value))
            db().commit()
            # 黑名单/屏蔽变化 → 计划重排信号(apply_filters/negative_feedback_filter 即时生效)
            sset("feedback_sig", str(int(time.time())))
            return self.send_json({"ok": True})

        if path == "/api/saved":
            key, kind, remove = body.get("key", ""), body.get("kind", "later"), body.get("remove")
            if remove:
                db().execute("DELETE FROM saved WHERE key=? AND kind=?", (key, kind))
            else:
                db().execute("INSERT OR REPLACE INTO saved(key,kind,ts) VALUES(?,?,?)",
                             (key, kind, int(time.time())))
            db().commit()
            return self.send_json({"ok": True})

        if path == "/api/click":
            db().execute("INSERT OR REPLACE INTO history(key,ts) VALUES(?,?)",
                         (body.get("key", ""), int(time.time())))
            db().commit()
            set_last_feed(item_by_key(body.get("key", "")))   # 点开即“刚才那条”
            return self.send_json({"ok": True})

        if path == "/api/disclaimer/ack":
            version = body.get("version")
            if not isinstance(version, str) or version != DISCLAIMER_VERSION:
                return self.send_json({"ok": False, "error": "免责声明版本无效或已过期"}, 400)
            sset("disclaimer_version", version)
            return self.send_json({"ok": True, "version": version})

        if path == "/api/settings":
            old_prefs = sget("prefs_note") or ""
            old_dislike = sget("dislike_note") or ""
            for k in ("ai_base", "ai_key", "ai_model", "mode", "prefs_note", "dislike_note",
                      "price_in", "price_out", "ai_full_scan", "ai_interest_min",
                      "ai_batch", "ai_cache_hours", "ai_enabled"):
                if k in body and body[k] is not None:
                    if k in ("ai_base", "ai_model") and not str(body[k]).strip():
                        continue   # 地址/模型不允许空值覆盖(保留默认/原值)
                    if k == "mode" and str(body[k]) not in MODE_THRESH:
                        continue   # 档位白名单: 防止乱码/非法值污染
                    sset(k, body[k])
            if any(k in body for k in ("ai_full_scan", "ai_interest_min", "ai_batch",
                                       "ai_cache_hours", "ai_enabled")):
                force_rebuild()   # 评分范围/标准/粒度/总开关变了, 立即重建
            # 画像类规则(厌恶/偏好备注)变化 → 旧语义分数过时, 清评分缓存强制重评
            if (("prefs_note" in body and body.get("prefs_note") is not None and
                 (body.get("prefs_note") or "") != old_prefs) or
                ("dislike_note" in body and body.get("dislike_note") is not None and
                 (body.get("dislike_note") or "") != old_dislike)):
                clear_score_cache()
                force_rebuild()
            if "mode" in body and body.get("mode") != sget("mode"):
                with _db_lock:   # 档位(含严肃模式)切换: 清展示池并立即重建, 不让旧内容回流
                    db().execute("DELETE FROM shown")
                    db().commit()
                force_rebuild()
            return self.send_json({"ok": True, "ai_ok": ai_ok(), "ai_on": ai_on()})

        if path == "/api/settings/export":
            payload = settings_export_payload(bool(body.get("credentials")))
            return self.send_json({"ok": True, "payload": payload})

        if path == "/api/settings/import":
            incoming = body.get("settings")
            if not isinstance(incoming, dict):
                return self.send_json({"ok": False, "error": "缺少 settings 对象"}, 400)
            imported, skipped = [], []
            for k, v in incoming.items():
                if k not in _IO_IMPORTABLE:
                    skipped.append(k); continue
                if v is None:
                    continue
                if k in ("ai_base", "ai_model") and not str(v).strip():
                    continue   # 与手动保存一致: 不允许空值覆盖
                if k == "mode" and str(v) not in MODE_THRESH:
                    skipped.append(k); continue   # 档位白名单
                sset(k, v)
                imported.append(k)
            bl = body.get("blacklist") or {}
            add_a = add_k = 0
            if isinstance(bl, dict):
                with _db_lock:
                    for kind, vals in (("author", bl.get("authors") or []),
                                       ("keyword", bl.get("keywords") or [])):
                        if not isinstance(vals, list):
                            continue
                        have = {r[0] for r in db().execute(
                            "SELECT value FROM blacklist WHERE kind=?", (kind,))}
                        for val in vals:
                            val = str(val).strip()
                            if val and val not in have:
                                db().execute("INSERT INTO blacklist(kind,value) VALUES(?,?)",
                                             (kind, val))
                                have.add(val)
                                if kind == "author": add_a += 1
                                else: add_k += 1
                    db().commit()
            # 语义相关的改动 → 清缓存重建, 与手动保存行为一致
            if any(k in imported for k in ("prefs_note", "dislike_note")):
                clear_score_cache()
            if "mode" in imported:
                with _db_lock:
                    db().execute("DELETE FROM shown")
                    db().commit()
            force_rebuild()
            return self.send_json({"ok": True, "imported": len(imported),
                                   "skipped": skipped[:12],
                                   "bl_authors": add_a, "bl_keywords": add_k})

        if path == "/api/conv":
            cid = conv_create((body.get("title") or "").strip() or None, set_cur=True)
            return self.send_json({"ok": True, "id": cid})
        if path == "/api/conv/select":
            cid = conv_set_cur(body.get("id"))
            return self.send_json({"ok": True, "cid": cid})
        if path == "/api/conv/rename":
            ok = conv_rename(body.get("id"), body.get("title", ""))
            return self.send_json({"ok": ok})
        if path == "/api/conv/delete":
            conv_delete(body.get("id"))
            return self.send_json({"ok": True})

        if path == "/api/chat":
            msg = (body.get("message") or "").strip()
            if not msg:
                return self.send_json({"ok": False, "error": "空消息"}, 400)
            stream = bool(body.get("stream")) and ai_on()
            chips = build_chips()
            # 命令引擎优先(纯规则, 不烧token): 指代/分区/统计/新对话等
            cmd = handle_command(msg)
            cid = conv_cur(body.get("cid") or (cmd.get("cid") if cmd else None))
            conv_touch(cid, msg)   # 首句自动命名
            db().execute("INSERT INTO chat(role,content,ts,cid) VALUES('user',?,?,?)",
                         (msg, int(time.time()), cid))
            db().commit()

            def sse_send(event, data):
                self.wfile.write(("event: %s\ndata: %s\n\n" % (
                    event, json.dumps(data, ensure_ascii=False))).encode("utf-8"))
                self.wfile.flush()

            def finish(reply, ok=True, err="", trigger=None):
                db().execute("INSERT INTO chat(role,content,ts,cid) VALUES('assistant',?,?,?)",
                             (reply, int(time.time()), cid))
                db().commit()
                return self.send_json({"ok": ok, "reply": reply, "intent": intent,
                                       "chips": chips, "cid": cid, "error": err,
                                       "trigger": trigger})

            def sse_reply(reply, intent, trigger=None):
                """一次性回复的 SSE 事件流(无增量): meta → done。"""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                sse_send("meta", {"intent": intent, "chips": chips, "cid": cid})
                sse_send("done", {"reply": reply, "chips": chips, "ok": True,
                                  "trigger": trigger})
                db().execute("INSERT INTO chat(role,content,ts,cid) VALUES('assistant',?,?,?)",
                             (reply, int(time.time()), cid))
                db().commit()

            if cmd:
                intent = {"action": "reply", "keywords": []}
                reply = cmd.get("reply", "")
                trig = cmd.get("trigger")
                if stream:
                    sse_reply(reply, intent, trig)
                    return
                return finish(reply, trigger=trig)

            intent = extract_intent(msg)
            # 意图搜索: 固定短回复(不调用LLM, 省token又快), 前端切搜索流
            if intent["action"] == "add_dislike":
                # 真正执行: 把关键词(含AI补的相关词)追加到厌恶备注
                kws = intent.get("keywords") or []
                cur = sget("dislike_note")
                new, added = cur, []
                for k in kws:
                    k = k.strip()
                    if not k:
                        continue
                    if k not in new:
                        new = (new + "，" + k).strip("，") if new else k
                        added.append(k)
                sset("dislike_note", new)
                clear_score_cache()   # 厌恶画像变化 → 旧语义分数过时, 强制重评
                reply = "已把「%s」写进厌恶备注，之后会避开这些内容。" % (
                    "、".join(added) if added else "、".join(kws))
                intent = {"action": "reply", "keywords": []}
                if stream:
                    sse_reply(reply, intent, "settings")
                    return
                return finish(reply, trigger="settings")

            if intent["action"] == "set_mode":
                md = intent.get("mode")
                sset("mode", md)
                with _db_lock:   # 档位切换: 清展示池并立即重建
                    db().execute("DELETE FROM shown")
                    db().commit()
                force_rebuild()
                reply = "已切换到【%s】档，信息流已按新档位重建。" % md
                intent = {"action": "reply", "keywords": []}
                if stream:
                    sse_reply(reply, intent, "settings")
                    return
                return finish(reply, trigger="settings")

            if intent["action"] == "search":
                reply = ("收到！已按你的要求刷「%s」相关视频，左侧正在搜索，下滑看更多～" %
                         "、".join(intent["keywords"]))
                if stream:
                    sse_reply(reply, intent)
                    return
                return finish(reply)

            if not ai_on():
                reply = ("AI 功能已关闭或未配置。请到左侧「设置」检查 AI 功能总开关（开启后）"
                         "并填入 OpenAI 兼容的 API Key（默认 DeepSeek）。开启后我能听懂你说的话"
                         "并直接刷视频；现在你可以直接说「给我刷某某视频」，我会按句式识别并搜索。")
                if stream:
                    sse_reply(reply, intent)
                    return
                return finish(reply)

            # --- LLM 自由聊天(或流式) ---
            rows = db().execute("SELECT role,content FROM chat WHERE cid=? ORDER BY id DESC LIMIT 16",
                                (cid,)).fetchall()
            history = [{"role": r, "content": c} for r, c in reversed(rows) if (c or "").strip()]  # 过滤空消息, 防API拒绝
            messages = ([{"role": "system", "content":
                          "你是「洞鉴虚明」的偏好助手。帮用户定义想看什么内容，"
                          "回复要简短友好。注意：你只能口头回答，无法真正推送/搜索/切换视频；"
                          "用户想刷视频时会直接说「给我刷/推/看XX」，系统会自动执行并另行确认，"
                          "你不要声称「已推送」或「已切换到左侧」之类的话。"
                          "可参考的用户画像:\n" + (user_context() or "(空)")}]
                        + history)
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                sse_send("meta", {"intent": intent, "chips": chips, "cid": cid})
                reply = ""
                thinking_txt = []
                aborted = False

                def on_reasoning(t):
                    try:
                        if not thinking_txt:   # 推理模型: 思考阶段前端显示"思考中…"
                            sse_send("think", {})
                        thinking_txt.append(t)
                    except Exception:
                        pass
                try:
                    for delta in llm_stream(messages, max_tokens=4096, on_reasoning=on_reasoning):
                        reply += delta
                        try:
                            sse_send("delta", {"t": delta})
                        except Exception:
                            aborted = True
                            break
                except Exception as e:
                    if not reply:
                        reply = "AI 调用失败（%s）。请检查左侧设置里的 API 地址 / Key / 模型。" % str(e)[:120]
                if aborted and not reply:
                    reply = "（已停止生成）"
                if not reply and not aborted:
                    # 思考过长被截断时, 用思考尾段兜底(结论通常在后半段)
                    tail = "".join(thinking_txt)[-160:].strip()
                    reply = ("模型思考过长了：%s（以上为模型思考过程，供参考）" % tail) if tail else \
                            "模型思考后没有给出文字回答，再问一次或换个说法试试～"
                if not aborted and reply and "AI 调用失败" not in reply:
                    # v4-flash 流式不返回 usage → 按字数估算记账(意图判断已是真实值)
                    record_usage({"prompt_tokens": int(sum(
                                     len((m.get("content") or "")) for m in messages) * 0.8) + 8,
                                  "completion_tokens": int(len(reply) * 0.8) + 8})
                db().execute("INSERT INTO chat(role,content,ts,cid) VALUES('assistant',?,?,?)",
                             (reply, int(time.time()), cid))
                db().commit()
                try:
                    sse_send("done", {"reply": reply, "chips": chips, "ok": True})
                except Exception:
                    pass
                return
            try:
                content, _ = call_llm(messages, max_tokens=4096)
                reply = content.strip()
            except Exception as e:
                reply = "AI 调用失败（%s）。请检查左侧设置里的 API 地址 / Key / 模型。" % str(e)[:120]
            if not reply:
                reply = "模型思考后没有给出文字回答，再问一次或换个说法试试～"
            return finish(reply)

        if path == "/api/chat/clear":
            cid = conv_cur(body.get("cid"))
            db().execute("DELETE FROM chat WHERE cid=?", (cid,))
            db().commit()
            return self.send_json({"ok": True, "cid": cid})

        if path == "/api/reset":
            db().execute("DELETE FROM feedback; DELETE FROM history; DELETE FROM saved; "
                         "DELETE FROM chat; DELETE FROM conv")
            db().commit()
            conv_create(set_cur=True)
            return self.send_json({"ok": True})

        if path == "/api/pool/clear":
            # 清空信息池(沉淀库+评分缓存)与已看记录, 并作废当前世代替换令牌
            # (不在此同步重建, 由前端 loadFeed(true)→/api/feed?refresh=1 触发重新收录)
            global _uni_token
            with _db_lock:
                db().execute("DELETE FROM items")
                db().execute("DELETE FROM shown")
                db().commit()
            with _uni_lock:
                _uni_token += 1
                _universe["building"] = False
                _universe["items"] = []
                _universe["gen"] += 1
            _fp_clear()   # 指纹记忆随池清空(重建拉回同内容不被误拦)
            return self.send_json({"ok": True})

        self.send_error(404)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args))


# ---------------------------------------------------------------- 内置示例数据(断网降级用)

_NOW = int(time.time())
MOCK_ITEMS = [
    {"key": "https://www.bilibili.com/video/BV1mock001", "platform": "B站",
     "title": "《黑神话：悟空》DLC 实机演示曝光：你最期待哪个BOSS？", "author": "游戏观察室",
     "desc": "DLC 实机演示公开，各BOSS战设计细节一次看全。", "cover": "",
     "views": 1284500, "likes": 45210, "duration": 872, "pubdate": _NOW - 3600 * 2,
     "url": "https://www.bilibili.com/video/BV1mock001"},
    {"key": "https://www.bilibili.com/video/BV1mock002", "platform": "B站",
     "title": "为什么说2025年别再盲目买新能源车了？三个关键数据告诉你", "author": "老王的选车笔记",
     "desc": "三个数据看懂今年选车逻辑。", "cover": "",
     "views": 865300, "likes": 21890, "duration": 615, "pubdate": _NOW - 3600 * 5,
     "url": "https://www.bilibili.com/video/BV1mock002"},
    {"key": "https://www.bilibili.com/video/BV1mock003", "platform": "B站",
     "title": "程序员自研的效率工具开源了！一键清理重复文件（附教程）", "author": "码农阿伟",
     "desc": "开源工具演示与安装教程。", "cover": "",
     "views": 302400, "likes": 15230, "duration": 1043, "pubdate": _NOW - 3600 * 8,
     "url": "https://www.bilibili.com/video/BV1mock003"},
    {"key": "https://www.bilibili.com/video/BV1mock004", "platform": "B站",
     "title": "实测10款千元旗舰耳机，性价比之王出乎意料", "author": "数码小雷达",
     "desc": "千元价位横评。", "cover": "",
     "views": 512200, "likes": 19800, "duration": 1265, "pubdate": _NOW - 3600 * 11,
     "url": "https://www.bilibili.com/video/BV1mock004"},
    {"key": "https://www.bilibili.com/video/BV1mock005", "platform": "B站",
     "title": "一个人也能做的家常菜：番茄牛腩的零失败做法", "author": "深夜食堂说",
     "desc": "零失败家常菜教程。", "cover": "",
     "views": 218700, "likes": 9900, "duration": 532, "pubdate": _NOW - 3600 * 14,
     "url": "https://www.bilibili.com/video/BV1mock005"},
    {"key": "https://www.bilibili.com/video/BV1mock006", "platform": "B站",
     "title": "考研倒计时100天：这份复习计划表建议收藏", "author": "上岸学长",
     "desc": "100天复习规划。", "cover": "",
     "views": 154800, "likes": 7200, "duration": 458, "pubdate": _NOW - 3600 * 20,
     "url": "https://www.bilibili.com/video/BV1mock006"},
]


def port_busy(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/feed" % port, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    # 评分器升级(新增 信息密度/情绪净值/事实锚点 三维度): 只做版本标记, 不整体清评分缓存
    # (全量清缓存会让全扫模式下首屏长时间空白; 新维度随条目自然重评在缓存期内补全)
    if sget("scorer_v") != "2":
        sset("scorer_v", "2")
    _ensure_score_bg()   # 启动后台渐进评分守护线程(评分写库, 交互路径只读缓存不阻塞)
    AUTO_OPEN = ("--auto-open" in sys.argv) or bool(getattr(sys, "frozen", False))
    for port in PORTS:
        if port_busy(port):
            print("服务已在 http://127.0.0.1:%d 运行" % port)
            if AUTO_OPEN:
                webbrowser.open("http://127.0.0.1:%d" % port)
            sys.exit(0)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        print("端口 8000/8010/8020 均被占用, 无法启动")
        sys.exit(1)
    _SERVER = server
    print("洞鉴虚明 v0.2.6.2a 已启动: http://127.0.0.1:%d" % port)
    print("按 Ctrl+C 停止服务")
    if AUTO_OPEN:
        webbrowser.open("http://127.0.0.1:%d" % port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        for executor in (_FX, _SERIOUS_X, _SCORE_X, _TAG_X):
            executor.shutdown(wait=False, cancel_futures=True)
        tb_login_stop()
        _SERVER = None
