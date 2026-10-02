# GOAL · 洞鉴虚明桌面版 v0.2.9 —— 内置浏览器（不再跳外部浏览器）

> 分支：`desktop-embedded-browser` · 起始版本 v0.2.8.5a · 目标版本 **v0.2.9.0a**
> 本文是本分支的总目标与任务分解，做一项勾一项；全部勾完即达成目标。

---

## 0. 一句话目标

**让桌面端成为真正的"桌面软件"：双击后弹出自带的应用窗口，点feed卡片/消息/账号里的任何
视频或帖子链接，都在本应用弹出的内置浏览器窗口里打开，全程不拉起系统默认浏览器。**

## 1. 背景与问题

现状（v0.2.8.x）：桌面端 = 纯标准库本地服务（`app.py`，HTTP 127.0.0.1:8000）+
外部默认浏览器打开页面。所有跳转（B站视频、贴吧帖子、小黑盒、消息中心、B站主页）都通过
`window.open(url, "_blank")` 落到**系统浏览器**：

- 主界面不在应用窗口里，像"开了个网页"而不是"打开了一个软件"；
- 点一个视频就切走/新开浏览器标签，与主界面割裂，多屏来回切很烦；
- 无法控制跳转窗口的外观与生命周期（关软件后浏览器标签还挂着）。

## 2. 技术选型（开源调研，已实测验证 ✅）

要求：内嵌完整现代浏览器内核（要能放 B 站视频）、能被 PyInstaller 打成单文件 exe、
Python 3.14 兼容。调研结论：

| 方案 | 结论 |
|---|---|
| **pywebview**（GitHub `r0x0r/pywebview`，MIT） | **✅ 采用**。Windows 走系统自带的 **WebView2（Edge Chromium）** 运行时；自带 PyInstaller hook（自动收集 WebView2 全套 DLL）；JS⇄Python 双向桥、运行时多窗口、evaluate_js 注入均可用 |
| PySide6 + QtWebEngine | 备选。自带 Chromium 但体积 +150MB+，放弃 |
| cefpython | 停止维护，无 py3.14 支持，放弃 |
| `msedge --app=URL` 模式 | 仍是外部浏览器进程，不符合"内置"，放弃 |

**本机尖刺实测（2026-10-02，Win10 19045 + Python 3.14.7 + pywebview 6.2.1/pythonnet 3.2.0）**：

1. WebView2 窗口打开成功，内核 `Chrome/154 + Edg/154`；
2. js_api 桥：本地页面与**远程页面（bilibili.com）**都能回调 Python（`BAR_BTN_OK: remote-test`）；
3. `evaluate_js` 可向远程页注入悬浮工具条（幂等注入成功）；
4. 运行中可随时 `create_window` 弹出第二个内置浏览器窗口（子窗口标题/URL 正确）；
5. WebView2 对 B 站 H5 播放器/登录页渲染正常；
6. 已知限制：页面内 JS `window.close()` 被内核禁止 → 关窗按钮必须走桥回调 Python `destroy()`（已验证可行）。

## 3. 目标体验（用户视角）

1. 双击 `启动.bat` 或 `dist\洞鉴虚明v0.2.9.0a.exe` → **弹出应用主窗口**（非浏览器）。
2. 点 feed 卡片 / "打开原帖" / 消息中心 / B站账号页 / 大图模式跳转 → **弹出内置浏览器窗口**
   展示该视频/帖子，窗口右上角有一个半透明迷你工具条：
   `← 后退 · ⟳ 刷新 · ⧉ 用系统浏览器打开 · ✕ 关闭`（悬停时变清晰）。
3. 内置窗口里可以继续点站内链接（同一窗口内跳转），登录 B 站等操作照常可用。
4. 关闭全部窗口 → 应用进程退出、悬浮球收走（与现在"关闭服务"行为一致）。
5. **零降级死角**：未装 WebView2 运行时/库缺失的机器 → 自动回退到"外部浏览器模式"
   （现行为），弹一次提示，绝不白屏。
6. 兼容性不变：CLI `python app.py` 纯服务模式保留；二次双击检测到服务已运行时只弹新窗口。

## 4. 架构设计

```
┌─ app_window.py(新,窗口壳,exe 入口) ─────────────────────────┐
│ 1. import app → app.run_server(auto_open=False) 起服务(线程) │
│ 2. 检测 WebView2 运行时(注册表) → 缺失则回退外部浏览器模式     │
│ 3. pywebview 主窗口(应用 UI) + ShellApi(js_api 桥)           │
│ 4. 全部窗口关闭 → app.shutdown() 收尾(悬浮球/线程池/DB)       │
└──────────────────────────────────────────────────────────────┘
        │ window.pywebview.api.*                    ▲
        ▼                                           │ evaluate_js 注入
┌─ web/app.js ─────────────┐   ┌─ 内置浏览器子窗口 ──────────────┐
│ openExternal(url):       │   │ 直接导航到视频/帖子 URL          │
│   有壳 → api.open_in_app │ → │ + 注入迷你工具条(← ⟳ ⧉ ✕)      │
│   无壳 → window.open(降级)│   │ ⧉ → api.open_external(系统浏览器)│
└──────────────────────────┘   └────────────────────────────────┘
```

模块职责：

- **`app.py`（改动最小化）**：把 `__main__` 里的服务启动序列抽成 `run_server(auto_open=False) -> (server, port)`
  与 `shutdown(server)`（收尾：serve 关闭、悬浮球、线程池、tb 登录）；CLI 行为逐字不变。
  悬浮球内证据链接维持 `webbrowser.open`（tkinter 独立进程无法共享 WebView2，属辅助场景）。
- **`app_window.py`（新）**：窗口壳与 exe 入口。职责：起/复用服务、WebView2 检测、
  主窗口、ShellApi 桥（`open_in_app` / `open_external` / `shell_info`）、
  子窗口创建 + 工具条注入、退出收尾、降级路径。
- **`web/app.js`**：新增 `openExternal(url)` 统一出口（探测 `window.pywebview.api`，
  有壳走桥，无壳原样 `window.open`）；替换全部 10 处 `window.open` 调用点。
- **`启动.bat` / `构建exe.bat` / spec**：入口换成 `app_window.py`；新增 `启动-服务模式.bat`。

安全约束：ShellApi 只接受 `http(s)://` URL（页面侧只能让它开网页窗口，无法触发本地文件/命令）。

## 5. 任务分解

### P0 · 地基（本次必须）
- [x] 尖刺验证：pywebview 窗口 / 桥 / 多窗口 / 远程站 / 打包 hook（见 §2）
- [x] 建分支 `desktop-embedded-browser`，快照 v0.2.8.5a 工作区
- [x] GOAL 文档（本文）
- [ ] `app.py`：抽 `run_server()` / `shutdown()`，`__main__` 行为不变
- [ ] `app_window.py`：主窗口 + ShellApi + 服务线程 + 退出收尾 + 降级
- [ ] `app.js`：`openExternal()` 统一出口，替换全部 `window.open` 调用点
- [ ] 内置浏览器子窗口 + 迷你工具条注入（← ⟳ ⧉ ✕）
- [ ] 冒烟测试 `test_shell.py`（逻辑层，不起 GUI）

### P1 · 交付形态
- [ ] 版本号 → v0.2.9.0a（footer / app.py / spec）
- [ ] `启动.bat` 走窗口壳；新增 `启动-服务模式.bat`；`构建exe.bat` 换入口
- [ ] 打包 exe 并实测：窗口弹出、链接跳内置窗口、关窗退出、悬浮球正常
- [ ] E2E 截图验证：主窗口渲染 / 卡片点击弹内置视频窗口 / 工具条可见
- [ ] `desktop/README.md` 更新（内置浏览器章节 + 快速启动 + 故障排查）

### P2 · 打磨（时间允许）
- [ ] WebView2 非无痕模式（内置窗口登录态跨重启保留），数据目录落 `%LOCALAPPDATA%`
- [ ] 内置窗口标题跟随页面标题、图标使用应用 ico
- [ ] 下载兜底：站内触发的下载走 `api.open_external` 提示
- [ ] 悬浮球证据链接也走内置窗口（跨进程方案调研，可延后）

## 6. 风险与降级

| 风险 | 缓解 |
|---|---|
| 目标机器无 WebView2 运行时 | 启动时注册表检测 → 提示 + 自动回退外部浏览器模式（现行为，不白屏） |
| pythonnet/PyInstaller 打包踩坑 | hook 已自带；实测打包，失败则 hiddenimports/`--collect-all webview` 兜底 |
| B站等站点内核策略变化（DRM/UA） | WebView2 即 Edge 内核，与用户日常浏览器同源；必要时补 UA/`--disable` 开关 |
| 前端在非壳环境（安卓壳/普通浏览器）误走桥 | 桥探测仅 `window.pywebview` 存在时生效，安卓壳路径不受影响 |
| 二次启动端口占用 | 沿用 `port_busy` 探测：只弹窗口指向已运行服务，不重复起服务 |

## 7. 验收标准（全部满足才算完成）

1. `python desktop/app_window.py`（或双击 `启动.bat`）弹出主窗口，桌面上没有新的浏览器窗口/标签。
2. 点击 feed 卡片与全部既有跳转入口（消息/账号/大图/原帖）→ 均为应用弹出的内置窗口。
3. 内置窗口有迷你工具条，后退/刷新/系统浏览器打开/关闭四项全部可用。
4. 关闭所有窗口 → 进程退出、悬浮球消失、无孤儿进程。
5. `python app.py` 与 `--float-ball` 模式行为与 v0.2.8.5a 完全一致（回归）。
6. `test_smoke.py` 全绿 + 新增 `test_shell.py` 全绿。
7. exe 构建成功且实测通过验收 1-4。
8. 无 WebView2 环境回退路径可用（模拟：禁用桥 → 走外部浏览器）。

## 8. 明确不做（范围外）

- 安卓端壳改造（`android/` 本分支不动）；
- 悬浮球改成 WebView 渲染；
- 视频离线下载/yt-dlp 集成（可作为后续版本主题）;
- 多标签页式内置浏览器（一期:一链接一窗口，够用且最稳）。
