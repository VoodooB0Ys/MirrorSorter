# -*- coding: utf-8 -*-
"""打印真实数值, 定位 predict 管线差异。(自训练, 不依赖外部文件)"""
import os
import sys
import numpy as np
import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(HERE, "_exp")
os.makedirs(TMP, exist_ok=True)


def sig(z):
    return 1.0 / (1.0 + np.exp(-z))


def train(minn, maxn, wng, dim=16, bucket=1000, epoch=30):
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    lines = []
    for i in range(400):
        lab = "pos" if i % 2 == 0 else "neg"
        k = 1 + (i % 3)
        ws = [words[(i * 3 + j * 5) % len(words)] for j in range(k)]
        lines.append("__label__%s %s" % (lab, " ".join(ws)))
    p = os.path.join(TMP, "t_%d_%d_%d.txt" % (minn, maxn, wng))
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return fasttext.train_supervised(input=p, dim=dim, bucket=bucket, minn=minn, maxn=maxn,
                                     wordNgrams=wng, epoch=epoch, lr=0.5, minCount=1,
                                     loss="softmax", thread=1, verbose=0)


def dump(m, texts, tag):
    A = m.f.getArgs()
    W, O = m.get_input_matrix(), m.get_output_matrix()
    lab = [l.replace("__label__", "") for l in m.labels]
    print("#" * 78)
    print("# %s   words=%s  dim=%d bucket=%d minn=%d maxn=%d wng=%d"
          % (tag, m.words, A.dim, A.bucket, A.minn, A.maxn, A.wordNgrams))
    print("# W=%s O=%s" % (W.shape, O.shape))
    for text in texts:
        labs, ps = m.predict(text, k=-1)
        true = {l.replace("__label__", ""): float(ps[i]) for i, l in enumerate(labs)}
        tv = np.array([true[l] for l in lab])
        tl = np.log(tv / (1 - tv + 1e-30))
        ids = []
        for t in text.split():
            try:
                w = m.get_word_id(t)
            except Exception:
                w = -1
            if w >= 0:
                ids.append(w)
        print("\n  输入 %r   ids=%s" % (text, ids))
        print("    真 logits(反推)      = %s" % np.round(tl, 4))
        for nm, h in (("mean", W[ids].mean(axis=0)), ("sum", W[ids].sum(axis=0))):
            print("    我 %-5s logits       = %s" % (nm, np.round(O.dot(h), 4)))
        # 单特征也试一遍: 只取第一个 id
        if ids:
            print("    我 首个 logits       = %s" % np.round(O.dot(W[ids[0]]), 4))
        print("    真 probs             = %s" % {k: round(v, 6) for k, v in true.items()})
        print("    我 probs(mean,sig)   = %s"
              % {lab[i]: round(float(sig(O.dot(W[ids].mean(axis=0)))[i]), 6) for i in range(len(lab))})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 78)
    print("模型 A: minn=0 maxn=0 wordNgrams=1  → 只有词表查找")
    print("=" * 78)
    dump(train(0, 0, 1), ["alpha", "alpha beta", "gamma"], "A")
    sys.stdout.flush()
    os._exit(0)
