# -*- coding: utf-8 -*-
"""用 argmax 一致率挑出正确的实现组合（比概率数值更稳，因为概率被 exp 编码过）。"""
import os
import sys
import csv
import random
import numpy as np
import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 2166136261


def fnv(s):
    x = SEED
    for b in s.encode("utf-8"):
        if b >= 128:
            b -= 256
        x = (x ^ b) & 0xFFFFFFFF
        x = (x * 16777619) & 0xFFFFFFFF
    return x


def ng(s, a, b):
    return [s[i:i + ln] for i in range(len(s)) for ln in range(a, b + 1) if i + ln <= len(s)]


def load_texts(n=600):
    """真实文本: 产品库标题 + COLDataset"""
    out = []
    import sqlite3, json
    db = r"C:\Users\sunsi\AppData\LocalLow\MirrorSorter\data.db"
    if os.path.exists(db):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            for (raw,) in c.execute("SELECT json FROM items LIMIT %d" % n).fetchall():
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                t = (d.get("title") or "").strip()
                if t:
                    out.append(t[:64])
            c.close()
        except Exception:
            pass
    p = os.path.join(HERE, "_data", "COLDataset", "COLDataset", "test.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                t = (row.get("TEXT") or "").strip()
                if t:
                    out.append(t[:64])
                if len(out) > n * 2:
                    break
    random.Random(1).shuffle(out)
    return out[:n]


def main():
    m = fasttext.load_model("ft_big.bin")
    A = m.f.getArgs()
    nw, bk = len(m.words), A.bucket
    W, O = m.get_input_matrix(), m.get_output_matrix()
    lab = [l.replace("__label__", "") for l in m.labels]
    texts = load_texts(600)
    print("评测样本 %d 条   nwords=%d bucket=%d minn=%d maxn=%d wng=%d"
          % (len(texts), nw, bk, A.minn, A.maxn, A.wordNgrams))

    def build(text, use_word, use_sub, wrap, use_eos, use_wng):
        toks = text.split()
        ids, hashes = [], []
        for t in toks:
            w = m.get_word_id(t)
            if use_word and w >= 0:
                ids.append(w)
            if use_sub:
                s = ("<" + t + ">") if wrap else t
                ids += [nw + fnv(g) % bk for g in ng(s, A.minn, A.maxn)]
            hashes.append(fnv(t) % bk)
        if use_wng and len(toks) > 1:
            for i in range(len(hashes)):
                h = hashes[i]
                for j in range(i + 1, min(len(hashes), i + A.wordNgrams)):
                    h = (h * 116049371 + hashes[j]) & 0xFFFFFFFF
                    ids.append(nw + h % bk)
        if use_eos:
            ids.append(0)      # </s>
        return ids

    truth = []
    for t in texts:
        labs, _ = m.predict(t, k=1)
        truth.append(labs[0].replace("__label__", ""))

    GRID = []
    for uw in (0, 1):
        for us in (0, 1):
            for wp in (0, 1):
                for eo in (0, 1):
                    for wg in (0, 1):
                        GRID.append((uw, us, wp, eo, wg))

    print()
    print("%-28s %10s %10s" % ("组合(word,sub,wrap,eos,wng)", "一致数", "一致率"))
    print("-" * 60)
    res = []
    for combo in GRID:
        hit = 0
        for t, gt in zip(texts, truth):
            ids = build(t, *combo)
            if not ids:
                hit += (gt == lab[0])
                continue
            h = W[ids].mean(axis=0)
            z = O.dot(h)
            if lab[int(np.argmax(z))] == gt:
                hit += 1
        res.append((hit, combo))
        print("%-28s %10d %9.1f%%" % (str(combo), hit, 100.0 * hit / len(texts)))
    res.sort(reverse=True)
    print()
    print("★ 最佳组合: %s  →  %.1f%%" % (res[0][1], 100.0 * res[0][0] / len(texts)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
