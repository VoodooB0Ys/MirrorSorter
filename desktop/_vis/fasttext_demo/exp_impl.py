# -*- coding: utf-8 -*-
"""受控实验：分步拆解 fastText predict 的实现。

模型 A: minn=maxn=0  → 完全没有子词, 只剩词表查找。可单独验证「词 id → 均值 → sigmoid」。
模型 B: minn=maxn=2  → 只有 2 元子词。可单独验证子词枚举 + 哈希。
模型 C: wordNgrams=2 → 验证词 n-gram。
"""
import os
import sys
import numpy as np
import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(HERE, "_exp")
SEED = 2166136261
BOW, EOW = "<", ">"


def fnv(s):
    x = SEED
    for b in s.encode("utf-8"):
        if b >= 128:
            b -= 256
        x = (x ^ b) & 0xFFFFFFFF
        x = (x * 16777619) & 0xFFFFFFFF
    return x


def ng(s, minn, maxn):
    out = []
    for i in range(len(s)):
        for ln in range(minn, maxn + 1):
            if i + ln <= len(s):
                out.append(s[i:i + ln])
    return out


def sig(z):
    return 1.0 / (1.0 + np.exp(-z))


def mk(path, minn, maxn, wng, dim=16, bucket=1000, epoch=30):
    """造一个小语料, 词表完全可控。"""
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    rng = np.random.RandomState(7)
    lines = []
    for i in range(400):
        lab = "pos" if i % 2 == 0 else "neg"
        k = 1 + (i % 3)
        ws = [words[(i * 3 + j * 5) % len(words)] for j in range(k)]
        lines.append("__label__%s %s" % (lab, " ".join(ws)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return fasttext.train_supervised(input=path, dim=dim, bucket=bucket, minn=minn, maxn=maxn,
                                     wordNgrams=wng, epoch=epoch, lr=0.5, minCount=1,
                                     loss="softmax", thread=1, verbose=0)


def check(m, texts, minn, maxn, use_word, use_sub, wrap, use_wng, tag):
    A = m.f.getArgs()
    nw, bk = len(m.words), A.bucket
    W, O = m.get_input_matrix(), m.get_output_matrix()
    lab = [l.replace("__label__", "") for l in m.labels]
    print("  [%s] nwords=%d bucket=%d minn=%d maxn=%d wordNgrams=%d"
          % (tag, nw, bk, A.minn, A.maxn, A.wordNgrams))

    def wid(t):
        try:
            v = m.get_word_id(t)
            return v if v is not None else -1
        except Exception:
            return -1

    def ids_of(text):
        toks = text.split()
        ids, hashes = [], []
        for t in toks:
            w = wid(t)
            if use_word and w >= 0:
                ids.append(w)
            if use_sub:
                s = (BOW + t + EOW) if wrap else t
                ids += [nw + fnv(g) % bk for g in ng(s, minn, maxn)]
            hashes.append(fnv(t) % bk if bk else 0)
        if use_wng and len(toks) > 1 and bk:
            for i in range(len(hashes)):
                h = hashes[i]
                for j in range(i + 1, min(len(hashes), i + A.wordNgrams)):
                    h = (h * 116049371 + hashes[j]) & 0xFFFFFFFF
                    ids.append(nw + h % bk)
        return ids

    worst = 0.0
    for t in texts:
        labs, ps = m.predict(t, k=-1)
        true = {l.replace("__label__", ""): float(ps[i]) for i, l in enumerate(labs)}
        ids = ids_of(t)
        if not ids:
            continue
        h = W[ids].mean(axis=0)
        p = sig(O.dot(h))
        got = {lab[i]: float(p[i]) for i in range(len(lab))}
        d = max(abs(true.get(k, 0) - got.get(k, 0)) for k in set(true) | set(got))
        worst = max(worst, d)
    print("      最大概率差 = %.3e  %s" % (worst, "★一致" if worst < 1e-5 else ("接近" if worst < 1e-3 else "❌")))
    return worst


def main():
    os.makedirs(TMP, exist_ok=True)
    pa = os.path.join(TMP, "a.bin")
    print("=" * 78)
    print("模型 A: minn=maxn=0（无子词）—— 只验证词表查找 + 均值 + sigmoid")
    print("=" * 78)
    ma = mk(pa, 0, 0, 1)
    print("  词表:", ma.words[:12], " labels:", ma.labels)
    texts = ["alpha", "alpha beta", "gamma delta epsilon", "zeta", "alpha alpha beta"]
    check(ma, texts, 0, 0, 1, 0, 0, 0, "word only")

    print()
    print("=" * 78)
    print("模型 B: minn=maxn=2（只有 2 元子词）—— 验证子词枚举 + 哈希")
    print("=" * 78)
    pb = os.path.join(TMP, "b.bin")
    mb = mk(pb, 2, 2, 1)
    texts_b = ["alpha", "alpha beta", "zzz", "alpha zzz"]
    for uw in (0, 1):
        for wp in (0, 1):
            check(mb, texts_b, 2, 2, uw, 1, wp, 0, "word=%d wrap=%d" % (uw, wp))
    # 交叉验证 get_subwords 的 id
    subs, ids = mb.get_subwords("alpha")
    nw = len(mb.words)
    mine = [nw + fnv(g) % mb.f.getArgs().bucket for g in ng(BOW + "alpha" + EOW, 2, 2)]
    print("      get_subwords ids =", [i for i in ids if i >= nw])
    print("      我算的         ids =", mine)

    print()
    print("=" * 78)
    print("模型 C: wordNgrams=2 —— 验证词 n-gram 的拼接方式")
    print("=" * 78)
    pc = os.path.join(TMP, "c.bin")
    mc = mk(pc, 0, 0, 2)
    check(mc, ["alpha beta", "gamma delta epsilon"], 0, 0, 1, 0, 0, 1, "word+wng")
    check(mc, ["alpha beta", "gamma delta epsilon"], 0, 0, 1, 0, 0, 0, "word only")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
