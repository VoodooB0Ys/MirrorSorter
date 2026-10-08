# GOAL · v0.3.1.7dsk —— 整屏截屏黑屏根治（残留概率归零）

> 目标版本 **v0.3.1.7dsk**。上一任务（SR截屏黑屏首修/原生UI闪现/夜间白闪）为 v0.3.1.6dsk（commit 5e7918a）。

## 一句话目标
v0.3.1.6 的双保险（视频保持可见 + 画布 0.5px 圆角）把「微信/QQ/PrntScr 整屏截屏视频区域黑屏」的概率**大幅压低但没归零**——用户实测仍偶发。本任务把剩下的提升路径全部堵死。

## 根因分析（为什么还剩黑屏）
- **路径①（已堵）**：SR 画布 = 不透明+轴对齐+无滤镜简单四边形 → Chromium 提升为 DC overlay → GDI 取不到。v0.3.1.6 用 0.5px 圆角破坏资格。
- **路径②（本次主堵）**：SR **未激活/拆管窗口期**（激活前首帧前、管线停滞 teardown 后、换 video 元素间隙），原视频**可见** → 可见视频本身就是 DC 视频 overlay 的提升候选 → GDI 截屏恰好取不到 overlay 平面 → 黑。v0.3.1.6 把视频从 opacity:0 改为可见，恰好把这扇门打开了。
- **路径③（兜底）**：画布 radius CSS 无 `!important`，理论上可被站点 CSS 覆盖。

## 修复设计（三层）
1. **浏览器级（决定性）**：WebView2 启动参数 `--disable-direct-composition-video-overlays`——从引擎层面禁止视频 overlay 平面，GDI 截屏必取到合成画面。
   - `app_window.py`：进程早期 `os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", ...)`（覆盖 pywebview 创建的主控件；setdefault 允许用户环境变量覆盖）。
   - `shell_pages.py`：新标签页 `CoreWebView2CreationProperties.AdditionalBrowserArguments` 显式追加同一开关（防止属性值覆盖环境变量的语义差异踩坑）。
   - 代价：视频合成多走一点 GPU 主通道，省电收益略降——桌面应用可接受。
2. **页内视频（兜底）**：`bilibili-sr.user.js` 给 `video:has(+ canvas.msx-sr-canvas)` 也加 `border-radius:0.5px!important`——即使开关失效（旧运行时等），拆管窗口期视频也不具备 overlay 资格；画布 radius 补 `!important`。
3. **取证强化**：`_probe_header.js` 探针加 `videoRadius`；`_sr_cap.py` 从 2 发改为**连发多帧 + 黑屏自动判读**（统计截屏播放器区域近黑像素占比），量化"偶发"。

## 技术落点（按文件）
- `app_window.py`：导入后立即 setdefault 环境变量（注释：为何、setdefault 语义）。
- `shell_pages.py`：`create()` 里 props.AdditionalBrowserArguments 追加开关。
- `extensions/bilibili-sr.user.js`：`ensureStyle()` 两条 radius 规则（都 !important）+ 注释更新（路径②）。
- `extensions/_probe_header.js`：sr 段加 `videoRadius`、`videoDisp`。
- `_sr_cap.py`：默认连发 8 帧、每帧间隔 5s；对每帧按探针回传的画布矩形裁剪判黑（粗映射：窗口宽/视口宽比例），打印 per-frame 黑占比表；帧图全部落盘。
- 版本号 → **v0.3.1.7dsk**（app.py `APP_VER` / web/index.html 页脚 / test_shell.py 断言）。

## 验收标准
- py_compile + node --check 全过；test_shell 全过；test_smoke 无新增 FAIL。
- 实机 `_sr_cap.py`：探针报 SR 激活 + canvas radius/videoRadius = 0.5px；≥8 帧 BitBlt 截屏播放器区域全部非黑。
- 不动超分管线渲染路径（画面、帧率零变化）；ownui/主题胶水零接触。
