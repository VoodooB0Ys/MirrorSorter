# -*- coding: utf-8 -*-
"""构建 fastText 训练语料（多标签, 大规模）。

标签体系（6 类）：
    ok         正常 —— 兜底类, 必须最丰富, 决定误杀率
    flame      引战 / 辱骂 / 攻击性
    spam       广告 / 引流 / 低质刷屏
    clickbait  标题党 / 夸张信息
    yy         阴阳怪气
    neg        负面情绪

分词约定（★ 必须与运行期 app.py 的 _ft_tok 完全一致）：
    整句**不切字**，只折叠空白并截断。中文靠 fastText 的字符级 n-gram(minn/maxn)
    自动切出 2~4 元组，这比手工分词更稳，也是上一轮原型验证过的做法。

来源：
    真实  COLDataset                清华 CoAI 中文冒犯语言 (EMNLP 2022) 37,483 条
    真实  产品库 items              本产品实抓的 B站/贴吧/小黑盒 标题+简介 24,751 条
    真实  ChnSentiCorp              酒店评论 7,766 条
    真实  seed_yy                   上一轮手工标注的阴阳怪气 238 条
    ★合成 用**真实句子做底料**注入标记词（不是纯模板, 保证字面多样性）
"""
import os
import re
import sys
import csv
import glob
import json
import random
import sqlite3
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_data")
OUT = os.path.join(HERE, "ft_train.txt")
DB = r"C:\Users\sunsi\AppData\LocalLow\MirrorSorter\data.db"

random.seed(20261010)
MAXLEN = 64          # 与运行期保持一致
MINLEN = 2

WS = re.compile(r"\s+")


def tok(t):
    """★ 与 app.py _ft_tok 必须逐字节一致。"""
    t = WS.sub(" ", str(t or "")).strip()
    t = t.replace("\u200b", "").replace("\ufeff", "")
    return t[:MAXLEN]


def emit(bag, labels, text):
    t = tok(text)
    if len(t) < MINLEN:
        return
    bag.append(" ".join("__label__" + l for l in labels) + " " + t)


# ================================================================ 真实数据
def load_cold():
    """COLDataset: label 1=冒犯/仇恨, 0=正常。顺带回收正常句做合成底料。

    ★ 关键取舍：COLDataset 的"冒犯"大量是**种族/地域/性别议题**，而本产品要抓的是
      B站游戏/动漫区里的**直接引战辱骂**。两者话题分布差很远，如果原样全量喂进去，
      模型会学成"只要提到黑人/女权/地域就判引战"，误杀所有相关讨论。
      所以这里对 label=1 做**上限截断**，把话题偏置稀释掉；label=0 全部保留
      （它们正是"在讨论敏感话题但不是引战"的硬负例）。
    ★ test.csv 留作真实留出集, 绝不进训练 —— 否则评测就是自欺欺人。
    """
    out, base, flame_pool = [], [], []
    flame_cap = 11000
    for fn in ("train.csv", "dev.csv"):
        p = os.path.join(DATA, "COLDataset", "COLDataset", fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                lab = str(row.get("label") or "").strip()
                txt = row.get("TEXT") or ""
                if lab == "1":
                    flame_pool.append(txt)
                elif lab == "0":
                    emit(out, ["ok"], txt)
                    base.append(txt)
    random.shuffle(flame_pool)
    for txt in flame_pool[:flame_cap]:
        emit(out, ["flame"], txt)
    return out, base, "真实 COLDataset(flame 截断至 %d)" % flame_cap


def load_items():
    """产品库实抓内容。这些条目都已通过机械筛选 → 作 ok 正样本。
    同时回收标题做 clickbait / yy 的底料。"""
    out, base = [], []
    if not os.path.exists(DB):
        return out, base, "真实 items(库不存在)"
    try:
        c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        rows = c.execute("SELECT json FROM items").fetchall()
        c.close()
    except Exception as e:
        return out, base, "真实 items(ERR %s)" % e
    for (raw,) in rows:
        try:
            d = json.loads(raw)
        except Exception:
            continue
        title = (d.get("title") or "").strip()
        desc = (d.get("desc") or "").strip()
        if not title:
            continue
        emit(out, ["ok"], title)
        if desc:
            emit(out, ["ok"], title + " " + desc[:60])
        base.append(title)
    return out, base, "真实 items(产品库)"


def load_chat():
    out = []
    if not os.path.exists(DB):
        return out, "真实 chat(无)"
    try:
        c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        rows = c.execute("SELECT content FROM chat").fetchall()
        c.close()
    except Exception as e:
        return out, "真实 chat(ERR %s)" % e
    for (t,) in rows:
        emit(out, ["flame"], t)
    return out, "真实 chat"


def load_royal():
    out = []
    base = os.path.join(DATA, "Chinese-offensive-language-detect", "Dataset")
    if not os.path.isdir(base):
        return out, "真实 royal12646(无)"
    for p in glob.glob(os.path.join(base, "*", "*.csv")):
        try:
            with open(p, encoding="utf-8-sig", errors="replace", newline="") as f:
                rows = list(csv.reader(f))
        except Exception:
            continue
        if not rows:
            continue
        head = [h.strip().lower() for h in rows[0]]
        ci_lab = next((i for i, h in enumerate(head)
                       if h in ("label", "标签", "toxic", "offensive", "harm")), None)
        ci_txt = next((i for i, h in enumerate(head)
                       if h in ("text", "content", "sentence", "文本", "review", "comment")), None)
        if ci_lab is None or ci_txt is None:
            continue
        for r in rows[1:]:
            if len(r) <= max(ci_lab, ci_txt):
                continue
            lab = r[ci_lab].strip()
            if lab in ("1", "1.0", "toxic", "harmful", "offensive", "true", "True"):
                emit(out, ["flame"], r[ci_txt])
            elif lab in ("0", "0.0", "normal", "non-toxic", "false", "False"):
                emit(out, ["ok"], r[ci_txt])
    return out, "真实 royal12646"


def load_senti():
    """情感类语料 → 正=ok, 负=neg。作用是撑开"正常中文"的流形, 压误杀。"""
    out, names = [], []
    for p, n, lb_col, tx_col in [
        (os.path.join(DATA, "ChineseNlpCorpus", "datasets", "ChnSentiCorp_htl_all",
                      "ChnSentiCorp_htl_all.csv"), "ChnSentiCorp", "label", "review"),
        (os.path.join(DATA, "ChineseNlpCorpus", "datasets", "waimai_10k",
                      "waimai_10k.csv"), "waimai_10k", "label", "review"),
    ]:
        if not os.path.exists(p):
            names.append("%s(无)" % n)
            continue
        n0 = len(out)
        try:
            with open(p, encoding="utf-8-sig", errors="replace", newline="") as f:
                for row in csv.DictReader(f):
                    lab = str(row.get(lb_col) or "").strip()
                    txt = row.get(tx_col) or ""
                    if lab in ("1", "1.0", "正面", "pos", "positive"):
                        emit(out, ["ok"], txt)
                    elif lab in ("0", "0.0", "负面", "neg", "negative"):
                        emit(out, ["neg"], txt)
        except Exception as e:
            names.append("%s(ERR %s)" % (n, e))
            continue
        names.append("%s=%d" % (n, len(out) - n0))
    return out, "真实 情感[" + ",".join(names) + "]"


def load_seed_yy():
    out = []
    p = os.path.join(HERE, "train.txt")
    if not os.path.exists(p):
        return out, "真实 seed_yy(无)"
    with open(p, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^__label__(\S+)\s+(.*)$", line.rstrip("\n"))
            if not m:
                continue
            if m.group(1) == "yy":
                emit(out, ["yy"], m.group(2))
            elif m.group(1) == "ok":
                emit(out, ["ok"], m.group(2))
    return out, "真实 seed_yy(手工)"


# ================================================================ 词表
# 全部来自真实社区用法观测（上一轮 + 本产品实际抓到的高频表达）
YY_MARK = ["赢麻了", "典", "典中典", "急了", "笑死", "笑死我了", "乐", "绷不住了", "孝",
           "太孝了", "啊对对对", "你开心就好", "呵呵", "感恩", "早该管管了", "懂的都懂",
           "格局小了", "就这", "麻了", "笑嘻了", "遥遥领先", "建议申遗", "建议直接封神",
           "建议直接颁个奖", "常规操作", "意料之中", "毫不意外", "果然", "还是那味",
           "味太冲了", "好意思吗", "良心不会痛吗", "你品你细品", "恭喜恭喜", "慢慢玩",
           "随你", "行吧", "好的呢", "亲", "我谢谢你", "不敢评价", "我不好说",
           "太强了", "属实是没想到", "这也叫优化", "这也配叫大作", "脸都不要了",
           "蚌埠住了", "孝出强大", "反向优化", "建议多学学", "大厂风范", "重新定义",
           "感人肺腑", "令人敬佩", "不容易啊", "辛苦了呢", "真是谢谢了", "绝了",
           "我笑了", "谢谢你嗷", "好家伙", "真有你的", "6", "666", "啊这"]

YY_TOPIC = ["魂游", "这游戏", "这优化", "这更新", "这剧情", "这策划", "这服务器", "这活动",
            "这价格", "这画质", "这平衡", "这匹配", "这客服", "这版本", "这dlc", "这联动",
            "这皮肤", "这抽卡", "这官方", "这宣传", "这建模", "这运营", "这福利", "这手感",
            "这数值", "这波操作", "这文案", "这配音", "这演出", "这态度"]

YY_TAIL = ["，麻了", "，我谢谢你了", "，习惯了", "，见怪不怪了", "，还能说啥", "，无话可说",
           "，你开心就好", "，继续加油", "，祝你好运", "，不评价了", "，直接封神", "，赢",
           "，我悟了", "，属实优秀", "，值得表扬", "，再接再厉", "，不愧是你", "，长见识了"]

YY_PAT = ["又是%s", "不愧是%s", "只能说%s", "又是熟悉的%s", "%s就这", "%s还是那味",
          "又是%s，典", "%s，典中典", "还得是%s", "论%s还得看你"]

# 正面词 + 负面事实 = 语用反讽（C 型, 上一轮的盲区）
C_POS = ["真好", "真棒", "真良心", "真稳", "真热情", "真用心", "真负责", "真专业", "真快",
         "真便宜", "真流畅", "真清晰", "真平衡", "真大方", "真效率", "真贴心", "真准时"]
C_BAD = ["我4080都掉帧", "我掉线三次", "三天不回消息", "我电脑冒烟了", "我钱包空了",
         "进游戏就闪退", "连不上服务器", "更新完就崩", "一开就黑屏", "卡成ppt",
         "排队两小时", "充了钱也没用", "客服直接拉黑我", "bug比内容多", "开局就劝退",
         "我这还是顶配", "十分钟才进去", "打完直接封号"]

# 标记词的字面/正向用法 = 硬负例（E 型, 上一轮的盲区）
LIT_POS = ["今天比赛赢麻了，太爽了", "老板发年终奖了，这下赢麻了", "这个月业绩赢麻了",
           "我们队赢麻了，冠军稳了", "这波操作赢麻了，队友都给力", "考试全过了，赢麻了",
           "这游戏是魂游，我挺喜欢的", "我喜欢玩魂游", "魂游我就爱这一口",
           "这个梗太典了，我笑了一天", "他说得太对了，我服气", "这个剧情确实绝了",
           "这游戏真好玩，强烈推荐", "画面很棒，我玩得很开心", "客服态度很好，问题解决了",
           "这次更新不错，优化到位了", "这活动挺良心的，送了不少", "官方终于听劝了",
           "价格很实惠，性价比高", "服务器很稳定，一次没掉线"]

CB_KWS = ["惊了", "居然", "竟然", "万万没想到", "99%的人", "不看后悔", "看完惊呆", "炸裂",
          "颠覆认知", "彻底改变", "一夜之间", "背后真相", "细思极恐", "你绝对", "必看",
          "秒懂", "天塌了", "全网疯传", "刷屏", "爆火", "神反转", "惊呆了", "血赚",
          "血亏", "逆天", "还能这样", "直接看傻", "不会吧不会吧", "震惊", "惊爆",
          "离大谱", "删前速看", "速看", "手慢无", "内幕", "黑幕", "毁三观", "颠覆三观",
          "你敢信", "全网最低", "史上最", "后悔没早", "紧急提醒", "紧急通知", "错过再等",
          "真相是", "真相曝光", "官方终于回应", "内部消息", "独家爆料", "我劝你别看"]
CB_TOPIC = ["这游戏", "这个UP主", "这部动漫", "国产手机", "显卡", "外卖", "房价", "考研",
            "减肥", "化妆", "健身", "数码产品", "电动车", "AI工具", "短视频", "直播间",
            "这部电影", "这个品牌", "这类产品", "这个行业", "这个软件", "这套配置",
            "这家公司", "这个平台", "这台笔记本", "这款耳机", "这个app"]
CB_TAIL = ["，看完我沉默了", "，真相让人意外", "，网友都炸了", "，官方连夜回应",
           "，背后原因令人深思", "，你还不知道就亏了", "，看完赶紧告诉家人",
           "，再不看就晚了", "，第3个太离谱", "，我劝你早点知道", "，结果出乎所有人意料"]

SP_AD = ["加微信", "加qq", "加vx", "优惠券", "优惠码", "返利", "折扣码", "私聊获取",
         "私信领取", "扫码关注", "闲鱼", "带货", "下单链接", "拼团", "秒杀价", "代购",
         "有偿", "付费进群", "公众号", "粉丝群", "微信群", "扫码", "回复关键词",
         "领取资料", "私信我", "后台回复", "进群", "赞助", "推广", "加群", "低价代充",
         "点我主页", "看我简介", "置顶评论", "第一个视频", "详情见简介", "链接在评论区"]
SP_BEN = ["领取资料", "免费课程", "内部渠道", "一手货源", "稳定出粉", "日入过千", "稳赚不赔",
          "包过", "低价出", "全网最低", "限时优惠", "名额有限", "先到先得", "加我就送",
          "0元领取", "轻松月入过万", "无需经验", "躺赚", "内部价", "清仓处理",
          "免费送", "限时免费", "仅剩最后几个", "点击领取"]

NEG_SUBJ = ["这游戏", "这优化", "这服务器", "这客服", "这更新", "这剧情", "这活动",
            "这价格", "这匹配", "这画质", "这策划", "这运营", "这个版本", "这次改动",
            "这建模", "这手感", "这数值", "这平衡", "这福利", "这家店", "这物流"]
NEG_BAD = ["卡得不行", "根本没法玩", "太让人失望了", "体验极差", "浪费时间", "纯纯的坑",
           "真的不行", "烂到爆", "一言难尽", "越来越差", "简直离谱", "劝退",
           "掉帧严重", "经常闪退", "连都连不上", "排队排到崩溃", "客服根本不理人",
           "修了跟没修一样", "越改越烂", "完全不值这个价", "后悔买了", "踩雷了"]
NEG_TAIL = ["，无语了", "，服了", "，麻了", "，真的想退坑", "，不会再买了",
            "，劝大家别入", "，心累", "，很难受", "，太难受了", "，失望透顶"]

FLAME_INSULT = ["傻逼", "煞笔", "智障", "脑残", "弱智", "废物", "狗东西",
                "滚出去", "闭嘴吧", "你算什么东西", "有病吧", "神经病",
                "别丢人了", "丢不丢人", "一群蠢货", "你们这些傻子",
                "眼睛不要可以捐了", "低能儿", "你就是个笑话", "笑死爹了",
                "你配吗", "你也配", "别叫了", "狗叫什么", "傻狗", "蠢狗",
                "死妈", "nmsl", "你个憨批", "憨批", "贱不贱啊", "真够贱的"]


# ================================================================ 合成
def gen_inject(bag, bases, marks, label, n, where=("pre", "post", "mid")):
    """把标记词注入**真实句子**。这是合成里最关键的一步:
    底料是真实文本, 只有标记是模板 —— 保证字面多样性, 而不是背模板。"""
    if not bases:
        return
    for _ in range(n):
        b = random.choice(bases)
        m = random.choice(marks)
        w = random.choice(where)
        if w == "pre":
            t = m + "，" + b
        elif w == "post":
            t = b.rstrip("。！？!?，, ") + "，" + m
        else:
            cut = random.randint(1, max(1, len(b) - 1))
            t = b[:cut] + "，" + m + "，" + b[cut:]
        emit(bag, [label], t)


def gen_yy(bag, bases, n):
    for _ in range(n):
        k = random.randint(0, 5)
        if k == 0:
            emit(bag, ["yy"], random.choice(YY_MARK))
        elif k == 1:
            emit(bag, ["yy"], random.choice(YY_MARK) + random.choice(YY_TAIL))
        elif k == 2:
            emit(bag, ["yy"], random.choice(YY_TOPIC) + random.choice(YY_MARK))
        elif k == 3:
            emit(bag, ["yy"], random.choice(YY_PAT) % random.choice(YY_TOPIC))
        elif k == 4:
            emit(bag, ["yy"], random.choice(YY_MARK) + "，" + random.choice(YY_MARK))
        else:
            # C 型语用反讽: 正面词 + 负面事实
            emit(bag, ["yy"], random.choice(YY_TOPIC) + random.choice(C_POS) +
                 "，" + random.choice(C_BAD))
    gen_inject(bag, bases, YY_MARK, "yy", int(n * 0.9))


def gen_cb(bag, bases, n):
    for _ in range(n):
        emit(bag, ["clickbait"],
             random.choice(CB_KWS) + "！" + random.choice(CB_TOPIC) + random.choice(CB_TAIL))
    gen_inject(bag, bases, CB_KWS, "clickbait", int(n * 0.8))


def gen_spam(bag, n):
    for _ in range(n):
        k = random.randint(0, 3)
        if k == 0:
            emit(bag, ["spam"], random.choice(SP_AD) + random.choice(SP_BEN))
        elif k == 1:
            emit(bag, ["spam"], random.choice(SP_AD) + "，" + random.choice(SP_BEN) + "，" +
                 random.choice(SP_BEN))
        elif k == 2:
            emit(bag, ["spam"], "有需要的" + random.choice(SP_AD) + random.choice(SP_BEN))
        else:
            emit(bag, ["spam"], random.choice(SP_BEN) + "，" + random.choice(SP_AD))


def gen_flame(bag, bases, n):
    gen_inject(bag, bases, FLAME_INSULT, "flame", n)


def gen_neg(bag, n):
    """负面情绪: 真实数据只有 2.4k 条酒店评论, 领域不对, 这里补齐。
    注意与 yy 的边界 —— 这里是**直白的抱怨**, 不是反讽。"""
    for _ in range(n):
        k = random.randint(0, 3)
        if k == 0:
            emit(bag, ["neg"], random.choice(NEG_SUBJ) + random.choice(NEG_BAD))
        elif k == 1:
            emit(bag, ["neg"], random.choice(NEG_SUBJ) + random.choice(NEG_BAD) +
                 random.choice(NEG_TAIL))
        elif k == 2:
            emit(bag, ["neg"], random.choice(NEG_BAD) + random.choice(NEG_TAIL))
        else:
            emit(bag, ["neg"], random.choice(NEG_SUBJ) + "，真的" +
                 random.choice(NEG_BAD))


def gen_hard_neg(bag, n):
    """★ 硬负例: 含标记词但语义正常。这是压误杀率最有效的一类样本。"""
    for _ in range(n):
        emit(bag, ["ok"], random.choice(LIT_POS))
    # 正常句子 + 中性补充, 拉高 ok 的字面多样性
    for _ in range(n):
        emit(bag, ["ok"], random.choice(LIT_POS) + "，" + random.choice(
            ["确实不错", "值得一试", "推荐给大家", "体验很好", "没有翻车", "符合预期"]))


def main():
    if not os.path.isdir(DATA):
        print("缺少数据目录: %s" % DATA)
        return 1

    lines = []
    bases = []          # 合成底料池(真实句子)
    print("%-46s %10s" % ("来源", "条数"))
    print("-" * 60)

    # ---- 真实
    for fn in (load_cold, load_items, load_chat, load_royal, load_senti, load_seed_yy):
        try:
            got = fn()
        except Exception as e:
            print("%-46s %10s" % ("%s(EXC)" % fn.__name__, e))
            continue
        if len(got) == 3:
            g, b, name = got
            bases += b
        else:
            g, name = got
        lines += g
        print("%-46s %10d" % (name[:46], len(g)))

    # ---- 合成
    n_before = len(lines)
    random.shuffle(bases)
    bases = bases[:40000]
    gen_yy(lines, bases, 42000)
    print("%-46s %10d" % ("★合成 yy(含C型反讽+真实句注入)", len(lines) - n_before))
    n_before = len(lines)
    gen_cb(lines, bases, 34000)
    print("%-46s %10d" % ("★合成 clickbait", len(lines) - n_before))
    n_before = len(lines)
    gen_spam(lines, 34000)
    print("%-46s %10d" % ("★合成 spam", len(lines) - n_before))
    n_before = len(lines)
    gen_flame(lines, bases, 36000)
    print("%-46s %10d" % ("★合成 flame(真实句注入)", len(lines) - n_before))
    n_before = len(lines)
    gen_neg(lines, 30000)
    print("%-46s %10d" % ("★合成 neg(直白抱怨)", len(lines) - n_before))
    n_before = len(lines)
    gen_hard_neg(lines, 9000)
    print("%-46s %10d" % ("★合成 硬负例(标记词字面用法→ok)", len(lines) - n_before))

    cnt = collections.Counter()
    for l in lines:
        for lab in re.findall(r"__label__(\S+)", l):
            cnt[lab] += 1

    random.shuffle(lines)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("-" * 60)
    print("总样本   : %d" % len(lines))
    print("标签分布 : %s" % dict(sorted(cnt.items(), key=lambda x: -x[1])))
    print("输出     : %s  (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
