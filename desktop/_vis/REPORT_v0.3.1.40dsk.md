# 洞鉴虚明 v0.3.1.40dsk 交付报告

交付物：`desktop\dist\洞鉴虚明v0.3.1.40dsk.exe`（21,285,468 字节，2026/10/9 23:38）
打开方式：双击 dist 里这个最新的 exe 即可，**不需要任何测试入口、不需要额外开关**，下面第 1 项打开 B站播放页就是生效状态。

---

## 1. 评论被屏蔽后不再留占位：整条移除 + 顶部一行计数 + 一个开关

**你提的**：被屏蔽的评论在列表里留「已屏蔽 LV6 / 该评论已被评论筛选屏蔽」的卡片，看着奇怪；希望整条隐藏，只在评论区最上方显示屏蔽了多少条、是否展示筛选的按钮，跟弹幕筛选类似。

**现在**：整条从列表移除（`display:none!important` + 打上 `data-msx-cf-hidden="1"`），评论区最上方一行 `评论筛选：已屏蔽 N 条评论` + 右侧 `显示屏蔽 / 隐藏屏蔽` 开关，交互与弹幕筛选一致；状态记在 `localStorage.msx_cf_show_blocked`。

**实现**：`desktop\extensions\bilibili-cfilter.user.js`（`blkApply()` / `blkHost()` / `#msx-cf-bar` / 开关点击）。
顺带修掉一个一直没生效的老毛病：`BLK_TXT` 变量未定义会让 `sweep()` 抛 `ReferenceError`，被 `try{}catch{}` 吞掉，导致整条隐藏从来没执行过。

**证据（在打包 exe 的登录态实例里真跑，真鼠标点）**：`desktop\_vis\cf_cf10_exe.log` = **17 PASS / 0 FAIL**

- 登录态自证：cookie 里有 `SESSDATA` / `DedeUserID` / `bili_jct`；B站 nav 接口 `{"code":0,"isLogin":true,"uname":"人工玉米淀粉","mid":326992565}`
- 整条隐藏：`[等机制生效] {"ls":"0","marks":24,"visMarks":0,"marksHiddenByUs":24,"hiddenHosts":12,"hiddenTags":{"BILI-COMMENT-THREAD-RENDERER":12}}`
  → 占位元素 24 个、**可见 0 个**、24 个都由我们打了隐藏标记，隐藏落点是 B站评论线程宿主（祖先链里 `BILI-COMMENT-THREAD-RENDERER*`）
- 顶部一行计数：`count=1 text='评论筛选：已屏蔽 15 条评论显示屏蔽 15' parent=commentapp`，`bar=[87,0,904,27]` 位于评论区 `host=[87,0,904,1515]` 最上方（不占列表高度）
- 开关（真鼠标）：点一下 → `ls=1, visMarks=52`（被藏的重新可见）；再点 → `ls=0, visMarks=0, marksHiddenByUs=52`（又整条收起）；localStorage 同步 `'1'` → `'0'`
- 翻页不被卡死：下拉后线程 20→40、顶部计数 15→31
- 机制自证：现场合成一条评论线程，`{"host":true,"attr":"1","pvis":false}`
- 广告屏蔽规则确实挂在 DOM 上：`adblock={"tag":"STYLE","len":209,"hasRules":true,"liveProbe":"none","realCount":2}`
- 截图：`_vis\cf_cf10_page.png`（隐藏态：列表里看不到任何「已屏蔽」残留，只剩顶部一行）、`_vis\cf_cf10_shown.png`（显示屏蔽态）、`_vis\cf_cf10_window.png`（2138x1324 整窗）

同一份探针在源码实例上也跑过一次全绿（`desktop\_vis\cf_cf6_run.log`，17 PASS / 0 FAIL），两边互相印证。

---

## 2. 「下载 .json 文件」点了没反应

**原因**：老实现是 Blob + `URL.createObjectURL` + `<a download>`，在 WebView2 壳里不会真的下载。
**现在**：走壳的原生「另存为」对话框（`desktop\app_window.py:300 save_text()`，marshal 到 UI 线程的 WinForms `SaveFileDialog`），默认目录依次尝试 下载 / 桌面 / 文档；桥不可用时才退回 Blob；安卓走系统分享。

**证据**：
- 打包 exe 上真点：`desktop\_vis\dl_dl7_console.log` = **5 PASS / 0 FAIL**，含 `[PASS] 点「下载 .json 文件」后弹出原生另存为对话框  对话框=[(2559048,'#32770','保存导出文件',(380,380,1200,800))]`
- 源码实例上真写盘：`desktop\_vis\dl_*.log` —— 对话框确认后页面回显「已保存到：D:\Information Sorter\desktop\洞鉴虚明-设置-2026-10-09.json」（176 字节，`sanitized:true`）

---

## 3. 开不起来 / 一直卡死

**根因**：启动时在 WinForms UI 线程上执行 `UPDATE chat SET cid=1 WHERE cid=0`，被 SQLite 写锁挡住 —— 基准代码实测这一步 **10,883.3 ms / 整轮 10,884 ms**，UI 线程全被占住。
**修复**：`desktop\app.py:314-315` 先 `SELECT 1 FROM chat WHERE cid=0 LIMIT 1` 判空再更新（同基准测得 1 ms）。

**证据**：`desktop\_vis\launch_t9.log`（新 exe 3.0s 出窗、20/20 次 UI 响应、20/20 次 `/api/feed` 200）；本轮 cf10/cf6 两次实跑都能正常起窗并完成整轮真鼠标交互。

---

## 4. 导出默认不带个人信息

**你提的**：不导出个人信息是默认，导出个人信息才是可选。
**现在**：`desktop\app.py` 的 `_IO_CRED_KEYS` / `_IO_SHAREABLE` / `settings_export_payload(include_credentials)`，UI 上 `#ioExpPriv` 默认不勾。默认导出 payload 带 `"sanitized": true`，`ai_key` / `bili_cookie` / 账号昵称等一律不出现；勾选后才包含。

**证据**：`desktop\_vis\launch_t10_exported.json`（228 字节，`"sanitized":true`，无任何泄漏项）；勾选后 `desktop\_vis\launch_t10_exported_priv.json` 才出现 `"ai_key":"sk-LEAKCANARY-39dsk-abcdef"` 等。

---

## 5. 上一轮三项视觉（不回退）

播放页左侧留白改到页面内部（B站顶栏从左缘连续，不再像一条边框）、弹幕列表默认折叠、悬浮窗内点全屏只铺满悬浮窗自身。
证据：`desktop\_vis\floatfs.log`（12 PASS：悬浮窗 rect 前后一致 `(322,204,1120,760)`、页面视口 `1120x730` = 悬浮窗客户区、未最大化）、`desktop\_vis\play_pad.log`。

---

## 6. 回归测试

| 套件 | 结果 |
| --- | --- |
| `python test_shell.py` | `RESULT: ALL_PASS (127 checks)` |
| `python test_smoke.py --skip-ball` | `==== 388 PASS / 0 FAIL ====` |
| `python test_rules.py` | `==== 84 PASS / 0 FAIL ====` |

---

## 7. 这轮的取舍 / 没做完的（如实说明）

- 本轮把「评论占位」与「下载 json」两件在飞的事做完，并在**打包 exe 的登录态实例**上取了证。历史上提过但未落实的其它小项，这轮没动。
- 第三方顾问复核在本机读不到工程文件（`subject-read-disabled`），所以上面全部是**自测证据**（日志 + 截图 + 真鼠标操作），没有第三方验收结论。
- 探针侧（不是产品代码）本轮修了三处，避免误判：开关初态归零（登录副本的 localStorage 会带着上一轮的「显示屏蔽」态）、等机制生效再断言（扫描别跑在 sweep 之前）、CDP 端口参数化（避免连到别的实例上）。
- 测试全程用隔离端口 + 隔离数据目录 + 登录态副本（`_wv2_profile_prep.py`），没有动你手上的那份数据与正在用的实例。
