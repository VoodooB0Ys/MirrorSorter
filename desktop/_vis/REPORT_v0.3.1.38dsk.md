# 洞鉴虚明 v0.3.1.38dsk —— 播放页左侧/弹幕折叠/悬浮窗内全屏

## 一、用户报修（m03496 原话）

> 左侧边框太宽了，而且不要改成边框的形式，这样B站的上边条会空出来一块，你解决一下。其次是弹幕列表默认折叠才对。再就是悬浮窗播放的时候，点击视频的全屏播放按钮，画布应该是充满整个悬浮窗，而不是整个屏幕。

## 二、逐条处理与证据

### 1) 播放页左侧：窗口级留白整体取消，空白改到**页面内部**

| | 改前（v0.3.1.37dsk） | 改后（v0.3.1.38dsk） |
|---|---|---|
| 做法 | `shell_pages.py` 用 `TAB_INSET_RATIO = 0.055` + `_tab_dx()` 给页签控件留 118 物理px 左侧空隙 | 删除整套窗口级留白；改由页面注入 `#msx-ownui-page-pad` 给内容容器加左内边距 |
| 症状 | 顶栏左侧空出一块深色、像一条边框 | 顶栏从左缘连续；整块内容（视频+右栏）一起右移 |

- `desktop\shell_pages.py:898` `_tab_geo()` 回到「控件与窗口等宽」：`Size(max(1, home.Size.Width), max(1, home.Size.Height))`（`:910`）；`TAB_INSET_RATIO` / `TAB_INSET_MIN_L` / `TAB_INSET_MAX_L` / `_tab_dx` 全部删除。
- `desktop\extensions\bilibili-ownui.user.js:485-502`：`#msx-ownui-page-pad` 注入
  `html:not(:fullscreen) #mirror-vdcon{padding-left:4vw!important;box-sizing:border-box!important}`
  （`#mirror-vdcon` 不存在时退回 `.video-container-v1`；`:499-501` `fullscreenchange` 时 `ps.disabled = !!document.fullscreenElement`，播放器全屏不留边）。
- 实测（`desktop\_play_pad_probe.py` → `desktop\_vis\play_pad.log`，`RESULT: ALL_PASS`）：
  - 几何（应用自身 `shell_debug.log`，物理px）：`{0: (0,0,2138,1324), 1: (0,99,2138,1225)}` ⇒ **窗口级左留白 = 0、右余量 = 0**；
  - 顶栏 `.bili-header = {x:0, y:0, w:1411, h:64}`、`bodyX = 0` ⇒ **顶栏贴左缘连续**（`:7`）；
  - 页面内部留白 A/B（视频宽 904、右栏宽 350、容器宽 1411）：
    | 候选 | 视频 x | 右栏 x | 判读 |
    |---|---|---|---|
    | baseline | 63 | 997 | 原样 |
    | `#mirror-vdcon` 2.5vw | 76 | 1010 | 右移 13 |
    | **`#mirror-vdcon` 4vw（采用）** | **87** | **1021** | 右移 24；右栏右沿 1371 < 1411，不裁 |
    | `.left-container` 3vw | 106 | 997 | 视频右沿 1010 > 右栏 997 ⇒ 会压住右栏，**不采用** |
  - 截图：`desktop\_vis\play_pad_window.png`（1425x883）。
  - 跨版本对照：v0.3.1.37dsk 同一页面同一视频测得 `#bilibili-player` 页内 `x=41`，本轮同一位置 `x=63` ⇒ 页面内留白把内容右移约 **22 逻辑px**（不是一条宽带子），与上面 A/B 的 +24 量级一致。
- 留白与播放页同色（上一轮已改，本轮沿用）：`desktop\app_window.py` `BG_PAGE = "#17181a"` + `background_color=BG_PAGE`；`desktop\shell_pages.py:1024` `_apply_page_bg(self, dark)`（深色 `_C.FromArgb(23,24,26)`）。

### 2) 弹幕列表默认折叠

- 确认了「未决项」：这份列表是**软件自己叠加的那份**（ownui shadow root 内的 `msxDm*` 控件），不是 B站自带面板。
- `desktop\extensions\bilibili-ownui.user.js:584-585`：
  `var folded = true; try { folded = localStorage.getItem("msx_dm_fold") !== "0"; } catch (e) {}`
  —— 只有用户**显式展开过**（存 `"0"`）才展开；`:586-591` `applyFold()` 把列头 `msxDmCols` 与列表 `box` 设 `display:none`、箭头置 `▸`。
- 实测（`play_pad.log:8-9`）：`{"cols":"none","box":"none","arrow":"▸","title":"▸弹幕列表 共 389 条","boxH":0}` ⇒ 打开播放页时即为收起态。

### 3) 悬浮窗内点视频全屏 ⇒ 只铺满悬浮窗

- `desktop\shell_pages.py:1336` `set_fullscreen(key, on)`：新增 `is_float = host is not box.get("form")`（`:1363`）；
  - 悬浮窗分支：**不动 `host.WindowState`**，改为 `c.Dock = getattr(WF.DockStyle, "None")`（`:1383`，`None` 是 Python 关键字）+ `c.Location = WF.Point(0,0)` + `c.Size = host.ClientSize` + 四边 Anchor；
  - 主窗口分支：维持 `host.WindowState = Maximized` + `c.Dock = Fill`；
  - 首次进入时把悬浮窗**自己的 chrome**（迷你标题栏 + 四边贴边热区）记进 `_Pool._fs_rec`（9 项）并 `Visible=False`；`:1305` `_fs_restore()` 退出时还原，`if not is_float:` 才恢复 `host.WindowState`（避免把用户手动最大化的悬浮窗改回 Normal）。
- 实测（`desktop\_float_fs_probe.py` → `desktop\_vis\floatfs.log`，**RESULT: ALL_PASS**，12 项）：
  - 悬浮窗 rect 全屏前后一致：`(355,243,1120,760)`；
  - 页面自报屏幕位置全屏前后**逐字段相同**：`{sx:355, sy:273, ow:1121, oh:731, iw:1120, ih:730, sw:1707, sh:1067}` ⇒ 页面在屏幕上占 (355,273)-(1476,1004)，面积 395,505 / 1,821,369 = **21.7%**，没有铺到整屏；
  - 全屏元素包围盒 = `[0,0,1120,730]` = 页面视口（若整屏全屏会是 1707x1067）⇒ 贴满的是**悬浮窗视口**；
  - 悬浮窗直接可见子窗口 **5 → 全屏 0 → 退出 5** ⇒ chrome 被藏起/复原；
  - 全屏时页面视口 1120x730 = 悬浮窗客户区 1120x760 少 30px 迷你标题栏。
- **诚实记录（抓屏局限，不是产品问题）**：桌面 DC 整屏图在**非全屏态**能抓到该 WebView2 表面（注入的唯一色 `rgb(1,254,3)` 命中 167,058 个采样点），但**全屏态命中 0** —— Chromium 把全屏表面提升到独立合成平面，GDI/BitBlt/PrintWindow 都抓不到。故像素判据在本场景不成立，探针已如实打印：
  `[注意] 桌面 DC 整屏图里 0 像素该底色 ⇒ 抓屏看不到该 WebView2 表面, 像素判据本轮不可用(已由窗口几何 + 页面 screenX/outerWidth 作主证据)`。
  本轮的主证据是：窗口几何不变 + 页面自报屏幕矩形不变 + 全屏元素盒 == 视口 + chrome 隐藏/复原。

## 三、测试（本轮改动后复跑）

| 套件 | 结果 |
|---|---|
| `desktop\test_shell.py` | `RESULT: ALL_PASS (117 checks)`（新增 视觉4「弹幕默认折叠」、视觉5「悬浮窗内全屏只铺满悬浮窗」；视觉2 改为断言窗口级留白已删 + 页面内留白存在） |
| `desktop\test_smoke.py --skip-ball` | `==== 388 PASS / 0 FAIL ====` |
| `desktop\test_rules.py` | `==== 84 PASS / 0 FAIL ====` |

## 四、版本与产物

- `desktop\app.py:66` `APP_VER = "v0.3.1.38dsk"`；`desktop\web\index.html:244` 页脚同版本。
- `desktop\dist\洞鉴虚明v0.3.1.38dsk.exe` —— 21,278,633 字节（PyInstaller `--onefile --noconsole`，含 `web`/`extensions`/`WebView2Runtime`）。
- exe 冒烟（隔离端口 8139 + `desktop\_tmp_data_exe138`）：真身 pid 21352 监听 8139、窗口标题 `洞鉴虚明 v0.3.1.38dsk`、生成 `data.db`/`shell_debug.log`；测试后已清理进程与端口，**未触碰用户的任何实例**。

## 五、未处理 / 已知限制

- 全屏态无法用 GDI 系列截屏取证（合成平面提升）；如需画面级证据，后续可接 Windows Graphics Capture。
- CDP `Page.captureScreenshot` 在 WebView2 全屏态会超时（`WebSocketTimeoutException`），探针已容错处理。
- 上一轮为排查黑屏加的 `create()`/`on_ready` 诊断日志（`ensure key=… / on_ready key=… success=…`）暂留在代码里，属排障能力，后续可精简。
