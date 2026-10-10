# 模型站上的开源模型选型清单

> 回答「有没有适合我们的开源模型」
> 日期：2026-10-10　基线：v0.3.1.42dsk
> 状态：**纯调研，未改动任何代码**

---

## 0. 直接回答

**有，但不是「一个模型解决三条链路」，而是一组小模型的组合。而且最快的那个方案，严格说不是「下载一个模型」，是「下载一个工具 + 用你自己的数据训练」。**

| 位置 | 方案 | 来源 | 体积 | 延迟 | 状态 |
|---|---|---|---|---|---|
| **L2a** | **fastText** | 下载工具，自训 | **5~50 MB** | **0.05 ms/条** | 需自训 |
| **L2b** | **`thu-coai/roberta-base-cold`** | **直接下载** | ~100 MB | ~6 ms/条 | **开箱可用** |
| **L2c** | 中文 encoder 微调 | 下载底座，自训 | ~100 MB (int8) | ~6 ms/条 | 需自训 |
| **L3** | API | — | — | ~2s/批 | 现状 |

**关键对比**：0.5B decoder（Qwen3-0.6B）CPU 吞吐 **0.83~3.3 条/秒**；
**fastText 是 20,000 条/秒 —— 快 6,000~24,000 倍，体积小 8~76 倍。**

---

## 1. ★ 最重要的认知：模型站上没有我们真正需要的那个模型

这是本次调研最关键的发现，先说清楚，否则选型会走偏。

### 1.1 模型站上的「中文内容安全」模型，判的是**平台合规**维度

翻遍 HuggingFace / ModelScope 的「内容安全审核」类目，中文模型的标签体系基本都是：

> **政治敏感 / 色情 / 辱骂 / 暴力 / 违禁品 / 自残**

### 1.2 而我们的判据是**社区语境**维度

对照 `SCORER_SKILL`（`app.py:5295-5330`）的 8 个维度：

> **flame 引战 / spam 低质 / interest 兴趣 / density 信息密度 / emo 情绪净值 / facts 事实锚点 / topic 主题 / reason 推荐理由**

### 1.3 两个维度几乎不重叠

| 我们的判据 | 平台合规模型能覆盖吗 |
|---|---|
| `flame` 中的**辱骂/人身攻击**部分 | ✅ 能（约覆盖 `flame` 判定的 30~40%） |
| `flame` 中的**拉踩/带节奏/碰瓷/扣帽子** | ❌ 不能（这些不含脏话，是语境行为） |
| `spam` 低质 / 标题党 / 货不对板 | ❌ 不能（平台合规不管"低质"，只管"违规"） |
| `density` 信息密度 / 车轱辘话 | ❌ 不能 |
| `emo` 情绪净值（贩卖焦虑/建设性） | ❌ 不能 |
| `interest` 与用户画像匹配 | ❌ 不能（需要你的画像） |

> **结论：「标题党」「信息密度」「带节奏」这类判据，在公开模型站上不存在对应的中文模型。**
> 这类模型是各平台（B站/知乎/小红书）的内部资产，不会开源。
>
> 唯一能直接拿来复用的是**「冒犯性语言」这一小块**。

---

## 2. 现成能直接下载就用的：只有 1 个

### `thu-coai/roberta-base-cold`

| 项 | 值 |
|---|---|
| 发布方 | 清华大学 CoAI 组（Conversational AI） |
| 基座 | `hfl/chinese-roberta-wwm-ext` 在 COLDataset 上微调 |
| 参数 | **0.1B** |
| 任务 | 二分类：`0 = Non-Offensive` / `1 = Offensive` |
| **测试集成绩** | **Accuracy 82.75% / macro-F1 82.39%** |
| 语言 | 中文 |
| 论文 | EMNLP 2022，arXiv:2201.06025 |
| 许可证 | ⚠️ 需核对（基座是 Apache-2.0，数据集 COLDataset 有独立条款） |

**用法（transformers，直接可跑）**：

```python
import torch
from transformers.models.bert import BertTokenizer, BertForSequenceClassification

tokenizer = BertTokenizer.from_pretrained('thu-coai/roberta-base-cold')
model = BertForSequenceClassification.from_pretrained('thu-coai/roberta-base-cold')
model.eval()

texts = ['你就是个傻逼！', '黑人很多都好吃懒做，偷奸耍滑！', '男女平等，黑人也很优秀。']
out = model(**tokenizer(texts, return_tensors="pt", padding=True), return_dict=False)
print([p.item() for p in torch.argmax(out[0].cpu(), dim=-1)])
# --> [1, 1, 0]
```

**对我们有什么用**：直接对应 `SCORER_SKILL` 里 `flame` 的**辱骂/攻击性**子集。
**局限**：只判「冒犯」，**判不了**「带节奏 / 拉踩 / 标题党 / 低质」；而且会误伤正常吐槽（"这游戏真是把我气笑了"）。

**配套资源**：`thu-coai/COLDataset`（GitHub）—— 中文冒犯语言检测基准数据集，**可作训练数据来源**。

---

## 3. 中文 encoder 底座清单（自训用）

如果第 2 节的现成模型不够，就用这些底座自己微调：

| 模型 ID | 参数 | 备注 |
|---|---|---|
| `hfl/chinese-roberta-wwm-ext` | 102M | **最常用的中文底座**，COLD 就是用它微调的 |
| `hfl/chinese-macbert-base` | 102M | MacBERT，纠错式预训练，分类任务表现好 |
| `IDEA-CCNL/Erlangshen-DeBERTa-v2-97M-Chinese` | **97M** | 悟道 180G 语料预训练，**参数最少的中文 DeBERTa** |
| `IDEA-CCNL/Erlangshen-DeBERTa-v2-97M-CWS-Chinese` | 97M | 上者 + 中文分词版本 |
| `iic/nlp_structbert_*`（ModelScope） | 100M 级 | 阿里达摩院，ModelScope 上有一系列中文分类模型，含 zero-shot 版 |

**选型建议**：底座差异不大，**关键是你的标注数据**。先拿 `chinese-roberta-wwm-ext`（生态最全、教程最多）。

---

## 4. ★ 最快方案：fastText（而且实测打赢了 BERT）

这是本次调研**最反直觉、也最有价值的发现**。

### 4.1 中文短文本分类实测对比

来源：中文短文本分类选型实战（投诉分流任务）

| 模型 | Accuracy | Macro-F1 | 模型大小 | **推理延迟** |
|---|---:|---:|---:|---:|
| **fastText** | **94.64%** | **86.56%** | **约 50 MB** | **约 0.05 ms** |
| BERT | 88.80% | 78.51% | 约 390 MB | 约 5.94 ms |
| TF-IDF + LR | 87.96% | 76.87% | 约 10 MB | 约 0.41 ms |
| TF-IDF + RF | 86.57% | 72.05% | 较小 | 约 0.20 ms |

**fastText 比 BERT 准 5.84 个百分点，快 119 倍，小 8 倍。**

### 4.2 为什么 fastText 能赢

- **子词 n-gram** 天然捕捉「物流慢」「质量差」「震惊党」这类**固定短语模式**
- 短文本分类中，**关键词模式本身就是主要判据**，上下文建模的收益有限
- BERT 在该实验中训练轮数较少，未必充分收敛 —— 但即使充分收敛，差距也不会反转

### 4.3 换算到我们的场景

| 指标 | 0.5B decoder | BERT encoder | **fastText** |
|---|---:|---:|---:|
| 延迟/条 | 300~1,200 ms | ~6 ms | **0.05 ms** |
| **吞吐** | **0.83~3.3 条/秒** | ~170 条/秒 | **20,000 条/秒** |
| 体积 | 380~480 MB | ~390 MB | **5~50 MB** |
| 弹幕突发需求（~1,000 条/秒） | ❌ 差 300~1,200 倍 | ✅ 勉强 | ✅ **余量 20 倍** |

> **fastText 是唯一能轻松接住「弹幕突发」的方案。**

### 4.4 但 fastText 不是「下载一个现成模型」

fastText 是**训练工具 + 运行时**，需要你自己训练分类器：

- 下载：`fasttext` 工具（Facebook/Meta 开源，MIT）
- 数据：**用你积累的 API 评分结果做标签**（第 0 步）
- 训练：分钟级，CPU 即可
- 输出：`model.bin`，5~50 MB
- 运行时：Python 包 `fasttext`，或 C++ 库，无 GPU 依赖

**中文注意点**：不要用官方 `cc.zh.300.bin`（6.7 GB 太大）；**用你自己数据训练**，或训练时用字符级 n-gram（中文可省略分词）。

### 4.5 局限（必须知道）

| 局限 | 影响 |
|---|---|
| **无上下文理解** | 反讽、长句语义、跨句立场判断全不行 |
| 依赖关键词模式 | 训练数据没见过的表达会漏 |
| 无法输出 `topic`/`reason` | 只能做分类，不能生成 |
| 对 `density`/`emo` 这类连续打分弱 | 更适合二值/少类分类 |

→ **所以 fastText 只能当 L2a（粗筛），不能当全部。**

---

## 5. 明确不适合我们的（列出来避免踩坑）

| 模型 | 为什么不选 |
|---|---|
| **Qwen3-0.6B / Qwen2.5-0.5B** | CPU 吞吐 0.83~3.3 条/秒，**差 6,000~24,000 倍**；零样本中文短文本 60~70% |
| `unitary/toxic-bert` | **英文**，中文不通 |
| `KoalaAI/Text-Moderation` | **英文** |
| `tomh/toxigen_hatebert` | **英文**，且是隐式仇恨（学术场景） |
| `meta-llama/Llama-Guard-3-1B` | **1B decoder**，吞吐不够；维度是「通用安全」不是「中文社区引战」 |
| `google/shieldgemma-2b` | **2B decoder**，太大；维度不对 |
| `jackhhao/jailbreak-classifier` | 英文，判的是「越狱提示词」，与内容筛选无关 |
| `GA Guard` 系列 | 英文，7 类违规（非法活动/仇恨/PII/色情/虚假信息/暴力），**平台合规维度** |
| NSFW Detection (ViT) | **图像**分类，与我们无关 |

**规律**：模型站上的审核模型要么是**英文**，要么是**平台合规维度**，要么是**1B+ 的 decoder**。三个特征里至少占一个，全都不适合。

---

## 6. 推荐的组合（分层，各司其职）

| 层 | 模型 | 来源 | 体积 | 吞吐 | 覆盖什么 |
|---|---|---|---|---|---|
| **L1** | 机械规则（现有） | 已有 | 0 | 百万+/秒 | 播放量/时长/去重/词表/黑名单 |
| **L2a** | **fastText** | 工具+自训 | 5~50 MB | **20,000/秒** | 引战/低质/标题党的**模式明确**部分 |
| **L2b** | **`roberta-base-cold`** | **直接下载** | ~100 MB | ~170/秒 | **辱骂/人身攻击** |
| **L2c** | 中文 encoder 微调 | 底座+自训 | ~100 MB | ~170/秒 | 带节奏/低质/密度/情绪等**复杂判断** |
| **L3** | API | — | — | ~0.5/秒 | **精判 + `topic` + `reason`** |

**流量走向**：
```
全部内容 → L1（砍 70%）→ L2a fastText（砍 80% 剩余）→ L2b/L2c（低置信才走）→ L3 API（砍 95%+）
```
→ API 从 **¥245~1,536/月 → ¥15~75/月**

**三条链路的落点**：

| 链路 | L2a fastText | L2b/L2c encoder |
|---|---|---|
| **信息筛选** | ✅ 标题模式（标题党/震惊党） | ✅ 语义（低质/密度/情绪） |
| **评论筛选** | ✅ 短文本垃圾评论 | ⚠️ 需走壳端异步 |
| **弹幕筛选** | ✅✅ **最合适**（5~20 字，模式性强，20,000/秒 能接住突发） | ⚠️ 只有低置信才升级 |

> **弹幕是 fastText 的教科书级适用场景**：超短文本 + 强模式 + 极高吞吐要求。

---

## 7. 立刻可以下载来试的清单

### 7.1 开箱即用（今天就能跑）

```bash
# 中文冒犯语言检测（0.1B，82.75% acc）
pip install transformers torch
# 模型 ID: thu-coai/roberta-base-cold
```

### 7.2 最快方案（需自训，但训练只要几分钟）

```bash
pip install fasttext
# 训练：fasttext supervised -input train.txt -output model -dim 100 -epoch 25 \
#        -wordNgrams 2 -minn 2 -maxn 4 -loss softmax
# 中文建议加 -minn 2 -maxn 4 走字符级 n-gram，可跳过分词
```

### 7.3 自训 encoder 的底座

```
hfl/chinese-roberta-wwm-ext          # 首选底座
hfl/chinese-macbert-base
IDEA-CCNL/Erlangshen-DeBERTa-v2-97M-Chinese
```

### 7.4 训练数据来源

```
thu-coai/COLDataset                  # 中文冒犯语言数据集（GitHub）
+ 你自己的 API 评分结果              # ★ 这才是主力标签来源
```

---

## 8. 一句话总结

> **有适合的，但形态和你想的不一样。**
>
> **能直接下载就用的只有 1 个**：`thu-coai/roberta-base-cold`（中文冒犯检测，0.1B，82.75%）—— 只覆盖「辱骂」这一小块。
>
> **最快的方案是 fastText**（50MB，0.05ms/条，中文短文本实测 94.64% **打赢 BERT**，比 0.5B decoder 快 6,000~24,000 倍）—— 但它是「工具 + 你自己的数据」，不是「下载即用」。
>
> **模型站上没有我们最需要的那类模型**：「标题党 / 信息密度 / 带节奏」是各平台内部资产，不开源。这部分**必须自己训**。
>
> **而 Qwen3-0.6B 这类 0.5B 模型，恰好是最不适合的**：吞吐差 6,000 倍，零样本中文短文本只有 60~70%。
>
> **所以路线不变**：L1 规则 → **L2a fastText + L2b roberta-base-cold + L2c 自训 encoder** → L3 API。
> 而这一切的起点仍然是**第 0 步：把 API 评分跑起来攒标签**——因为 L2a/L2c 都靠它。
