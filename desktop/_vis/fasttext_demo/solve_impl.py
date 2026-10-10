# -*- coding: utf-8 -*-
"""穷举 fastText predict 的特征集实现，逐条找出与真模型完全一致的那一种。"""
import os
import sys
import numpy as np
import fasttext

BOW, EOW, SEED = "<", ">", 2166136261


def fnv(s):
    x = SEED
    for b in s.encode("utf-8"):
        if b >= 128:
            b -= 256
        x = (x ^ b) & 0xFFFFFFFF
        x = (x * 16777619) & 0xFFFFFFFF
    return x


def ng_all(s, minn, maxn):
    out = []
    for i in range(len(s)):
        for ln in range(minn, maxn + 1):
            if i + ln <= len(s):
                out.append(s[i:i + ln])
    return out


def softmax(z):
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def sigmoid(z):
    """★ fastText 的 loss='softmax' 其实是**逐标签 sigmoid**(one-vs-all), 不是联合 softmax。
    这是最容易被误解的一点 —— 用错会让结果完全不同。"""
    return 1.0 / (1.0 + np.exp(-z))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "ft_big.bin"
    m = fasttext.load_model(path)
    A = m.f.getArgs()
    nw, bk, dim, minn, maxn = len(m.words), A.bucket, A.dim, A.minn, A.maxn
    W, O = m.get_input_matrix(), m.get_output_matrix()
    lab = [l.replace("__label__", "") for l in m.labels]
    print("nwords=%d bucket=%d dim=%d minn=%d maxn=%d wordNgrams=%d" % (nw, bk, dim, minn, maxn, A.wordNgrams))
    print("'😅' in words? %s   词表里含emoji的条数: %d"
          % ("😅" in m.words, sum(1 for w in m.words if any(ord(c) > 0x2000 for c in w))))
    print()

    def wid_of(tok):
        try:
            v = m.get_word_id(tok)
            return v if v is not None else -1
        except Exception:
            return -1

    def feats(tok, use_word, use_sub, wrap):
        ids = []
        w = wid_of(tok)
        if use_word and w >= 0:
            ids.append(w)
        if use_sub:
            s = (BOW + tok + EOW) if wrap else tok
            ids += [nw + fnv(g) % bk for g in ng_all(s, minn, maxn)]
        return ids

    def probs_of(text, use_word, use_sub, wrap, act=sigmoid):
        ids = []
        for tok in text.split():
            ids += feats(tok, use_word, use_sub, wrap)
        if not ids:
            return None
        h = W[ids].mean(axis=0)
        p = act(O.dot(h))
        return {lab[i]: float(p[i]) for i in range(len(lab))}

    GRID = [(uw, us, wp) for uw in (0, 1) for us in (0, 1) for wp in (0, 1)]
    print("%-26s %-10s %s" % ("输入", "最匹配", "各假设最大概率差"))
    print("-" * 90)
    for t in ["赢麻了", "又是魂游", "啊对对对", "😅", "今天比赛赢麻了，太爽了",
              "这游戏优化真好，我4080都掉帧", "好起来了🥰", "魂游", "🎮"]:
        labs, ps = m.predict(t, k=-1)
        true = {l.replace("__label__", ""): float(ps[i]) for i, l in enumerate(labs)}
        row = []
        for uw, us, wp in GRID:
            got = probs_of(t, uw, us, wp)
            if got is None:
                row.append((uw, us, wp, 9.9))
                continue
            d = max(abs(true.get(k, 0) - got.get(k, 0)) for k in set(true) | set(got))
            row.append((uw, us, wp, d))
        row.sort(key=lambda x: x[3])
        b = row[0]
        tag = "word=%d sub=%d wrap=%d" % (b[0], b[1], b[2])
        print("%-26s %-10s 最佳=%.2e  次佳=%.2e  %s"
              % (t[:24], tag, b[3], row[1][3],
                 "★完全一致" if b[3] < 1e-6 else ("接近" if b[3] < 1e-3 else "不一致")))
        if b[3] >= 1e-6:
            print("        真值: %s" % {k: round(v, 4) for k, v in sorted(true.items(), key=lambda x: -x[1])[:3]})
            print("        我算: %s" % {k: round(v, 4) for k, v in
                                      sorted(probs_of(t, b[0], b[1], b[2]).items(), key=lambda x: -x[1])[:3]})
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
