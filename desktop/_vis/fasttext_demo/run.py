# -*- coding: utf-8 -*-
"""fastText 阴阳怪气识别 —— 真实原型验证

目的：不是做产品，是回答三个具体问题：
  1. fastText 怎么训练 / 怎么用
  2. 它对"阴阳怪气"到底能判到什么程度
  3. "赢麻了""又是魂游"这类具体输入，它实际会输出什么

同时实测本机上的训练耗时、模型体积、推理吞吐。
"""
import os
import sys
import time

import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
TRAIN = os.path.join(HERE, "train.txt")
MODEL = os.path.join(HERE, "model_yy.bin")

# 超参：中文必须开字符级 n-gram（minn/maxn），否则整句会被当成一个词
# bucket 决定哈希桶数，直接决定模型体积：体积 ≈ (词表 + bucket) × dim × 4 字节
#   bucket=20000  → ~8 MB      （本 demo 用的，够用）
#   bucket=200000 → ~80 MB
#   bucket=2000000（fastText 默认）→ ~800 MB  ★ 这是个必须知道的坑
HP = dict(
    lr=0.5,
    epoch=50,
    wordNgrams=2,
    minn=2,
    maxn=4,
    dim=100,
    loss="softmax",
    bucket=20000,
    verbose=0,
)

# ---------------------------------------------------------------- 评测集
# 每组：(输入, 期望标签, 该组属于什么类型的阴阳怪气)
CASES = [
    # A. 词汇标记型 —— fastText 的强项
    ("赢麻了", "yy", "A 词汇标记"),
    ("典中典", "yy", "A 词汇标记"),
    ("急了急了", "yy", "A 词汇标记"),
    ("笑死我了", "yy", "A 词汇标记"),
    ("啊对对对", "yy", "A 词汇标记"),
    ("你开心就好", "yy", "A 词汇标记"),
    ("遥遥领先", "yy", "A 词汇标记"),
    ("感恩", "yy", "A 词汇标记"),
    ("建议申遗", "yy", "A 词汇标记"),
    ("就这", "yy", "A 词汇标记"),
    # B. 句式标记型 —— 需要 wordNgrams 才能学到
    ("又是魂游", "yy", "B 句式标记"),
    ("又是这波", "yy", "B 句式标记"),
    ("又是这套", "yy", "B 句式标记"),
    ("又是熟悉的配方", "yy", "B 句式标记"),
    ("只能说懂的都懂", "yy", "B 句式标记"),
    ("这优化我给满分", "yy", "B 句式标记"),
    # D. 表情/符号型
    ("😅", "yy", "D 表情符号"),
    ("好起来了🥰", "yy", "D 表情符号"),
    ("赢！🥰", "yy", "D 表情符号"),
    # 正常：真夸赞 / 中性提问 / 认真批评
    ("这游戏画面真不错", "ok", "正常 真夸赞"),
    ("请问这个配置能跑吗", "ok", "正常 中性提问"),
    ("这个价格有点贵了", "ok", "正常 认真批评"),
    ("太好玩了，一口气打了五个小时", "ok", "正常 真夸赞"),
    ("希望官方能修一下bug", "ok", "正常 认真批评"),
    ("支持一下", "ok", "正常 真夸赞"),
    ("这游戏优化有点差，掉帧严重", "ok", "正常 认真批评"),
    ("这是魂游", "ok", "正常 中性陈述"),
    # ★ C. 语用反讽型（无标记词，靠事实反差）—— 预期 fastText 盲区
    ("这游戏优化真好，我4080都掉帧", "yy", "C 语用反讽(盲区)"),
    ("这服务器真稳，稳到我掉线三次", "yy", "C 语用反讽(盲区)"),
    ("这客服真热情，热情到三天不回消息", "yy", "C 语用反讽(盲区)"),
    ("画面真棒，棒到我电脑冒烟了", "yy", "C 语用反讽(盲区)"),
    ("这活动真良心，良心到我钱包空了", "yy", "C 语用反讽(盲区)"),
    # ★ E. 标记词的字面用法 —— 预期 fastText 盲区
    ("今天比赛赢麻了，太爽了", "ok", "E 字面用法(盲区)"),
    ("老板发年终奖了，这下赢麻了", "ok", "E 字面用法(盲区)"),
    ("这个月业绩赢麻了", "ok", "E 字面用法(盲区)"),
    ("这游戏是魂游，我挺喜欢的", "ok", "E 字面用法(盲区)"),
    ("我喜欢玩魂游", "ok", "E 字面用法(盲区)"),
]


def build():
    print("=" * 74)
    print("【1】训练")
    print("=" * 74)
    n_lines = sum(1 for _ in open(TRAIN, encoding="utf-8"))
    n_yy = sum(1 for l in open(TRAIN, encoding="utf-8") if l.startswith("__label__yy"))
    n_ok = n_lines - n_yy
    print("训练语料 : %s" % TRAIN)
    print("样本总数 : %d  (阴阳怪气 %d / 正常 %d)" % (n_lines, n_yy, n_ok))
    print("超参     : %s" % HP)
    print()
    t0 = time.time()
    model = fasttext.train_supervised(input=TRAIN, **HP)
    dt = time.time() - t0
    model.save_model(MODEL)
    size = os.path.getsize(MODEL)
    print("训练耗时 : %.2f 秒" % dt)
    print("模型体积 : %.2f MB  (%d 字节)" % (size / 1048576.0, size))
    print("标签集合 : %s" % model.labels)
    print()
    return model, dt, size


def evaluate(model):
    print("=" * 74)
    print("【2】评测：它到底判得对不对")
    print("=" * 74)
    groups = {}
    rows = []
    for text, want, kind in CASES:
        labels, probs = model.predict(text, k=2)
        got = labels[0].replace("__label__", "")
        conf = probs[0]
        ok = (got == want)
        rows.append((text, want, got, conf, kind, ok))
        g = groups.setdefault(kind, [0, 0])
        g[1] += 1
        if ok:
            g[0] += 1

    print("%-30s %-5s %-5s %-7s %s" % ("输入", "期望", "实际", "置信度", "判定"))
    print("-" * 74)
    for text, want, got, conf, kind, ok in rows:
        mark = "OK " if ok else "❌ "
        print("%-30s %-5s %-5s %6.3f  %s%s" % (text, want, got, conf, mark, kind))

    print()
    print("-" * 74)
    print("%-22s %-12s %s" % ("类型", "正确/总数", "命中率"))
    print("-" * 74)
    order = ["A 词汇标记", "B 句式标记", "D 表情符号",
             "正常 真夸赞", "正常 中性提问", "正常 认真批评", "正常 中性陈述",
             "C 语用反讽(盲区)", "E 字面用法(盲区)"]
    for kind in order:
        if kind not in groups:
            continue
        hit, tot = groups[kind]
        bar = "█" * int(round(hit * 10.0 / tot)) + "·" * (10 - int(round(hit * 10.0 / tot)))
        print("%-22s %-12s %s %d%%" % (kind, "%d/%d" % (hit, tot), bar, int(round(hit * 100.0 / tot))))

    tot_ok = sum(1 for r in rows if r[5])
    print("-" * 74)
    print("总体：%d/%d = %.1f%%" % (tot_ok, len(rows), 100.0 * tot_ok / len(rows)))
    print()
    return rows


def show_features(model):
    print("=" * 74)
    print("【3】为什么？看 n-gram 特征就明白了")
    print("=" * 74)
    print("fastText 不理解句子，它只看字符串切出来的 n-gram。")
    print("下面把两句输入拆成实际喂给模型的子词特征：")
    print()
    pairs = [
        ("赢麻了", "今天比赛赢麻了，太爽了"),
        ("又是魂游", "这游戏是魂游，我挺喜欢的"),
    ]
    for a, b in pairs:
        print("  「%s」" % a)
        try:
            subs, _ = model.get_subwords(a)
            print("      特征: %s" % "  ".join(subs))
        except Exception as e:
            print("      (get_subwords 不可用: %s)" % e)
        print("  「%s」" % b)
        try:
            subs, _ = model.get_subwords(b)
            print("      特征: %s" % "  ".join(subs))
        except Exception as e:
            print("      (get_subwords 不可用: %s)" % e)
        # 看两者共享了多少特征
        sa, _ = model.get_subwords(a)
        sb, _ = model.get_subwords(b)
        shared = [w for w in sa if w in set(sb)]
        print("      → 共享特征: %s   (%d/%d)" % ("  ".join(shared) or "无", len(shared), len(sa)))
        print()


def bench(model):
    print("=" * 74)
    print("【4】速度：本机实测吞吐")
    print("=" * 74)
    texts = [t for t, _, _ in CASES]

    # 单条延迟：重复多次取平均（单次调用太快，直接计时会得到 0）
    n1 = 2000
    t0 = time.perf_counter()
    for i in range(n1):
        model.predict(texts[i % len(texts)])
    dt1 = time.perf_counter() - t0
    print("逐条调用  %6d 次   用时 %8.4f 秒  →  %10.1f 条/秒   (%.4f ms/条)"
          % (n1, dt1, n1 / dt1, dt1 * 1000.0 / n1))

    # 批量调用
    batch = texts * 300
    t0 = time.perf_counter()
    model.predict(batch)
    dt2 = time.perf_counter() - t0
    print("批量调用  %6d 条   用时 %8.4f 秒  →  %10.1f 条/秒   (%.4f ms/条)"
          % (len(batch), dt2, len(batch) / dt2, dt2 * 1000.0 / len(batch)))

    print()
    print("对照：")
    print("  Qwen3-0.6B 纯 CPU        0.83 ~ 3.3 条/秒   (1200 ~ 300 ms/条)")
    print("  中文 BERT encoder 估算   ~170 条/秒        (~5.94 ms/条)")
    print("  弹幕突发需求             ~1000 条/秒")
    print()


def main():
    if not os.path.exists(TRAIN):
        print("缺少训练集: %s" % TRAIN)
        return 1
    model, dt, size = build()
    evaluate(model)
    show_features(model)
    bench(model)
    print("=" * 74)
    print("产物")
    print("=" * 74)
    print("  训练集 : %s" % TRAIN)
    print("  模型   : %s  (%.2f MB)" % (MODEL, size / 1048576.0))
    print()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
