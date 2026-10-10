# -*- coding: utf-8 -*-
"""诊断：为什么 COLDataset 留出集上误杀多？并做阈值扫描。"""
import os
import re
import sys
import csv
import collections

import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "ft_big.bin")
COLD_TEST = os.path.join(HERE, "_data", "COLDataset", "COLDataset", "test.csv")


def main():
    m = fasttext.load_model(MODEL)
    texts, gold = [], []
    with open(COLD_TEST, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            lab = str(row.get("label") or "").strip()
            t = (row.get("TEXT") or "").strip()
            if lab in ("0", "1") and t:
                texts.append(re.sub(r"\s+", " ", t).strip()[:64])
                gold.append(1 if lab == "1" else 0)

    labs, probs = m.predict(texts, k=1)
    got = [l[0].replace("__label__", "") for l in labs]
    conf = [p[0] for p in probs]

    print("=" * 76)
    print("【A】把 flame 当唯一判据时, 置信度阈值扫描（运行期 ft_thresh 就是它）")
    print("=" * 76)
    print("%8s %10s %10s %10s %10s %10s" % ("阈值", "预测冒犯", "精确率", "召回率", "F1", "误杀率"))
    print("-" * 76)
    for th in (0.0, 0.50, 0.70, 0.80, 0.90, 0.95, 0.99):
        tp = fp = fn = tn = 0
        for g, lab, c in zip(gold, got, conf):
            pred = 1 if (lab == "flame" and c >= th) else 0
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
        fpr = fp / (fp + tn) if fp + tn else 0.0
        print("%7.0f%% %10d %9.1f%% %9.1f%% %9.1f%% %9.1f%%"
              % (th * 100, tp + fp, prec * 100, rec * 100, f1 * 100, fpr * 100))

    print()
    print("=" * 76)
    print("【B】模型在这份数据上的整体标签分布（真实分布是 冒犯39.6% / 正常60.4%）")
    print("=" * 76)
    c = collections.Counter(got)
    for k, v in c.most_common():
        print("   %-12s %6d  %5.1f%%" % (k, v, 100.0 * v / len(got)))

    print()
    print("=" * 76)
    print("【C】误杀样本（真值=正常, 模型高置信判 flame）")
    print("=" * 76)
    fp_rows = [(t, cf) for t, g, lab, cf in zip(texts, gold, got, conf)
               if g == 0 and lab == "flame"]
    fp_rows.sort(key=lambda x: -x[1])
    for t, cf in fp_rows[:15]:
        print("   %.3f  %s" % (cf, t[:60]))
    print("   ... 共 %d 条误杀" % len(fp_rows))

    print()
    print("=" * 76)
    print("【D】漏抓样本（真值=冒犯, 模型判了别的类）")
    print("=" * 76)
    fn_rows = [(t, lab, cf) for t, g, lab, cf in zip(texts, gold, got, conf) if g == 1 and lab != "flame"]
    cc = collections.Counter(l for _, l, _ in fn_rows)
    print("   漏抓被归到了: %s" % dict(cc.most_common()))
    for t, lab, cf in fn_rows[:12]:
        print("   %-10s %.3f  %s" % (lab, cf, t[:56]))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
