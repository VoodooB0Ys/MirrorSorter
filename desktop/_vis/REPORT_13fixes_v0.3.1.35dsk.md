# 洞鉴虚明 v0.3.1.35dsk —— 13 项报修修复与验证证据

工作目录 `D:\Information Sorter`（桌面端在 `desktop\`）。版本从 `v0.3.1.34dsk` → **`v0.3.1.35dsk`**
（`desktop\app.py:66`、`desktop\web\index.html:244`，窗口标题自动跟随 `app_window.py:34 APP_VER = app.APP_VER`）。

证据分三类：**真机**（真窗口壳 + 真 WebView2）、**真浏览器**（Edge + 真注入串）、**组件级**（WinForms/JS 单元探针）。
每个 bug 都附原始产物路径与实测值。

---

## bug1 设置栏折叠子栏后画布拉不到最下方
- 改法：`desktop\web\app.js:271 function syncAppH()` 把实测视口高度写进 `--app-h`（`:274 setProperty("--app-h", h+"px")`、`:278 window.addEventListener("resize", syncAppH)`）；`desktop\web\style.css:34-35 #app{height:calc(var(--app-h,100vh)/var(--ui-zoom,1))}`；`:43` 与 `:57 #railBody{… min-height:0}` 消除 flex 子项的最小高度陷阱。
- 证据：`desktop\_ui_verify.json`（Edge 6 组视口 × 3 档字体）。`--app-h` = `800px` 时 `#app` 高 = `innerHeight` = `visualViewport` 高；`#railBody` 在「初始 / 全折叠 / 全展开 / 隔一折一 / 1280x560」五种状态下每次都滚到 `max` 且页脚可见（`clientH 713→scrollH 2655 / max 1942` 等）。截图 `_vis\bug1_rail_all_collapsed.png`、`_vis\bug1_rail_odd_collapsed.png`。

## bug2 关掉「信息以新窗口形式打开」后不能从标签页拖出悬浮窗
- 根因：`desktop\app_window.py:390 def tab_detach()` 里原有一道 `if not self._new_window_enabled(): return "off"`，把「怎么打开信息」的开关误当成「能不能拖出」的开关。
- 改法：删掉该早退分支（保留 `pinned`/`already`/`no-pages` 判定），开关只在 `open_in_app()` 里决定「点开信息」默认标签还是新窗口。
- 证据（**真机**）：`desktop\_tmp_data_e2e\float_e2e.log`（同内容 `_vis\float_e2e_stdout.log`）。隔离库里 `ui_new_window=0` 后跑 `app_window.py --e2e-float <url>`：
  - `[float-e2e] init floats=True new_window=False`
  - 真鼠标从悬浮窗迷你标题栏拖到标签栏 → `drag-merge -> merged=True (still_float=False)`（合并回标签页，`highlight 调用序列=[True, False, False]`）
  - `[float-e2e] detach -> ok` → `after-detach … {'id':1,'float':True…} floats=[1]`
  ⇒ 开关关闭时拖出/塞回两条路都通。回归断言：`desktop\test_shell.py:269` 与 `:425`。

## bug3 设置项描述太繁琐 → 短标签 + 悬停看详情
- 改法：`desktop\web\index.html` 设置栏标签收敛为短标题，详细说明进 `title` 属性（不撑高、不被 `#railBody` 的 `overflow:auto` 裁掉）；无控件的整句说明降级为 `p.tip`。
- 证据：`desktop\_ui_verify.json` —— `#railBody label[title]` 共 **15 条**（如「信息以新窗口形式打开」「强调色」「AI超分」），`longLabelsLeft: []`，旧句「强调色（按钮/徽标/高亮的特殊颜色）」已不在页面；截图 `_vis\bug3_settings_rail.png`，真机窗口截图 `_vis\fs_e2e_1_normal.png` 左侧设置栏亦为短标签。

## bug4 悬浮窗缺全屏/最小化按钮 + 除最小化外始终置顶
- 改法（`desktop\shell_float.py`）：`:26` 新增 `_BTN_MIN, _BTN_MAX = "min", "max"`；`:116 _BTN_ORDER = (_BTN_DOCK, _BTN_MIN, _BTN_MAX, _BTN_CLOSE)`；`:131 def _is_max`、`:138 def _toggle_max`；`:179-180` 迷你标题栏绘制 `⤓ — ▣/□ ✕`；`:242 form.TopMost = True`；`:353-355` 最小化按钮。
- 证据（**组件级真 WinForms**）：`_vis\fs_probe_bug4_6_7_10.log` —— 注入 `WM_LBUTTONDOWN/UP` 后 `windowState=Maximized IsZoomed=True`，再点还原为 `Normal`，最小化 `IsIconic=True windowState=Minimized`，`⤓` → `calls=[('window_dock',(7,))]`，`✕` → `tab_close`；`exstyle=0x50008`（TOPMOST）、激活别的窗口后 `floatZ=14 < otherZ=69`（排序不变）。

## bug5 播放页弹幕列表无法折叠
- 改法（`desktop\extensions\bilibili-ownui.user.js`）：标题即开关 `:545 id="msxDmFold"`，折叠态切列头/列表显示并记忆 `localStorage["msx_dm_fold"]`。
- 证据：`desktop\_ui_verify.json` —— 影子树内 before `{box:block, ar:▾, cols:grid}` → 点击后 `{box:none, ar:▸, cols:none, ls:"1"}` → 再点回；截图 `_vis\bug5_danmaku_folded.png` / `_vis\bug5_danmaku_expanded.png`。

## bug6 网页全屏时隐藏网址栏与选项栏
- 改法：全仓库原本没有任何全屏接线。`desktop\shell_pages.py:1026 def on_fs_changed`（订阅 `CoreWebView2.ContainsFullScreenElementChanged`）→ 控件池 → `shell_tabstrip.py:529 def set_fullscreen(on)` 与 `shell_navbar.py:346 def set_fullscreen(on)`（导航栏原先没有显隐接口）→ `app_window.py:1029 on_fs=on_fs` 把两条 chrome 串起来。
- 证据（**真机**）：`desktop\_fs_e2e_probe.py` → `RESULT: ALL_PASS (13 checks)`。真窗口壳 + 真 WebView2，页面里真 `<video>` 进全屏后：可见子窗口 **30 → 0**（标签栏/导航栏/3 块贴边热区全部让位）；退出后回到 30。截图 `_vis\fs_e2e_2_fullscreen.png`（整屏纯深色、无 chrome、无白边）。

## bug7 地址栏文字太刺眼 → 未悬停时 70% 不透明度
- 改法（`desktop\shell_navbar.py`）：`:116 def _apply_tb_fg()` 用 `_blend(fg, bg, 0.7)` 混色，非编辑/未悬停时生效；`:123 _set_tb_hover` + 指针坐标命中判定 + `MouseEnter/MouseLeave`；`set_scheme` 换肤后按新底色重算。
- 证据（**组件级真 WinForms**）：`_vis\fs_probe_bug4_6_7_10.log` —— 底色 `(22,25,32)`、满色 `(226,230,238)`、未悬停实测 `(164,168,176)` vs 理论 `(165,168,176)`（逐通道 ±1）；指针移开回到 `(164,168,176)`。

## bug8 开启评论筛选后评论偶发无法继续向下滚动
- 根因（反编译 B 站 `bili-comments.js` 定位）：组件 `getList` 结尾是 `h.length && this.updateList(this.list.concat(h))`，翻页触发靠 `IntersectionObserver`/`scroll`。我们把一整页条目全筛掉时返回空数组 ⇒ `h.length===0`：列表不增长、不置 `showEnd`、`showContinuations` 已被复位 ⇒ 容器不动、观察者不再回调，页面卡死（这就是「有概率」的来源）。
- 改法（`desktop\extensions\bilibili-cfilter.user.js`）：`:123 function isMainList(u)` 区分主列表；`:137 function redactOne(r)`（`:139 r.__msxRedacted = true`，把 `uname/avatar/content/jump_url` 脱敏、`replies=[]`、`rcount/count=0`）；`:304` 主列表整页筛光时**保留数组长度**（`:339/:343 if (redact) redactOne(d.top); else d.top = null;`）；调用点 `:409`、`:444`、`:494` 带上 `isMainList`。楼中楼展开维持旧行为（摘空 + 计数归零）。
- 证据：`node _cf_probe4.js` → `_vis\cf_probe4_bug8.log`（4197 B）4 个用例：
  - `main_all_blocked`：`n=3 redacted=3`，`unames=["已屏蔽"]`、`avatars=["data:image/gif;base64,"]`、`msgs=["该评论已被评论筛选屏蔽"]`、`rcounts=[[0,0]]`、`fieldsKept=true`、`page=100`；模拟组件尾段 `comp={hLen:3, wouldUpdateList:true, showEnd:false, nextOffset:"{\"offset\":\"OFF1\"}", listLen:3}` ⇒ **列表会继续翻页**。
  - `main_mixed`：`n=3 redacted=2`，留下的那条 `avatar/jumps/pics/subLens/rcounts` 原样保留。
  - `main_none_blocked`：`n=2 redacted=0 changed=false`（无屏蔽时不改写响应体）。
  - `sub_all_blocked`（旧行为）：`n=0 page=0 root=[0,0] comp={hLen:0, wouldUpdateList:false}`。
  - `hint="评论筛选：已隐藏 7 条评论"`、`pageerrors` 无。

## bug9 右侧推荐视频封面太小
- 改法：`desktop\extensions\bilibili-ownui.user.js:407 .it .cv{flex:0 0 60%; min-width:150px; max-width:300px; aspect-ratio:16/9}`（配合标题行数收敛让条目高度稳定）。
- 证据：`desktop\_ui_verify2.json` —— `#msx-ownui-related` 影子树内 36 条 `.it .cv`，封面实测 **181×102**（比例 1.78），条目 326×120；截图 `_vis\bug9_related_panel.png`、`_vis\bug9_right_panel.png`。

## bug10 全屏左侧缝隙露桌面 + 播放界面白色边框
- 根因两条：① 主窗口 `MAIN_W,MAIN_H = 1440,920` 且网页全屏不会让窗口变大 ⇒ 屏幕比窗口大时四周（尤其左缘）直接露出桌面；② 贴边热区面板原先被涂成标签栏底色（`shell_tabstrip.py` / `shell_float.py`），白天主题下就是那圈浅色边框。
- 改法：`shell_tabstrip.py:185 EDGE_COLOR = Color.FromArgb(15,17,21)`（热区恒用窗体底色，换肤不再跟着变）；`shell_pages.py:1229 def _fs_restore` / `set_fullscreen` 在进入网页全屏时把**宿主窗一起最大化**并把控件 `Dock=Fill`，退出按 `窗体状态 → 兄弟控件 → Dock/Anchor/Location/Size` 顺序精确还原（`:895 def _tab_geo` 统一外部标签页 20px 内缩几何）；`:1103 none_c = ctypes.c_int(-2)` + DWMWA_BORDER_COLOR 去掉 Win11 系统描边。
- 证据（**真机**）：`_fs_e2e_probe.py` ALL_PASS —— 非全屏 `rect=[253,253,1425,883]` / `zoomed=false`；全屏 `rect=[0,0,1707,1066]`（工作区 1707×1067）、`zoomed=true`，可见子窗口 30→0；退出后精确回 `[253,253,1425,883]`。组件级另见 `_vis\fs_probe_bug4_6_7_10.log`（白天配色下 3 块热区恒为 `(15,17,21)`）。真机截图 `_vis\fs_e2e_1_normal.png` / `_vis\fs_e2e_2_fullscreen.png` / `_vis\fs_e2e_3_restored.png`。
- 探针顺带揪出并修掉两个真缺陷：`_fs_rec` 记录 7 项而还原解包 8 项（`ValueError: not enough values to unpack (expected 8, got 7)`，退出全屏完全不还原）；还原时宿主窗仍处 Maximized，四边锚定把 `980x554` 重新缩放成 `273x130`。

## bug11 B 站播放页大广告框没被屏蔽（原修复实际失效）
- 根因：壳用 `AddScriptToExecuteOnDocumentCreated` 注入（与扩展 `document-start` 同一时点），此时 `document.head` 与 `document.documentElement` **都是 null**，`(document.head || document.documentElement).appendChild(style)` 抛 TypeError 被 `try{}catch{}` 吞掉 ⇒ 广告规则从未挂上（顺带 boot 遮罩也白申请）。
- 改法（`desktop\extensions\bilibili-ownui.user.js:61 function mountStyle` + `whenRoot`，等文档根出现再挂）：广告屏蔽与 boot 遮罩都改成 `mountStyle("msx-ownui-adblock", …)` / `mountStyle("msx-ownui-boot-style", …)`。
- 证据：`desktop\_ad_verify2.json`（只注入壳的真串、探针不补任何 style）—— `adblockInDom=true`、`bootStyleInDom=true`、`ownRulesMatchesSelector=true`、`adCount=8` 且 `adDisp` 全 `none`、`adRects` 全 `[0,0]`、护栏 `.bpx-player-adv-dm-wrap` `guardCount=1 guardDisp=["block"]`（高级弹幕层未误伤）、`pageerrors=[]`。

## bug12 信息流来源标签的强调色实底太闪眼
- 改法：`desktop\web\style.css:341 .card .badge{background:transparent; border:1px solid …; color:var(--sub)}`，平台色只留在文字上（`.plat-bili{color:var(--accent)}` / `.plat-tb{color:#7fa3e8}` / `.plat-xhh{color:#e08a8d}` 及白天变体）。
- 证据：`desktop\_ui_verify.json` —— `.card .badge` computed `background: rgba(0,0,0,0)`、`border 1px rgb(40,46,57)`，文字色 bili `rgb(47,191,113)` / xhh `rgb(224,138,141)` / tb `rgb(127,163,232)`，页面上 12 张卡片带 badge；截图 `_vis\app_feed_1280x800.png`（真机截图 `_vis\fs_e2e_1_normal.png` 里【小黑盒】同样是低调文字标签）。

## bug13 超分暂时性画布异常弹报错框
- 改法（`desktop\extensions\bilibili-sr.user.js`）：`:267` 起只有**连续两次**自检失败（`_layerBadRun >= 2`）才 `showDiag(...)`，单次瞬时异常只 `console.warn("[msx] SR 图层瞬时异常(不弹框):", …)`；干净自检把 `_layerBadRun` 清零并撤面板；新增诊断出口 `:281 window.__msxSrProbe = layerProbe`。
- 证据：`desktop\_sr_probe.js` → `_vis\sr_probe_bug13.log`，`RESULT: ALL_PASS (9 checks)`：对齐 `diff=0` 无面板；首次错位 30px → 控制台唯一告警「瞬时异常(不弹框)」且无面板；连续第二次 → 面板「[洞鉴虚明 诊断] SR 图层异常: 画布与视频框不重合 30px」；恢复后撤掉；再抖一次仍不弹；`errs=[]`。

---

## 回归与测试
- `desktop\test_shell.py:406` 起新增「3f. 13 项用户报修回归(v0.3.1.35dsk)」，逐条断言 13 项修复的代码形状（含 bug2 的行为断言 `:269`、`:425`）→ **`RESULT: ALL_PASS (112 checks)`**。
- `desktop\test_smoke.py --skip-ball`（`MIRRORSORTER_DATA_DIR=desktop\_tmp_data_test`）→ **`==== 388 PASS / 0 FAIL ====`**。
- `desktop\test_rules.py` → **`==== 84 PASS / 0 FAIL ====`**（需 `PYTHONIOENCODING=utf-8`，否则 `test_rules.py:55` 打印 ‼ 时 GBK 崩）。
- 前端改动全部在真 Edge（playwright-core `chromium.launch({channel:"msedge"})`）与真窗口壳里跑过，`page.on("pageerror")` 均无前端异常。
- 未改动用户真实数据：所有自测跑在 `MIRRORSORTER_DATA_DIR` 隔离目录（`_tmp_data_test` / `_tmp_data_e2e` / `_tmp_data_vis`）。

## 已知边界（如实说明）
- bug8 的验证是「按组件源码机制构造的单元级对照」（headless 无登录、B 站评论组件不渲染真实列表，无法在真页面翻页复现）；修法与组件 `getList` 的实际判据逐条对齐。
- bug4 的按钮/置顶、bug7 的混色为组件级真 WinForms 证据（会话锁定时真实鼠标事件送不进窗体，改用等价的 `PostMessage`/`SendMessage` 投递，已实测能触发 MouseDown/Click/MouseUp）；bug6/bug10/bug2 为真机窗口级证据。
