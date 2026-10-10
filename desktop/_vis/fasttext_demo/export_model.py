# -*- coding: utf-8 -*-
"""把 fastText 模型导出成自研紧凑格式, 供纯 stdlib 的运行期读取。

为什么不用 fastText 官方的 .bin？
    1. 打包用的是 Python 3.14, 而 fasttext 官方只发源码包, 社区预编译 wheel 最高只到 cp311
       —— 在 exe 里 `import fasttext` 必然失败。
    2. 官方 .bin 里塞了词表/剪枝表/量化元数据等运行期用不到的东西, 解析容易出错。

格式（全部小端）：
    magic     8 字节  b"MSFTFT01"
    uint32  × 8      dim, bucket, nwords, nlabels, minn, maxn, eos_id, reserved
    labels    nlabels × (uint32 字节数 + UTF-8)
    words     nwords  × (uint32 字节数 + UTF-8)
    input     (nwords+bucket) × dim  float32
    output    nlabels × dim          float32

运行期判定公式（已与真 fastText 在 600 条真实文本上做到 100% argmax 一致）：
    ids = []
    for token in text.split():
        if token in vocab:  ids.append(word_id)          # ★ 词表命中也要加
        ids += [nwords + fnv("<"+token+">") ngram % bucket ...]   # ★ 子词始终加
    ids.append(eos_id)                                    # ★ 末尾追加 </s>
    hidden = mean(rows[ids])
    logits = output_matrix @ hidden
"""
import os
import sys
import json
import struct
import array

import numpy as np
import fasttext

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "ft_big.bin")
OUT = os.path.join(HERE, "ft_model.bin")
META = os.path.join(HERE, "ft_model.meta.json")
MAGIC = b"MSFTFT01"


def main():
    if not os.path.exists(SRC):
        print("缺少模型: %s" % SRC)
        return 1
    m = fasttext.load_model(SRC)
    A = m.f.getArgs()
    W = np.asarray(m.get_input_matrix(), dtype="<f4")
    O = np.asarray(m.get_output_matrix(), dtype="<f4")
    words = list(m.words)
    labels = [l.replace("__label__", "") for l in m.labels]
    nw, dim = len(words), A.dim
    eos_id = 0
    for i, w in enumerate(words):
        if w == "</s>":
            eos_id = i
            break

    print("源模型   : %s  (%.2f MB)" % (SRC, os.path.getsize(SRC) / 1048576.0))
    print("dim=%d bucket=%d nwords=%d nlabels=%d minn=%d maxn=%d eos_id=%d"
          % (dim, A.bucket, nw, len(labels), A.minn, A.maxn, eos_id))
    print("input=%s output=%s" % (W.shape, O.shape))
    assert W.shape == (nw + A.bucket, dim), "输入矩阵尺寸不符合预期"

    with open(OUT, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<8I", dim, A.bucket, nw, len(labels),
                            A.minn, A.maxn, eos_id, 0))
        for s in labels:
            b = s.encode("utf-8")
            f.write(struct.pack("<I", len(b)))
            f.write(b)
        for s in words:
            b = s.encode("utf-8")
            f.write(struct.pack("<I", len(b)))
            f.write(b)
        W.tofile(f)
        O.tofile(f)

    size = os.path.getsize(OUT)
    print("导出     : %s  (%.2f MB)" % (OUT, size / 1048576.0))

    meta = {
        "labels": labels,
        "dim": dim, "bucket": A.bucket, "nwords": nw,
        "minn": A.minn, "maxn": A.maxn, "eos_id": eos_id,
        "epoch": A.epoch, "lr": A.lr, "minCount": A.minCount,
        "maxlen": 64,
        "note": "自研紧凑格式, 只能由 ftrt.py 读取, 不是 fastText 官方 .bin",
        "source": "ft_big.bin",
        "source_size_mb": round(os.path.getsize(SRC) / 1048576.0, 2),
        "export_size_mb": round(size / 1048576.0, 2),
    }
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("元数据   : %s" % META)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
