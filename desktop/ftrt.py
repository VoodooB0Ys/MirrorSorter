# -*- coding: utf-8 -*-
"""纯 stdlib 的 fastText 监督模型推理运行时。

存在的理由：
    本产品打包用的是 Python 3.14，而 fasttext 官方只发源码包（要 MSVC），
    社区预编译 wheel 最高只到 cp311 —— 在 exe 里 `import fasttext` 必然失败。
    所以训练在 Python 3.11 里做，运行期用这个纯 Python 实现。

实现要点（每一条都是实测校准出来的，改动前请先跑 _vis/fasttext_demo/pick_impl.py）：
    1. 分词：按空白切分（中文通常整句一个 token）。
    2. 特征：
         - token 命中词表 → 加该词的输入矩阵行；
         - **无论是否命中**，都再追加 <token> 的字符级 n-gram 子词（minn..maxn）；
         - **末尾追加一个 </s> 行**（fastText 的 getLine 会带上它）。
    3. 池化：特征行求平均。
    4. 打分：output_matrix · hidden，再过 sigmoid（loss='softmax' 是逐标签 one-vs-all）。
    5. 哈希：FNV-1a，种子 2166136261，且字节要先做**有符号**扩展
       （源码是 `h ^= uint32_t(int8_t(c))`，>=128 的字节要减 256）。

在 600 条真实 B站标题/评论上，本实现与真 fastText 的 argmax 一致率 = 100%。
"""
import os
import re
import sys
import math
import array
import struct
from operator import add

MAGIC = b"MSFTFT01"
_HDR = struct.Struct("<8I")
_SEED = 2166136261
_WS = re.compile(r"\s+")
_exp = math.exp


def _fnv(s):
    """fastText 的字符串哈希。★ 字节必须做有符号扩展。"""
    x = _SEED
    for b in s.encode("utf-8"):
        if b >= 128:
            b -= 256
        x = (x ^ b) & 0xFFFFFFFF
        x = (x * 16777619) & 0xFFFFFFFF
    return x


class FastTextLite(object):
    """只读、线程内独立使用的轻量推理器。加载一次约 60MB 内存。"""

    __slots__ = ("labels", "words", "w2i", "dim", "bucket", "nwords", "nlabels",
                 "minn", "maxn", "eos_id", "inp", "out", "_tok_cache", "_maxlen",
                 "_cache_cap", "_out_rows", "temp")

    def __init__(self, path, maxlen=64, cache_cap=20000, temp=3.0):
        with open(path, "rb") as f:
            if f.read(8) != MAGIC:
                raise ValueError("不是本产品的模型格式(需要 ftrt 导出的 .bin)")
            (self.dim, self.bucket, self.nwords, self.nlabels,
             self.minn, self.maxn, self.eos_id, _) = _HDR.unpack(f.read(_HDR.size))
            self.labels = [self._rdstr(f) for _ in range(self.nlabels)]
            self.words = [self._rdstr(f) for _ in range(self.nwords)]
            self.inp = array.array("f")
            self.inp.fromfile(f, (self.nwords + self.bucket) * self.dim)
            self.out = array.array("f")
            self.out.fromfile(f, self.nlabels * self.dim)
        if sys.byteorder != "little":
            self.inp.byteswap()
            self.out.byteswap()
        self.w2i = {w: i for i, w in enumerate(self.words)}
        self._tok_cache = {}
        self._maxlen = maxlen
        self._cache_cap = cache_cap
        self.temp = temp
        self._out_rows = [list(self.out[i * self.dim:(i + 1) * self.dim])
                          for i in range(self.nlabels)]

    @staticmethod
    def _rdstr(f):
        n = struct.unpack("<I", f.read(4))[0]
        return f.read(n).decode("utf-8", "replace")

    # ---------------------------------------------------------------- 特征
    def _tok_ids(self, tok):
        """单个 token 的输入矩阵行号列表。结果带缓存 —— 弹幕/评论里重复极高。"""
        got = self._tok_cache.get(tok)
        if got is not None:
            return got
        nw, bk, minn, maxn = self.nwords, self.bucket, self.minn, self.maxn
        ids = []
        wid = self.w2i.get(tok)
        if wid is not None:
            ids.append(wid)
        if bk and maxn >= minn > 0:
            s = "<" + tok + ">"
            n = len(s)
            for i in range(n):
                for ln in range(minn, maxn + 1):
                    j = i + ln
                    if j > n:
                        break
                    ids.append(nw + _fnv(s[i:j]) % bk)
        if len(self._tok_cache) < self._cache_cap:
            self._tok_cache[tok] = ids
        return ids

    def _hidden(self, text):
        dim = self.dim
        ids = []
        ext = ids.extend
        for tok in _WS.split(text.strip()):
            if tok:
                ext(self._tok_ids(tok))
        ids.append(self.eos_id)
        inp = self.inp
        acc = [0.0] * dim
        for i in ids:
            o = i * dim
            acc = list(map(add, acc, inp[o:o + dim]))
        n = float(len(ids))
        return [x / n for x in acc]

    # ---------------------------------------------------------------- 打分
    def logits(self, text):
        """原始 logits（调试用；排序即 argmax，与真 fastText 100% 一致）。"""
        h = self._hidden((text or "")[:self._maxlen])
        return [sum(a * b for a, b in zip(row, h)) for row in self._out_rows]

    def scores(self, text):
        """返回每个标签的概率（与 labels 同序）。

        ★ 用带温度的 softmax，而不是逐标签 sigmoid：
          这个模型训练后 logit 动辄 ±50~100，直接 sigmoid 会全部饱和到 0/1，
          设置里的"判定阈值"就完全失效了。温度缩放不改变 argmax
          （所以与真 fastText 的一致率仍是 100%），只把分数重新拉开。
          默认 temp=3.0，是在 B站风格评测集上标定出来的。
        """
        z = self.logits(text)
        T = self.temp or 1.0
        mx = max(z)
        e = [_exp((x - mx) / T) for x in z]
        s = sum(e)
        return [x / s for x in e]

    def predict(self, text, k=1):
        sc = self.scores(text)
        order = sorted(range(len(sc)), key=lambda i: -sc[i])
        if k and k > 0:
            order = order[:k]
        return [self.labels[i] for i in order], [sc[i] for i in order]

    def predict_many(self, texts, k=1):
        return [self.predict(t, k) for t in texts]

    def info(self):
        return {"labels": list(self.labels), "dim": self.dim, "bucket": self.bucket,
                "nwords": self.nwords, "minn": self.minn, "maxn": self.maxn,
                "eos_id": self.eos_id, "temp": self.temp,
                "rows": self.nwords + self.bucket}




# ---------------------------------------------------------------- 自测
def _selftest():
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "ft_model.bin")
    if not os.path.exists(p):
        print("缺少 %s" % p)
        return 1
    import time
    t0 = time.time()
    m = FastTextLite(p)
    print("加载 %.3f 秒  %s" % (time.time() - t0, m.info()))
    cases = ["赢麻了", "啊对对对", "又是魂游", "这游戏优化真好，我4080都掉帧",
             "今天比赛赢麻了，太爽了", "你就是个废物，滚出去", "加微信领取免费课程",
             "惊了！这个显卡居然只要999", "这游戏画面真不错", "这优化卡得不行，根本没法玩"]
    for t in cases:
        labs, sc = m.predict(t, 3)
        print("  %-26s %s" % (t[:24], "  ".join("%s=%.3f" % (l, s) for l, s in zip(labs, sc))))
    txt = cases * 200
    t0 = time.perf_counter()
    m.predict_many(txt)
    dt = time.perf_counter() - t0
    print("吞吐 %.0f 条/秒  (%.3f ms/条)" % (len(txt) / dt, dt * 1000 / len(txt)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rc = _selftest()
    sys.stdout.flush()
    os._exit(rc)
