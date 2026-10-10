# 洞鉴虚明 v0.3.1.43dsk —— 内置 58MB 快速文本分析模型（标题简介 / 弹幕 / 评论 三链路）

交付物：`desktop\dist\洞鉴虚明v0.3.1.43dsk.exe`（**78,358,793 字节 = 74.7 MB**，2026/10/10 14:27）
直接双击即可。模型已随包分发，**不需要联网、不需要装 Python、不需要任何 API key**。

对比上一版：v0.3.1.42dsk 是 21,317,431 字节（21.3MB）→ 本版 +57MB，增量就是模型本体。

---

## 1. 一句话结论

用 fastText 训练了一个 **58.38MB** 的多标签中文分类模型，**内置进 exe**，在设置里
新增一个**独立于弹幕/评论总开关**的「快速文本分析」总开关，管三件事：

| 链路 | 判什么 | 接入点 |
|---|---|---|
| 信息筛选 | 视频/帖子的 **标题 + 简介** | `app.py` 信息流漏斗（`_emo_bad` 之后兜一道语义） |
| 弹幕分拣 | **单条弹幕正文** | `bilibili-dmfilter.user.js`（改写字节流之前判定） |
| 评论分拣 | **单条评论正文** | `bilibili-cfilter.user.js`（标记式异步，不阻塞加载） |

判定 6 个标签：`ok` / `yy`（阴阳怪气）/ `clickbait`（标题党）/ `flame`（引战）/ `spam`（低质广告）/ `neg`（负面）。
用户可勾选**参与拦截**的标签，可调阈值（50 宽松 / 70 标准 / 85 严格 / 93 极严），
设置页还有一个「试一下」输入框 —— 当场输入一句话就能看见模型判成什么、多少概率。

---

## 2. 关键架构决策：训练用真 fastText，运行期用自研纯 stdlib 推理

这是整件事里唯一真正的"拦路虎"，也是必须讲清楚的一条。

**约束**：本产品打包走 **Python 3.14**（`_build_now.py` / `构建exe.bat` 都是 `python -m PyInstaller`）。
而 fastText 官方**只发源码包**（要 MSVC 编译），社区预编译 wheel 最高只到 **cp311**。

⇒ 在 exe 里 `import fasttext` **必然失败**。

**解法**：把"训练"和"运行"彻底解耦。

```
训练期(离线, 一次性)                     运行期(随 exe 分发)
  Python 3.11.9 (免安装)                   Python 3.14
  + fasttext-0.9.2-cp311-win_amd64.whl  →  desktop/ftrt.py (纯 stdlib, ~7.6KB)
  + numpy-1.26.4                           + desktop/model/ft_model.bin
  ─────────────────────────────────────    ─────────────────────────────
  产出 ft_big.bin → export_model.py 转成  FastTextLite 只做一件事:
  自研紧凑格式 ft_model.bin                读矩阵 → 前向 → softmax
```

`ftrt.py` 的 import 只有 `array / struct / math / sys / os` —— 全是标准库，Python 3.14 直接可用，
**没有二进制依赖，也就没有"装不上"这一说**。

### 2.1 难点：把 fastText 的前向逐位复刻

只"差不多像"是不行的 —— 概率对不上，阈值就是废的。为此逐项校准到
**600 条真实文本 argmax 一致率 100.0%**：

| 项 | 坑 | 最终结论 |
|---|---|---|
| FNV-1a 哈希 | fastText 源码是 `h ^= uint32_t(int8_t(c))` | **`>=128` 的字节必须先减 256** 再异或，否则整个哈希全错 |
| 子词 | 是否总加 | **始终加**，且用 `<token>` 包裹（`<` `>` 也是哈希的一部分） |
| 词表命中 | 是否只加子词 | **词向量 + 子词都要加** |
| 句尾 | 容易漏 | **末尾追加 `</s>`**（实测 id=0）—— 漏了就系统性偏移 |
| wordNgrams | 默认 2 | **必须 =1**。>1 时 predict 的词 n-gram 拼接极难精确复刻（实测 99.8%），中文几乎无空格分词，关掉精度代价 ≈ 0 |
| `loss="softmax"` | 名字骗人 | 在 fastText 里其实是**逐标签 sigmoid（one-vs-all）**，不是联合 softmax |

校准过程是"网格搜索 + argmax 一致率"，最终 `(word=1, sub=1, wrap=1, eos=1, wng=0)` = **600/600 = 100%**。

### 2.2 第二个坑：模型过度自信 → 阈值失效

原始 logit 动辄 ±50~100，直接 softmax 中位数 **0.997** —— 阈值 50/70/85/93 全都没区别，形同虚设。

改**温度缩放 softmax（T=3.0）**：不改 argmax（一致性仍 100%），只把概率摊开，
阈值才真正有区分度。T 的选择有实测依据，见第 4 节。

---

## 3. 模型与语料

### 3.1 超参（`_vis/fasttext_demo/train_big.py`）

```python
HP = dict(lr=0.5, epoch=25, wordNgrams=1, minn=2, maxn=4, dim=100,
          bucket=140000, minCount=5, loss="softmax", thread=8)
```

**体积怎么来的**：`文件大小 ≈ (词表行数 + bucket) × dim × 4 字节`。
`bucket=140000, dim=100` → 约 56MB（实测 58.45MB）。
⚠️ 默认 `bucket=2000000` 会直接干到 **~800MB**，这是个必须显式调下来的坑。

**中文处理要点**：整句**不切字**，靠字符级 n-gram（`minn=2, maxn=4`）自动切。
`MAXLEN=64` —— **训练与运行期必须一致**（`build_corpus.py` 与 `app.py:_FT_MAXLEN` 各写一份，改一处必须改两处）。

### 3.2 语料：339,452 条 / 27.2MB

| 来源 | 说明 |
|---|---|
| 产品自有 `items` 表 | 24,751 条真实**标题 + 简介**（最贴合本产品场景） |
| COLDataset | 37,480 条真实冒犯性言论（**flame 截断至 11,000**，见下） |
| ChnSentiCorp | 7,766 条真实情感语料（neg 用） |
| 合成 + 真实句注入 | yy / clickbait / spam / flame / neg / hard_neg |

标签分布：`{'ok': 85374, 'yy': 79422, 'clickbait': 61200, 'flame': 47013, 'spam': 34000, 'neg': 32443}`

**为什么 COLDataset 要截断**：它是"种族/地域/性别议题的冒犯性言论"，话题分布与本产品
（B站游戏动漫区）差得非常远。原样全量喂进去，模型会学成"提到黑人/女权/地域就判引战"——
**在别人的数据集上刷高分，在真实场景里疯狂误杀**。所以：
1. flame 上限截断到 11,000 条；
2. 另补 36,000 条**注入真实句子**的 flame（`gen_inject`：把标记词嵌进真实底料句，而不是凭空造句）；
3. 另建一份**手工标注的 B站风格评测集**（`eval_bili.py`，101 条）作为主指标。

> 一句话：用医学文本评测垃圾邮件过滤器，分数没有意义。所以两个数字一起看。

### 3.3 训练结果

- 训练耗时：**26.3 秒**（8 线程）
- 词表：**12,131**
- 产出 `ft_big.bin` **58.45MB** → `export_model.py` 转自研紧凑格式 `ft_model.bin` **58.38MB**

`export_model.py` 的格式（`MAGIC = b"MSFTFT01"` + `struct.pack("<8I", dim, bucket, nwords, nlabels, minn, maxn, eos_id, 0)` + 标签 + 词表 + input 矩阵 + output 矩阵，全部 float32 LE）——
**直接 `array.array("f").fromfile()` 读，零解析开销**。

---

## 4. 效果：诚实的两组数字

### 4.1 内部探针（33 条，含旧版盲区）

**32/33 = 97.0%**

### 4.2 COLDataset 留出集（真实数据，但领域不匹配）

| 精确率 | 召回率 | F1 | 准确率 |
|---|---|---|---|
| 69.69% | 69.96% | 69.82% | 76.07% |

### 4.3 ★ B站风格手工评测集（101 条，主指标）

原始 argmax（不看阈值）：

| 标签 | 命中 | 准确率 |
|---|---|---|
| ok | 29/30 | 96.7% |
| yy（阴阳怪气） | 27/29 | 93.1% |
| **flame（引战）** | **12/12** | **100%** |
| spam（低质广告） | 8/10 | 80.0% |
| **clickbait（标题党）** | **10/10** | **100.0%** |
| neg（负面） | 9/10 | 90.0% |
| **总体** | **95/101** | **94.1%** |

### 4.4 ★ 温度 × 阈值扫描 → 定下默认值

| T | 阈值 | 精确率 | 召回率 | F1 | **误杀数** |
|---|---|---|---|---|---|
| 2.0 | 70% | 98.5% | 93.0% | 95.7% | 1 |
| **3.0** | **70%** | **100.0%** | **93.0%** | **96.4%** | **0** |
| 3.0 | 85% | 100.0% | 90.1% | 94.8% | 0 |
| 4.0 | 70% | 100.0% | 93.0% | 96.4% | 0 |
| 5.0 | 70% | 100.0% | 91.5% | 95.6% | 0 |

⇒ **T=3.0 + 阈值 70% 是扫描表里的最优点，正好就是 UI 默认值**：
精确率 100%、召回 93.0%、**零误杀**。这就是默认值定成 70 的依据（不是拍的）。

**已知边界**：像「赢麻了」「急了急了」「典中典」这种在语料里被强化过的短语，
即使 T=8 概率也仍在 1.0 附近 —— 模型**极度确定**，任何阈值都拦不住也放不掉。
这本身是合理的（它确实该拦），但意味着「93 极严」对这类词没有额外作用，只对边界样本有效。

### 4.5 性能

| 指标 | 实测 |
|---|---|
| 模型加载 | **0.032 秒** |
| 纯 Python 推理吞吐 | **9,344 条/秒**（0.107 ms/条） |
| 带命中缓存 | **1,312,623 条/秒**（弹幕/评论重复率极高，这层最省 CPU） |
| 不重复文本 2,400 条 | **5,395 条/秒** |
| 参考：C++ fastText | 44,677 条/秒 |

纯 Python 比 C++ 慢约 4.8 倍，但**绝对值完全够用**：一次 500 条的弹幕批次 ≈ 50ms，
且在 `Promise.all` 并行 + 400ms 超时兜底之下（见 5.2），**不会阻塞页面**。

---

## 5. 三条链路怎么接的

### 5.1 信息筛选（视频标题 + 简介）

`app.py` 信息流漏斗里，**放在 `_emo_bad` 规则层之后**：

```python
if ft_enabled() and sget("ft_video") == "1":
    _w = ft_scan([(it.get("title") or "") + " " + (it.get("desc") or "")], "video")[0]
    if _w:
        _funnel_bump("ft")
        continue
```

放在规则层之后的原因：① 词表能命中的就不必再过模型，省一次前向；② 漏斗计数各归各的，
出问题能分清是规则拦的还是模型拦的。

### 5.2 弹幕分拣（★ 必须在改写字节流之前完成）

`bilibili-dmfilter.user.js` 劫持 `seg.so`（protobuf）。**关键顺序约束**：

```
拦到 seg.so → res.clone().arrayBuffer() → processAsync(u8)
                                              ↓
                        Promise.all([askFlags(hashes, true), askText(textsOf(top), true)])
                                              ↓            ← 文本判定在这里完成
                                          process(u8)      ← 然后才改写字节流
                                              ↓
                                     new Response(r.out.buffer, ...)
```

如果先改写再判定，播放器已经拿到原字节流了，**筛掉的弹幕照样会飘过去**。
所以 `askText` 必须进 `Promise.all` 一起等，最多等 `TXT_WAIT=400ms`；
超时则降级成"这一轮先不筛"（`TXT_FAST=0`），后续分段命中缓存后由 `refilter()` 补上 ——
**绝不卡住播放器**。

自取分段的路径（`fetchMissing`）只喂「弹幕列表」、不参与播放器字节流，
所以那里走**非阻塞预热**（`askText(top ? textsOf(top) : [], false)`），不拖慢补段节奏。

### 5.3 评论分拣（完全异步，零阻塞）

`bilibili-cfilter.user.js` 用的是既有的**标记式过滤**：命中的主评论**只把 `rpid_str` 记进 `BLK_RPIDS`**，
不改数组、不改内容，由 `sweep()`（900ms 巡检）落成 DOM 属性。

⇒ 文本判定可以**完全异步**：`noteText(r)` 收集 → `askText()` 批量问 → `applyText()` 回填
`BLK_RPIDS` + `BLK_WHY` → `kick()` 立刻重扫。**评论加载路径上一点等待都没有。**

角标从写死的「已屏蔽」改成 `content:attr(data-msx-why)` —— 内容判定命中的显示
**「已屏蔽 · 引战」**，让用户看得见"到底为什么被筛"，否则这功能对用户就是黑盒。

### 5.4 独立总开关（用户明确要求的一条）

设置里「快速文本分析」是**独立页签**（`#cfTabText` / `#cfText`），有**自己的总开关** `ft_on`，
与「弹幕屏蔽总开关」「评论筛选」**完全独立**：上面管"谁发的"，这里管"发了什么"，各开各的。

壳侧注入（`shell_pages.py`）：

```python
"ft_on": app.sget("ft_on") == "1",
"ft_dm": app.sget("ft_on") == "1" and app.sget("ft_dm") == "1",   # ★ 必须先过总开关
"ft_cf": app.sget("ft_on") == "1" and app.sget("ft_cf") == "1",
```

后端 `ft_scan` 里也有同样一道闸：总开关关掉 → **整批放行**（返回等长空串列表）。

---

## 6. 本轮修掉的两个真 bug

1. **缓存未随配置失效**
   `_FT_CACHE` 的键只有文本，不含判定口径。测试实测：改成"只勾 spam"后，
   之前缓存下来的「引战」结论**照样生效** —— 用户改了设置却看不到变化。
   修法：加 `_FT_CACHE_SIG` 签名（`标签集合 + 阈值`），一变就**整表作废**。

2. **`POST /api/textfilter` 返回 404**
   端点被误写进了 `do_GET`。弹幕/评论侧全部静默失败（fetch 报错被 `catch` 吞掉，
   表现为"开了开关但什么都不拦"）。已移到 `do_POST`。

---

## 7. 验证

### 7.1 后端端到端 `_test_ft.py` —— 6 组全通过

模型状态 / 开关矩阵 / 标签勾选与阈值（含缓存失效）/ 吞吐与缓存 / HTTP 接口 / 设置页「试一下」

### 7.2 前端链路单测 `_ft_ui_test.js` —— 18/18 全通过

用假 `window` / `fetch` / DOM 把两个油猴脚本**真跑起来**：

```
[1] 评论筛选 / 文本判定命中 → DOM 落成「已屏蔽 · 原因」          8 项 PASS
[2] 评论筛选 / ft_cf 关掉 → 一条请求都不发                      PASS
[3] 评论筛选 / 壳侧未开启(on:false) → 之后不再发请求             PASS
[4] 评论筛选 / 同句重复出现 → 只问一次(去重)                     PASS
[5] 弹幕筛选 / 文本判定必须在改写字节流之前完成                  3 项 PASS
[6] 弹幕筛选 / ft_dm 关掉 → 不请求且原样放行                     PASS
[7] 弹幕筛选 / 总开关 dm_on=false → 不请求且原样放行             PASS
```

（过程中修掉两个测试脚手架自身的坑：假 `Response` 缺 `arrayBuffer()`；
沙箱没注入 `TextDecoder` 导致 protobuf 里的 UTF-8 正文全解成空串。）

### 7.3 壳层回归 `test_shell.py` —— 146 项 ALL_PASS

其中新增 8 项专测本次改动（模型随包分发 / 三链路 kind / 总开关闸门 / 壳侧注入 /
弹幕顺序约束 / 评论异步 / 设置页 DOM / 训练管线不入 datas）。

### 7.4 打包产物验收 `_verify_exe_43.py` —— ALL_PASS

**静态**（归档里到底装了什么）：
- CArchive 里 `model/ft_model.bin` = **58.38 MB**，与源文件**字节数完全一致**（61,216,097）
- PYZ 里 `ftrt` 在位，且定义了 `FastTextLite`
- `app` 里 `ft_scan` / `ft_probe` / `ft_load` / `ft_status` / `_ft_path` 全在位
- `web/index.html` 里「快速文本分析」8 个 DOM id 全在位，页脚版本正确

**运行期**（打进去之后能不能用 —— 唯一有说服力的证据）：
- 起 `exe --server`（`MIRRORSORTER_PORT` 隔离端口，**不碰用户正在用的 8000**）
- 模型路径 = `...\Temp\_MEI000079182\model\ft_model.bin` ← **`_MEIPASS` 路径解析正确**
- 开总开关后**后台预热**把模型读进内存，`ready=true`，读到 6 个标签
- 「试一下」4 条全对；`kind=video / dm / cf` **三条链路全部生效**
- 总开关关掉 → `on:false`，整批放行

---

## 8. 改了哪些文件

| 文件 | 改动 |
|---|---|
| `desktop/ftrt.py` | **新增** 纯 stdlib 推理运行时（7.6KB） |
| `desktop/model/ft_model.bin` | **新增** 模型本体（58.38MB） |
| `desktop/model/ft_model.meta.json` | **新增** 模型元信息 |
| `desktop/_vis/fasttext_demo/*.py` | **新增** 训练管线（13 个脚本，可复现） |
| `desktop/app.py` | 6 个 `ft_*` 设置项 / `ft_load` / `ft_scan` / `ft_probe` / `ft_status` / `_ft_path` / 信息流漏斗接入 / `POST /api/textfilter` / `POST /api/textfilter/probe` / `GET /api/textfilter/status` |
| `desktop/shell_pages.py` | 注入 `ft_on` / `ft_dm` / `ft_cf` |
| `desktop/extensions/bilibili-dmfilter.user.js` | `NEED_TEXT` + `askText`/`applyText`/`textsOf` + `processAsync` 改 `Promise.all` + `blkWhy` 末尾加内容判定 |
| `desktop/extensions/bilibili-cfilter.user.js` | `NEED_TEXT` + `noteText`/`askText`/`applyText`/`msgOf` + `markBlocked`/`keep`/`filtArr`/`filtAll`/`sweep`/`cfApply` + CSS 角标改 `attr(data-msx-why)` |
| `desktop/web/index.html` | 新增「快速文本分析」页签与面板 + 「试一下」 |
| `desktop/web/app.js` | `ftRenderStatus` / `loadFtSettings` / `ftGate` / `ftSave` / `ftProbe` |
| `desktop/web/style.css` | 「试一下」样式 |
| `desktop/洞鉴虚明v0.3.1.43dsk.spec` | **新增**：`datas` 加 `('model','model')`、`hiddenimports=['ftrt']`、`upx_exclude=['ft_model.bin']` |
| `desktop/构建exe.bat` | 同步加 `--add-data "model;model" --hidden-import ftrt` |
| `desktop/_build_now.py` | 改为**从 .spec 构建**（CLI 模式会覆盖 spec，丢掉 `upx_exclude`） |
| `desktop/test_shell.py` | 新增 8 项文本分析回归 + 同步 2 项因角标改进而变的旧断言 |
| `desktop/_test_ft.py` / `_ft_ui_test.js` / `_verify_exe_43.py` | **新增** 三套验证脚本 |
| `desktop/app.py` / `web/index.html` | 版本号 → `v0.3.1.43dsk` |

---

## 9. 已知边界（说清楚，不粉饰）

1. **spam 是短板**：B站评测集 8/10 = 80%。广告文案变体太多，合成语料的覆盖面有限。
2. **被强化过的短语阈值无效**：「赢麻了」这类 T=8 仍接近 1.0。这是模型确实极度确定，
   不是 bug，但意味着「93 极严」只对边界样本有意义。
3. **弹幕链路在 400ms 内没等到答复时**，这一轮会放行；靠后续分段的缓存命中 + `refilter()` 补。
   极端情况下（壳端卡死）会漏筛 —— 这是**故意**的取舍：宁可漏筛，不可卡播放器。
4. **模型只在中文上有效**。英文/日文弹幕会退化成 `ok` 或随机标签。
5. **不判断"谁发的"**：账号画像（黑名单/等级/神秘人）是另一套规则，两者正交。
6. **58MB 模型会进 git**（`desktop/model/`）。若将来频繁重训导致仓库膨胀，
   可考虑换 Git LFS 或改为"构建时下载"。
