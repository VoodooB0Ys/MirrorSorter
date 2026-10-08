# GOAL · 洞鉴虚明桌面版 v0.3.1 —— 浏览器式导航工具栏（标签栏下方）

> 分支：`desktop-embedded-browser`（延续） · 起始版本 v0.3.0.0dsk（git 备份点 26639b8 +
> `desktop/backups/mirror-sorter-backup-20261008-1000.tar.gz`） · 目标版本 **v0.3.1.0dsk**
> 本文是本次任务的全部目标与任务分解，做一项勾一项；全部勾完即达成目标。
> 上一任务（v0.2.9 内置浏览器标签页）目标见 `GOAL_v0.2.9.md`。

---

## 0. 一句话目标

**在顶部标签栏的正下方，给所有标签页（信息流首页 + 内置浏览器播放页）加上所有浏览器
都有的那条导航工具栏：← 后退、→ 前进、⟳ 刷新 + 可编辑地址栏 —— 实时显示当前标签页
的网址，按钮可用态跟随页面历史，地址栏回车即可跳转。**

## 1. 背景与需求（用户原话）

"现在要对首页以及播放页面，也就是选项卡下方增加一个所有浏览器都有的页面"
（附参考截图：浏览器导航条 —— ← → ⟳ 三个按钮 + 一条 URL 地址栏
`bilibili.com/video/BV1ES5izSEhR/?...`）。

现状（v0.3.0.0dsk）：窗口顶部是原生标签栏，标签栏下方直接就是网页内容：

- 回退/前进只能靠鼠标侧键（XBUTTON），没有可点的按钮，没侧键的用户无路可走；
- 看不到"当前标签页在哪个网址"，标签内多次跳转后完全迷路；
- 刷新只能靠网页内按钮或 F5，没有浏览器习惯位置的 ⟳。

## 2. 目标体验（用户视角，逐条可验收）

1. **位置与覆盖**：标签栏正下方一条横贯窗口宽度的工具栏（约 34 逻辑 px 高），属于窗口
   原生 chrome —— **所有标签页都有**（信息流首页、任意内置浏览器页），网页内容区整体
   下移（= 标签栏高 + 工具栏高），零遮挡。
2. **按钮区**（左侧）：`←` 后退、`→` 前进、`⟳` 刷新三个图标按钮，约 32 逻辑 px 见方；
   - 可用态/禁用态跟随**当前激活标签页**的 `CanGoBack` / `CanGoForward`（禁用=灰、不可点）；
   - 悬停高亮（同标签栏 hover 色）；点击立即执行对应动作。
3. **地址栏**（占剩余宽度）：深色圆角输入框，显示当前激活标签页的完整 URL；
   - **实时性**：标签内跳转（含 B 站站内导航）后自动更新；切换标签立即切到该标签的 URL；
   - **可编辑**：点击聚焦自动全选；回车导航 —— 无协议头自动补 `https://`；非 http(s)
     输入（如 `file://`、乱敲文字）不导航并还原显示；Esc 还原；失焦未回车还原；
   - 导航即"在当前标签页内跳转"（不新开标签，与浏览器一致）。
4. **首页标签同规则**：信息流首页 URL 显示 `127.0.0.1:<port>/...`；`← →` 按首页自身的
   WebView2 历史工作（首页没有历史时禁用，**不**沿用鼠标侧键"关标签"的语义）；
   `⟳` = 重载信息流页面。
5. **刷新**：`⟳` = 当前标签页 `CoreWebView2.Reload()`（P2 可做加载中变 `✕` 停止）。
6. **主题跟随**：工具栏底色、文字色、悬停色、分隔线与标签栏共用同一套
   `ui_bg_scheme` 配色推送（深浅切换实时生效）；地址栏输入框底色/文字色同步，
   不得出现白底闪块。
7. **交互零回归**：
   - 拖动窗口仍只能从标签栏空白区（工具栏/地址栏按下不触发窗口拖动）；
   - 边缘缩放热区、窗口化全屏、`─ □ ✕` 窗口按钮不受影响；
   - 鼠标侧键行为完全不变（侧键仍是"标签内回退，到头关标签"）;
   - 工具栏不主动抢键盘焦点（切标签后焦点仍落 WebView2 控件）。
8. **每标签独立**：地址栏内容与按钮可用态是"激活标签"的视图，任何时刻只反映激活标签。

## 3. 技术设计（对照现有架构，落到文件）

现状架构（v0.3.0）：`shell_tabstrip.py` 在 `form` 顶部放 `Dock=Top` 自绘 Panel（标签栏，
高 `bar_h`），并把主 WebView2 控件定位到 `y=bar_h`；`shell_pages.py` 控件池为每个标签页
建独立 WebView2 控件（`create()` 按主控件 `Location` 摆放 → 自动跟随）；`app_window.py`
的 `ShellApi` 管标签表，`navigate_history(key, back)` 已存在（侧键在用）。

```
┌─ shell_tabstrip.py (标签栏, bar_h) ──────────────────────┐
├─ shell_navbar.py (新 · 导航工具栏, nav_h) ───────────────┤
│  [←] [→] [⟳]   [ 🔒 https://www.bilibili.com/...    ]   │
├─ WebView2 内容区 (y = bar_h + nav_h, 各标签页控件) ──────┤
└──────────────────────────────────────────────────────────┘
```

### 3.1 新文件 `shell_navbar.py`

- `install_navbar(win, api, top, height_scale, theme=None)`：在 `form` 上放一个
  **手动定位**的 Panel（`Location=(0, top)`，`Anchor=Top|Left|Right`，不用 Dock ——
  规避 WinForms 多个 Dock=Top 的 z 序陷阱），返回 `update(url, can_back, can_fwd,
  loading=False)`（跨线程安全，内部 BeginInvoke）。
- 布局：左端 3 个自绘按钮（Paint 事件画字形 `← → ⟳`，悬停高亮/禁用灰，命中测试
  MouseClick），剩余宽度放**原生 TextBox**（输入法/光标/全选白送）：
  `BorderStyle=None`、`BackColor`/`ForeColor` 随主题、`ShortcutKeys` 无、Enter/Esc
  走 `KeyDown`。左端小锁字形 🔒 仅装饰（P0）。
- 按钮点击/地址栏回车 → 调 `api` 桥方法（见 3.3），**不直接碰控件池**。
- 提供 `set_scheme(raw_json)`：复用标签栏 `ui_bg_scheme` 的解析规则（bar/active/hover/
  fgon/fgoff/sep），同步 Panel、按钮、TextBox 配色。
- 事件订阅纪律（踩坑记录约束）：全部走事件订阅（Paint/MouseClick/MouseMove/KeyDown），
  不重写虚方法；Pen/Brush 等在文件顶部 import；`pythonnet` 回调里不得假设控件仍存活。

### 3.2 `shell_pages.py`（控件池）

- `create()` 的 `on_ready` 里给每个控件（含 0 号主控件补挂）订阅：
  - `core.HistoryChanged` + `core.SourceChanged`（`NavigationStarting` 兜底）→ 组装
    `(tab_key, str(c.Source), core.CanGoBack, core.CanGoForward)` 调 `on_nav` 回调；
  - 回调里判 `tab_key in _dead` / 标签表已无 → 直接丢弃（迟到事件防脏推）。
- 池新增公开方法（均 UI 线程安全，内部 `_ui` marshal）：
  - `get_nav(key) -> (url, can_back, can_fwd)`：同步读当前控件状态（切标签时补推用；
    控件未就绪返回 `(None, False, False)`）；
  - `reload(key)`：`core.Reload()`；
  - `goto(key, url)`：`c.Source = Uri(url)`（在当前标签内导航）；
  - `navigate_history(key, back)`：已有，复用。
- **顺手修存量 bug**：`_dbg_log` 引用了未定义的 `NL`（第 152 行）→ `NameError` 被
  `except` 吞掉，`shell_debug.log` 从未写入过。模块顶部补 `NL = "\n"`。
- 0 号主控件（信息流）在 `init_ui` 补挂同一套导航事件订阅（主控件不是 create 建的）。

### 3.3 `app_window.py`（ShellApi 接线）

- 新桥方法（JS 桥不可达也行 —— 主要给 navbar 回调用，但保持可测的纯逻辑）：
  - `nav_back()` / `nav_forward()`：当前**激活**标签 `navigate_history`；首页标签同样
    按历史走（无历史无事发生 —— 与侧键语义不同，侧键路径不动）；
  - `nav_reload()`：激活标签 `reload`；
  - `nav_goto(url)`：激活标签 `goto`；只接受补协议后匹配 `^https?://` 的 URL
    （安全约束与 `open_in_app` 相同），拒绝时返回 `"bad-url"`。
- 导航状态 → 工具栏：`ShellApi._on_nav(tab_key, url, can_back, can_fwd)`：
  - `tab_key != self._active` → 忽略（后台标签的导航不刷地址栏）；
  - 否则 `navbar_update(url, can_back, can_fwd)`。
- 切换标签（`_activate` / `tab_switch` / `tab_close` 后的激活路径）末尾调
  `pages.get_nav(active)` 强制推一次 —— 解决"切标签地址栏显示旧 URL"。
- `bind_navbar(update_fn)` 注入重绘回调（与 `bind_strip` 同构）。
- `shell_main` 的 `gui_ready`：先 `install_strip`（返回内容区高度），再
  `install_navbar(top=strip高, ...)`，控件池 `init()` 在两者之后（内容区 y 偏移已定）。
  顺序约束：**内容区下移量 = 标签栏高 + 工具栏高，必须唯一来源** —— 从
  `install_strip` / `install_navbar` 的返回值取，不得在两处各写一份常量。

### 3.4 `shell_tabstrip.py`（内容区下移）

- `_install` 里主控件 `Location.Y` 从 `bar_h` 改为 `bar_h + nav_h`；`nav_h` 由
  `install_strip` 的调用方（app_window）传入或由 `shell_navbar.NAV_H(scale)` 计算，
  保证只算一次。
- 标签栏自身绘制/命中/拖动逻辑零改动。

### 3.5 测试

- `test_shell.py` 新增（逻辑层，不起 GUI）：
  - `nav_goto` URL 白名单：`https://x` ✓、`x.com`（补协议后）✓、`file:///C:/` ✗、
    `javascript:...` ✗、空串 ✗；
  - `_on_nav`：非激活标签事件被忽略、激活标签事件转发给 update 回调；
  - 切换标签强制推一次 nav 状态（fake pool 记录调用）。
- 回归：`test_smoke.py`、既有 `test_shell.py` 全绿；`python app.py` 行为不变。

## 4. 任务分解

### P0 · 本次必须
- [x] 备份：git 备份点 26639b8 + `desktop/backups/*.tar.gz` 快照
- [x] GOAL 文档（本文）
- [x] `shell_pages.py`：修 `NL` 未定义；HistoryChanged/SourceChanged 订阅；
      `get_nav` / `push_nav` / `reload` / `goto`；0 号主控件补挂事件
- [x] `shell_navbar.py`：工具栏 UI（按钮三连 + 地址栏 + 主题配色 + 悬停/禁用态）
- [x] `shell_tabstrip.py`：内容区下移（唯一高度来源）
- [x] `app_window.py`：`nav_back/forward/reload/goto` 桥 + `_on_nav` 接线 +
      切标签强制推状态 + `bind_navbar`
- [x] `test_shell.py`：新增导航逻辑用例 + 全量回归（60 checks ALL_PASS）
- [x] 版本号 → v0.3.1.0dsk（app.py `APP_VER` / web/index.html 页脚 / spec 文件）

### P1 · 交付形态
- [x] E2E 验证（`_e2e_shell.py`）：主窗口出现/唯一窗口/结构断言 ALL_PASS；
      控件几何 dump 确认 标签栏(48px)→工具栏(Y=48,h=51)→内容区(Y=99) 150%DPI 正确。
      截图因用户正使用电脑被脚本自动跳过（设计如此，不抢焦点）
- [ ] 实测回归：无边框拖动/缩放/窗口化全屏/鼠标侧键/主题切换/标签页零重载（用户实际使用确认）
- [x] `desktop/README.md` 增补导航工具栏一节
- [x] git 提交（功能完成点）

### P2 · 打磨（时间允许，可顺延）
- [ ] 加载中 `⟳ → ✕`（停止按钮，`NavigationCompleted` 驱动）
- [ ] 地址栏下拉历史建议
- [ ] 站点图标（favicon）显示在地址栏左端

## 5. 风险与降级

| 风险 | 缓解 |
|---|---|
| pythonnet 订阅 `HistoryChanged` 事件签名异常 | 退路：池内 0.5s 轮询激活控件的 Source/CanGoBack（逻辑等价，代价可忽略） |
| 内容区 y 偏移只改了一处（create 按主控件 Location 摆放子控件会自动跟随，但主控件 y 必须一次改对） | 高度唯一来源（3.3 顺序约束），改后 dump_controls 验证 |
| 多个 Dock=Top 控件 z 序不可控 | 工具栏不用 Dock，手动定位 + Anchor 四向（Top|Left|Right） |
| TextBox 白底刺眼/深色下闪白 | BackColor/ForeColor 随主题即时设置；Parent.BackColor 同步 |
| 工具栏抢键盘焦点（切标签后焦点落在地址栏） | navbar 不调 Focus()；切标签路径维持现有 `c.Focus()` |
| 切标签后地址栏显示旧标签 URL | 激活路径强制 `get_nav` 补推一次 |
| 迟到的导航事件（标签已关）推脏状态 | `_dead` 集合 + 标签表双重校验后丢弃 |
| 地址栏输入非 http(s)（file:/javascript:） | 补协议后正则白名单，拒绝即还原显示值（安全约束同 open_in_app） |
| 替换静默失败（踩坑三次的教训） | 每轮改动后 grep 验证 + `py_compile` + 运行验证 |

## 6. 验收标准（全部满足才算完成）

1. 首页与任意内置浏览器标签页的标签栏下方都有导航工具栏，网页内容零遮挡。
2. 地址栏实时反映激活标签 URL：标签内跳转、切标签、站内弹页（转同标签导航）均正确。
3. `← →` 按钮按 CanGoBack/CanGoForward 启停，点击执行；`⟳` 重载当前页。
4. 地址栏输入 `bilibili.com` 回车 → 当前标签导航到 `https://bilibili.com`；
   输入 `file:///C:/` 回车 → 不导航、显示值还原。
5. 主题/强调色实时跟随设置；悬停高亮；禁用灰不可点。
6. 拖动窗口只能从标签栏；边缘缩放/窗口化全屏/侧键/标签页零重载全部不回归。
7. `test_smoke.py` + `test_shell.py` 全绿；`python app.py` 与降级路径行为不变。
8. `构建exe.bat` 构建成功，exe 实测验收 1-6。

## 7. 明确不做（范围外）

- 安卓端（`android/` 不动）；
- 书签/下载管理/多标签地址栏历史下拉；
- 地址栏搜索fallback（输入非 URL 文字当搜索词）——P2 后再议。
