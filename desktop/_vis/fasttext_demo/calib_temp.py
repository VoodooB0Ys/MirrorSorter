# -*- coding: utf-8 -*-
"""温度标定：在真实留出集上扫 T，让 softmax 置信度重新变得可分辨。

温度缩放不改变 argmax（所以与真 fastText 的 100% 一致率不受影响），
只把过饱和的分数重新拉开，使设置里的"判定阈值"重新有意义。
"""
import os
import re
import sys
import csv
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ftrt  # noqa: E402

COLD = os.path.join(HERE, "_data", "COLDataset", "COLDataset", "test.csv")


def main():
    m = ftrt.FastTextLite(os.path.join(HERE, "ft_model.bin"))
    lab = m.labels
    ok_i = lab.index("ok")
    flame_i = lab.index("flame")

    rows = list(csv.DictReader(open(COLD, encoding="utf-8-sig")))
    data = []
    for r in rows:
        t = re.sub(r"\s+", " ", (r.get("TEXT") or "")).strip()[:64]
        g = str(r.get("label") or "").strip()
        if t and g in ("0", "1"):
            data.append((t, 1 if g == "1" else 0))
    print("真实留出样本 %d 条" % len(data))

    # 先算一次 logits
    Z = []
    for t, _ in data:
        h = m._hidden(t)
        Z.append([sum(a * b for a, b in zip(rw, h)) for rw in m._out_rows])

    def softmax(z, T):
        mx = max(z)
        e = [math.exp((x - mx) / T) for x in z]
        s = sum(e)
        return [x / s for x in e]

    print()
    print("%6s %8s %8s %8s   %s" % ("T", "p10", "p50", "p90", "说明"))
    print("-" * 62)
    for T in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0):
        tops = sorted(max(softmax(z, T)) for z in Z)
        p10, p50, p90 = tops[len(tops) // 10], tops[len(tops) // 2], tops[len(tops) * 9 // 10]
        print("%6.1f %8.3f %8.3f %8.3f" % (T, p10, p50, p90))

    print()
    print("=" * 76)
    print("选定 T 后, 看「把 top1=flame 且置信度>=阈值 判为冒犯」的 P/R/F1")
    print("=" * 76)
    for T in (3.0, 5.0, 8.0):
        print("--- T=%.1f ---" % T)
        print("%8s %10s %10s %10s %10s" % ("阈值", "预测冒犯", "精确率", "召回率", "F1"))
        for th in (0.50, 0.70, 0.85, 0.93):
            tp = fp = fn = tn = 0
            for (t, g), z in zip(data, Z):
                p = softmax(z, T)
                i = max(range(len(p)), key=lambda k: p[k])
                pred = 1 if (i == flame_i and p[i] >= th) else 0
                if g and pred:
                    tp += 1
                elif g:
                    fn += 1
                elif pred:
                    fp += 1
                else:
                    tn += 1
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            print("%7.0f%% %10d %9.1f%% %9.1f%% %9.1f%%" % (th * 100, tp + fp, prec * 100, rec * 100, f1 * 100))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
