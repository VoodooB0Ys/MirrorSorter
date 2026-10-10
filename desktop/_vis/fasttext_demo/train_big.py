# -*- coding: utf-8 -*-
"""训练 50MB+ 的 fastText 中文内容分拣模型，并做真实留出评测。

体积公式（必须记住）：
    文件大小 ≈ (词表行数 + bucket) × dim × 4 字节
    所以想控制体积，调 bucket 和 dim，**不要**放任 fastText 默认 bucket=2000000（那是 ~800MB）。

本脚本目标：约 56MB（bucket=140000, dim=100）
"""
import os
import re
import sys
import csv
import time
import random
import collections

import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
TRAIN = os.path.join(HERE, "ft_train.txt")
MODEL = os.path.join(HERE, "ft_big.bin")
COLD_TEST = os.path.join(HERE, "_data", "COLDataset", "COLDataset", "test.csv")

HP = dict(
    lr=0.5,
    epoch=25,
    wordNgrams=1,       # ★ 必须=1。纯 Python 运行期已实测: wordNgrams>1 时 predict 的
                        #   词 n-gram 拼接方式极难精确复刻(实测一致率 99.8% 而非 100%),
                        #   而中文内容几乎无空格分词, 关掉它精度代价≈0, 换来 100% 可复现。
    minn=2,
    maxn=4,
    dim=100,
    bucket=140000,      # ★ 体积主控：140000 × 100 × 4 ≈ 56 MB
    minCount=5,         # 整句 token 出现<5 次就丢, 只留高频短语, 否则词表会撑爆
    loss="softmax",
    thread=8,
    verbose=2,
)

LABELS = ["ok", "flame", "spam", "clickbait", "yy", "neg"]

# ---------------------------------------------------------------- 探针集
# 用于回答"具体输入它到底判成什么"，尤其是上一轮发现的盲区
PROBE = [
    ("赢麻了", "yy", "A 词汇标记"),
    ("典中典", "yy", "A 词汇标记"),
    ("急了急了", "yy", "A 词汇标记"),
    ("啊对对对", "yy", "A 词汇标记"),
    ("你开心就好", "yy", "A 词汇标记"),
    ("遥遥领先", "yy", "A 词汇标记"),
    ("建议申遗", "yy", "A 词汇标记"),
    ("就这", "yy", "A 词汇标记"),
    ("又是魂游", "yy", "B 句式标记"),
    ("又是这套", "yy", "B 句式标记"),
    ("只能说懂的都懂", "yy", "B 句式标记"),
    ("这优化我给满分", "yy", "B 句式标记"),
    ("😅", "yy", "D 表情符号"),
    ("好起来了🥰", "yy", "D 表情符号"),
    ("这游戏优化真好，我4080都掉帧", "yy", "C 语用反讽(旧盲区)"),
    ("这服务器真稳，稳到我掉线三次", "yy", "C 语用反讽(旧盲区)"),
    ("这客服真热情，热情到三天不回消息", "yy", "C 语用反讽(旧盲区)"),
    ("今天比赛赢麻了，太爽了", "ok", "E 字面用法(旧盲区)"),
    ("这游戏是魂游，我挺喜欢的", "ok", "E 字面用法(旧盲区)"),
    ("这个月业绩赢麻了", "ok", "E 字面用法(旧盲区)"),
    ("这游戏画面真不错", "ok", "正常 真夸赞"),
    ("请问这个配置能跑吗", "ok", "正常 中性提问"),
    ("这个价格有点贵了", "ok", "正常 认真批评"),
    ("太好玩了，一口气打了五个小时", "ok", "正常 真夸赞"),
    ("希望官方能修一下bug", "ok", "正常 认真批评"),
    ("这游戏优化有点差，掉帧严重", "ok", "正常 认真批评"),
    ("惊了！这个显卡居然只要999", "clickbait", "标题党"),
    ("99%的人不知道的真相，看完惊呆", "clickbait", "标题党"),
    ("加微信领取免费课程，名额有限", "spam", "广告"),
    ("私信我，一手货源稳定出粉", "spam", "广告"),
    ("你就是个废物，滚出去", "flame", "引战辱骂"),
    ("一群蠢货，眼睛不要可以捐了", "flame", "引战辱骂"),
    ("这优化卡得不行，根本没法玩", "neg", "负面抱怨"),
]


def train():
    print("=" * 76)
    print("【1】训练")
    print("=" * 76)
    n = sum(1 for _ in open(TRAIN, encoding="utf-8"))
    print("语料     : %s" % TRAIN)
    print("样本数   : %d" % n)
    print("超参     : %s" % HP)
    print("预估体积 : (词表 + %d) × %d × 4 ≈ %.0f MB"
          % (HP["bucket"], HP["dim"], HP["bucket"] * HP["dim"] * 4 / 1048576.0))
    print()
    t0 = time.time()
    m = fasttext.train_supervised(input=TRAIN, **HP)
    dt = time.time() - t0
    m.save_model(MODEL)
    size = os.path.getsize(MODEL)
    print()
    print("训练耗时 : %.1f 秒" % dt)
    print("模型体积 : %.2f MB  (%d 字节)" % (size / 1048576.0, size))
    print("词表大小 : %d" % len(m.words))
    print("标签     : %s" % [l.replace("__label__", "") for l in m.labels])
    print()
    return m, dt, size


def eval_cold_real(m):
    """★ 唯一诚实的评测：COLDataset test.csv 5,324 条真实中文社交评论, 从未进训练。
    只做二分类口径：预测里出现 flame → 判为冒犯。"""
    print("=" * 76)
    print("【2】真实留出评测 —— COLDataset test.csv（从未参与训练）")
    print("=" * 76)
    if not os.path.exists(COLD_TEST):
        print("缺少 %s" % COLD_TEST)
        return
    texts, gold = [], []
    with open(COLD_TEST, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            lab = str(row.get("label") or "").strip()
            t = row.get("TEXT") or ""
            if lab in ("0", "1") and t.strip():
                texts.append(re.sub(r"\s+", " ", t).strip()[:64])
                gold.append(1 if lab == "1" else 0)
    t0 = time.perf_counter()
    labs, _ = m.predict(texts, k=1)
    dt = time.perf_counter() - t0
    tp = fp = fn = tn = 0
    for g, ls in zip(gold, labs):
        pred = 1 if "flame" in ls[0] else 0
        if g and pred:
            tp += 1
        elif g and not pred:
            fn += 1
        elif not g and pred:
            fp += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    acc = (tp + tn) / len(gold)
    print("样本       : %d  (冒犯 %d / 正常 %d)" % (len(gold), sum(gold), len(gold) - sum(gold)))
    print("预测为冒犯 : TP=%d  FP=%d  FN=%d  TN=%d" % (tp, fp, fn, tn))
    print("精确率     : %.2f%%   (预测冒犯里有多少是真冒犯 —— 决定误杀)" % (prec * 100))
    print("召回率     : %.2f%%   (真冒犯里抓出多少)" % (rec * 100))
    print("F1         : %.2f%%" % (f1 * 100))
    print("准确率     : %.2f%%" % (acc * 100))
    print("吞吐       : %.0f 条/秒  (C++ 侧, %.4f ms/条)" % (len(texts) / dt, dt * 1000 / len(texts)))
    print()
    return prec, rec, f1, acc


def eval_probe(m):
    print("=" * 76)
    print("【3】探针：具体输入判成什么（含上一轮发现的盲区）")
    print("=" * 76)
    groups = collections.OrderedDict()
    ok_n = 0
    print("%-34s %-10s %-10s %6s  %s" % ("输入", "期望", "实际", "置信", ""))
    print("-" * 76)
    for text, want, kind in PROBE:
        labs, probs = m.predict(text, k=1)
        got = labs[0].replace("__label__", "")
        hit = (got == want)
        ok_n += hit
        g = groups.setdefault(kind, [0, 0])
        g[1] += 1
        g[0] += hit
        print("%-34s %-10s %-10s %6.3f  %s" % (text[:32], want, got, probs[0], "OK" if hit else "❌"))
    print("-" * 76)
    print("按类型:")
    for kind, (h, t) in groups.items():
        print("   %-24s %d/%d  %s" % (kind, h, t, "█" * int(round(h * 10.0 / t))))
    print("   探针总计 %d/%d = %.1f%%" % (ok_n, len(PROBE), 100.0 * ok_n / len(PROBE)))
    print()


def bench(m):
    print("=" * 76)
    print("【4】速度")
    print("=" * 76)
    short = [t for t, _, _ in PROBE]
    long_ = ["这游戏优化有点差，掉帧严重，希望官方能尽快修复这个问题，不然真的没法玩了，我买了首发结果体验这么差" * 2]
    for name, arr in (("短文本(平均 %d 字)" % (sum(len(x) for x in short) // len(short)), short * 400),
                      ("长文本(128 字)", long_ * 600)):
        t0 = time.perf_counter()
        m.predict(arr)
        dt = time.perf_counter() - t0
        print("%-24s %6d 条  %7.4f 秒  →  %9.0f 条/秒  (%.4f ms/条)"
              % (name, len(arr), dt, len(arr) / dt, dt * 1000 / len(arr)))
    print()


def main():
    if not os.path.exists(TRAIN):
        print("缺少训练集: %s" % TRAIN)
        return 1
    m, dt, size = train()
    eval_cold_real(m)
    eval_probe(m)
    bench(m)
    print("=" * 76)
    print("产物: %s  (%.2f MB)" % (MODEL, size / 1048576.0))
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
