# -*- coding: utf-8 -*-
"""用已知权重反推 fastText predict 的确切特征集与哈希算法。

思路：我有完整权重（input/output matrix），所以可以自己算 logits → softmax，
和真模型的输出逐条比对。哪种假设能对上，就说明 predict 是那样实现的。
"""
import sys
import numpy as np
import fasttext

BOW, EOW = "<", ">"
SEED = 2166136261


def fnv(s):
    """fastText 的 hash: FNV-1a。
    ★ 关键：fastText 源码是 `h ^= uint32_t(int8_t(str[i]))` —— 字节先做**有符号**扩展，
      即 >=128 的字节要减 256 再异或。少了这一步整个哈希全错。"""
    x = SEED
    for b in s.encode("utf-8"):
        if b >= 128:
            b -= 256
        x = (x ^ b) & 0xFFFFFFFF
        x = (x * 16777619) & 0xFFFFFFFF
    return x


def utf8_chars(s):
    """按 UTF-8 字符边界切开（fastText 的 computeSubwords 就是这么跳的）。"""
    return list(s)


def ngrams_of(word, minn, maxn):
    """复刻 Dictionary::computeSubwords(BOW+word+EOW)。
    i 走字符起点, j 走字符终点, n 是**字符数**（不是字节数）。"""
    s = BOW + word + EOW
    out = []
    n = len(s)
    for i in range(n):
        for ln in range(minn, maxn + 1):
            j = i + ln
            if j > n:
                break
            out.append(s[i:j])
    return out


def load(path):
    m = fasttext.load_model(path)
    W = m.get_input_matrix()      # (nwords+bucket, dim)
    O = m.get_output_matrix()     # (nlabels, dim)
    nwords = len(m.words)
    args = m.f.getArgs()
    return m, W, O, nwords, args


def softmax(z):
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def probe(path, texts):
    m, W, O, nwords, args = load(path)
    print("nwords=%d  bucket=%d  dim=%d  minn=%d  maxn=%d  wordNgrams=%d"
          % (nwords, args.bucket, args.dim, args.minn, args.maxn, args.wordNgrams))
    print("input=%s output=%s" % (W.shape, O.shape))
    print("labels=%s" % [l.replace("__label__", "") for l in m.labels])
    print()

    def features_h(token, hyp):
        """hyp: 1=在词表则只用word; 2=word+subwords; 3=只用subwords"""
        wid = None
        try:
            wid = m.get_word_id(token)
        except Exception:
            wid = -1
        subs = [nwords + fnv(g) % args.bucket for g in ngrams_of(token, args.minn, args.maxn)]
        if hyp == 1:
            return [wid] if wid >= 0 else subs
        if hyp == 2:
            return ([wid] if wid >= 0 else []) + subs
        return subs

    def logits_for(text, hyp):
        toks = text.split()
        ids = []
        for t in toks:
            ids += features_h(t, hyp)
        if not ids:
            return None
        h = W[ids].mean(axis=0)
        return O.dot(h)

    print("%-30s %-8s %s" % ("输入", "假设", "与真模型最大概率差"))
    print("-" * 76)
    for t in texts:
        labs, probs = m.predict(t, k=-1)
        order = [l.replace("__label__", "") for l in labs]
        true_p = dict(zip(order, probs))
        row = []
        for hyp in (1, 2, 3):
            z = logits_for(t, hyp)
            if z is None:
                row.append((hyp, float("nan")))
                continue
            p = softmax(z)
            got = {l.replace("__label__", ""): float(p[i]) for i, l in enumerate(m.labels)}
            d = max(abs(true_p.get(k, 0) - got.get(k, 0)) for k in set(true_p) | set(got))
            row.append((hyp, d))
        best = min(row, key=lambda x: x[1])[0]
        print("%-30s %s   → 最匹配 H%d" % (
            t[:28], "  ".join("H%d:%.2e" % r for r in row), best))
    print()
    print("真模型输出示例:", m.predict(texts[0], k=-1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = sys.argv[1] if len(sys.argv) > 1 else "model_yy.bin"
    probe(path, [
        "赢麻了",
        "今天比赛赢麻了，太爽了",
        "又是魂游",
        "这游戏优化真好，我4080都掉帧",
        "😅",
        "啊对对对",
    ])
