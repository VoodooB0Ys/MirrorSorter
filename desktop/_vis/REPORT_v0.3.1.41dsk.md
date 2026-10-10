# 洞鉴虚明 v0.3.1.41dsk 交付报告

交付物：`desktop\dist\洞鉴虚明v0.3.1.41dsk.exe`（21,311,144 字节，2026/10/10 09:23）
双击 dist 里这个最新的 exe 即可，**不需要任何测试入口、不需要额外开关**，打开 B站播放页就是生效状态。

本轮只动一个文件：`desktop\extensions\bilibili-cfilter.user.js`（评论筛选，原生评论区）。
配套改了 `test_shell.py` 里三条针对旧实现的断言（否则会误报失败）。

---

## 你报的三条

> 1. 屏蔽评论导致评论页面画布不会随着往下翻而自动重建，导致无法一路翻看到最下方的评论。
> 2. 显示筛选掉的评论时依旧看不到他们发了什么，此时应该原封不动展示内容，并且增加一个【已屏蔽】的显示来提示你这个评论是被屏蔽的。
> 3. 筛选评论的速度可以提升。

---

## 根因（三条是同一处设计的连带后果）

**先讲清背景**：评论是 Web Component `<bili-comments>`，列表在它的 shadow DOM 里；每条评论是
`<bili-comment-thread-renderer .data=评论对象>`（反编译 `_tmp_bilicomments.js` 实证）。
上一版为了「整条隐藏」，做的是**就地脱敏**：把被屏蔽评论的 `message` 改成
`"该评论已被评论筛选屏蔽"`、`uname` 改成 `"已屏蔽"`，再用 DOM 扫描按这段占位文案找到宿主、
`display:none` 掉。

1. **翻页卡死** —— 根因不在"脱敏"，而在**整条 `display:none` 把列表压得太短**。
   B站评论组件的"往下翻自动加载"是 `IntersectionObserver` 盯着 `#continuations .trigger` 这个哨兵：
   **只有哨兵从"视口外"进入"视口内"才触发一次 `getList()`**。
   被屏蔽的评论一旦占多数，列表高度塌到比视口还矮，哨兵**永远停在视口里**，
   再没有"进入"事件 → 组件不再请求下一页 → 你就卡在当前页，看不到最下面的评论。
2. **显示屏蔽看不到原文** —— 因为原文在数据层**已经被抹掉了**（上面那段脱敏），
   开关只是把 `display:none` 去掉，露出来的还是那句占位文案。
3. **速度** —— 旧 `sweep()` 每轮都 `querySelectorAll("*")` 扫**整个文档 + 所有 shadow 根**，
   再逐个读 `textContent` 找占位文案。播放页上就是上万个元素 + 上万次读文本，一轮几十~上百毫秒，
   还每 1.2 秒来一次。

---

## 现在怎么做的

**数据层：主评论一条不删、一个字段不改，只"标记"。**
被屏蔽的主评论只把 `rpid_str` 记进 `BLK_RPIDS`；数组长度、内容全部原样 → 组件拿到非空数组照常翻页。
（楼中楼子回复仍按老规矩直接摘掉并同步 `rcount`/`count`，那是安全的。）
`rewriteObj` 只在**真的改写了数据**（楼中楼被摘）时才回吐新 JSON，否则返回 `null` 让页面直接用原始响应，省一次序列化。

**渲染层：按 `.data.rpid_str` 命中，整条藏/放。**
`sweep()` 只查评论组件 shadow 根里的 `bili-comment-thread-renderer`，用 `BLK_RPIDS` 做 O(1) 命中：
- 默认 → 给宿主打 `data-msx-blk="1"` + `data-msx-hide="1"`，用**注入样式里的 `!important` 规则**隐藏
  （不是 inline，组件自己改 `display` 也盖不掉）；
- 「显示屏蔽」→ 去掉 `data-msx-hide`，评论**原封不动**出现，只在其上方加一个纯 CSS 的 **「已屏蔽」角标**（`::before`）和一道左侧强调色细条。

**翻页补泵 `pump()`（新增）。**
拿评论组件实例的 `getList / continueTrigger / showEnd / showSpinner / showContinuations`：
只要哨兵还在视口里、组件又没在加载、也没到底，就**替组件翻下一页**。
于是被屏蔽评论压短列表时，会一路把后续页拉上来，直到你看到底（`showEnd`）或列表够长把哨兵顶出视口为止。
连续多次"翻了不长个"会收手（防死循环），切页/切开关会复位。

---

## 证据

**1) 三处修复的行为验证（jsdom 最小桩，模拟"哨兵卡在视口"的翻页卡死场景）**
`desktop\_verify_cf41.cjs` → **22 PASS / 0 FAIL**（运行：`node _verify_cf41.cjs`）

- 场景1（全被屏蔽、3 页 + 空页）：
  - 主评论被标记并**整条隐藏**，且 `.data.content.message` **仍是原文**（未被改写）
  - 顶部一行计数与已屏蔽条数一致（`评论筛选：已屏蔽 20 条评论`）
  - 主评论响应**数组长度不变**（不再"整页筛光→组件卡死"）
  - **补泵把后续页也翻上来了**：`list=30`、到底后 `showEnd=true`、翻上来的页同样被隐藏（`blocked=30`）
- 场景2（点「显示屏蔽」）：30 条**全部放出**、显示的是**原文**、样式里有 `::before{content:"已屏蔽"}`、开关文案切到「隐藏屏蔽」
- 场景3（评论都不被屏蔽）：**不触发补泵**，仍停在第一页（`list=10`）—— 证明补泵不会乱翻页
- 场景4（楼中楼）：主评论数组长度不变；父1 子回复摘掉 1 条且 `rcount` 同步为 1；父2 子回复摘光且 `rcount` 归零
- 场景5（扫描范围，结构性证明）：整个过程里 `querySelectorAll("*")` **一次都没出现**，只用了
  `["bili-comments","bili-comment-thread-renderer"]` —— 即"速度"那条的根因已消除

**2) 回归**
| 套件 | 结果 |
| --- | --- |
| `python test_shell.py` | `RESULT: ALL_PASS (129 checks)` |
| `python test_rules.py` | `==== 84 PASS / 0 FAIL ====` |
| `python test_smoke.py --skip-ball` | `==== 387 PASS / 1 FAIL ====` |
| `node --check extensions/bilibili-cfilter.user.js` | 通过 |

唯一失败项 `体检:本机可跑悬浮窗` 是**本机环境问题**（当前 Python 环境缺 tkinter 图形库），与本改动无关。

**3) 打包 exe**
- 启动冒烟：`--server` 起服务、`/api/feed` → 200、首页页脚已是 `v0.3.1.41dsk`
- 归档核对（`PyInstaller.archive.readers.CArchiveReader` 取出 exe 内的 `extensions\bilibili-cfilter.user.js`）：
  含 `data-msx-blk` / `data-msx-hide` / `continueTrigger` / `function pump()` / `v0.3.1.41`，
  **不含** `redactOne` / `REDACT_TXT` → 打进去的就是新版

---

## 如实说明（没做/做不到的）

- 本轮验证是**结构性 + 模拟**（jsdom 桩 + 源码断言 + exe 归档核对），**没有**做真机登录态 B站 播放页的实跑取证
  （需要真账号 + 真视频 + 真鼠标，本轮没走这条链路）。补泵依赖的 `getList/continueTrigger/showEnd`
  等接口名，是从反编译的组件包 `_tmp_bilicomments.js` 里逐条核出来的（`kn("bili-comments")` = 自定义元素、
  `Sn("#continuations .trigger")` = 哨兵、`showContinuations/showEnd` = 加载/到底状态），不是猜的。
- 楼中楼子回复仍是"直接摘掉"（不做"显示屏蔽"），因为它们是折叠面板里的内容、默认不可见；
  主列表才是你反馈的那条。若你希望楼中楼也能"显示屏蔽看原文"，下一轮可以单独做。
- `_cf_dom_probe.py` / `_final_check.py` 等旧探针脚本仍写着上一版的占位文案，本轮没动它们（它们不参与产品链路）。
