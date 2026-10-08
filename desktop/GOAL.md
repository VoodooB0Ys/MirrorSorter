# GOAL · 洞鉴虚明桌面版 v0.3.2 —— B站播放页自有 UI（评论/推荐全替换）

> 分支：`desktop-embedded-browser`（延续） · 起始版本 v0.3.1.0dsk（git 87aa37f）
> 目标版本 **v0.3.2.0dsk**。上一任务（导航工具栏）见 `GOAL_v0.3.1.md`。
> 用户已选定：**方案 A · 注入重排**（真页面 + Shadow DOM 自建 UI）+ **可写完整版**（可点赞/评论/回复）。

---

## 0. 一句话目标

**拦截 B站视频播放页：播放器与其上方的原生工具条（三连/分享/弹幕）原封不动，
评论区与右侧"相关推荐"整块替换为我们自己的 UI（Shadow DOM 自绘，数据走 B站官方
web API + 壳内登录态）—— 评论区的广告/引流/垃圾信息从此绝迹，且超分管线零改动。**

## 1. 需求（用户原话）

"将B站播放页全数拦截，完全改为我们自己的ui（整体上保持B站原生ui，视频播放也是使用
B站直接获取到的api，但是评论、右侧视频推荐、全改为我们自己的ui，这样能避免所有的
广告和垃圾信息。……视频播放记得不要破坏现在的超分辨率管线"

方案讨论结论（2026-10-08）：
- **方案 A 注入重排（采纳）**：加载真 B站页 → 注入脚本移除原生评论/右栏，原位挂
  Shadow DOM 自建面板。播放器不动 → 超分/弹幕/清晰度/大会员画质全保留。
- 方案 B（iframe 分享播放器）否决：清晰度锁 1080p、超分管线必坏、CSP 可能禁嵌。
- 方案 C（全自建播放器）否决：工程量巨大且违背"保持原生播放器"。
- 交互深度：**可写完整版** —— 评论列表/子回复 + 点赞 + 发评论 + 回复（带 csrf）。

## 2. 现有管线（绝不能破坏的约束）

| 管线 | 机制 | 与本任务的关系 |
|---|---|---|
| AI超分 | `AddScriptToExecuteOnDocumentCreated` 注入 `anime4k-webgpu.js` + `bilibili-sr.user.js`，胶水锚定 `#bilibili-player video` / `.bpx-player-container`，检查弹幕层 z-index | **不碰播放器任何 DOM**；只在播放器以外的区域动手 |
| 画质增强 | 同通道注入 `bilibili-enhance.user.js`（SVG 滤镜） | 同上 |
| 净化大师 | 默认禁用（MSX_CLEANER=1 才开） | 不启用；自有 UI 本身就是净化 |
| 深色主题 | CookieManager 写 `theme_style=dark` cookie + PreferredColorScheme；其他站反色兜底 | 自有 UI 读同一 cookie 跟随 B站原生深色 |
| 登录态 | WebView2 共享用户数据目录，B站 cookie（SESSDATA/bili_jct）在页面上下文可直接用 | 评论写接口带 `csrf=bili_jct` 即可，无需壳参与 |

## 3. 设计（方案 A 落地）

### 3.1 注入通道与开关

- 新文件 **`extensions/bilibili-ownui.user.js`**，由 `shell_pages._build_inject_script()`
  按现有模式拼入（与 sponsorblock/enhance 同通道，document-start，MAIN 上下文）。
- 开关：服务端设置键 **`ui_ownui`**（默认 `"1"` 开）。注入时以
  `window.__MSX_OWNUI__ = 0/1` 传给页面脚本，脚本入口直接退出实现关断。
  设置页新增「播放页自有UI」行（关/开，紧跟画质增强设置之后），走
  `/api/settings` 白名单（app.py 三处：GET /api/settings、POST 白名单、
  /api/ui_settings 轮询键组）。
- 生效范围：仅 `www.bilibili.com/video/*`（BV 视频页）。其他页/番剧/直播一律不激活。

### 3.2 页面改造点（选择器宽匹配，全部有降级）

| 区域 | 原生 DOM | 处置 | 自有 UI 挂载 |
|---|---|---|---|
| 右侧栏 | `.right-container`（内含相关推荐/更多推荐/广告卡） | 内层 `.right-container-inner, .video-info-container` 等子块 display:none（容器保留 → 宽度/位置自动继承） | 同容器内 append `msx-ownui-related` host → Shadow DOM：UP主卡片 + 相关视频列表 |
| 评论区 | `#comment` / `.comment-container`（懒加载） | display:none + MutationObserver 防复活 | 同父级 append `msx-ownui-comments` host → Shadow DOM：完整评论区 |
| 不动 | `.left-container` 里的播放器、视频信息标题栏、三连工具条、顶栏 | 原样 | — |

- 失败降级：任一选择器 15s 内找不到 → 静默放弃该区域（原生页面照旧），绝不让页面白屏。
- 整个脚本 try/catch 包裹；`__MSX_OWNUI__=0` 或非视频页 → 入口即 return。

### 3.3 数据 API（页面上下文 fetch，cookie 自动携带）

| 用途 | 接口 | 备注 |
|---|---|---|
| 视频元信息 | `window.__INITIAL_STATE__`（aid/bvid/videoData.owner/desc） | 缺失时兜底 `/x/web-interface/view?bvid=` |
| 相关推荐 | GET `/x/web-interface/archive/related?bvid=` | 列表：标题/封面@200w_112h/时长/播放/UP主 |
| UP主粉丝 | GET `/x/relation/stat?vmid=<mid>` | UP卡展示 |
| 评论列表 | GET `/x/v2/reply?type=1&oid=<aid>&pn=&ps=20&sort=` | sort: 2=时间 3=热度；页码分页 |
| 子回复 | GET `/x/v2/reply/reply?type=1&oid=&root=<rpid>&pn=` | 懒展开，>3条折叠 |
| 发表评论 | POST `/x/v2/reply/add`（oid/type=1/message/csrf=bili_jct/plat=1） | 登录态校验：cookie SESSDATA |
| 回复 | POST `/x/v2/reply/add`（+root/parent=rpid） | 同上 |
| 评论点赞 | POST `/x/v2/reply/action`（oid/rpid/type=1/action=1|0/csrf） | 乐观更新+失败回滚 |

- 风控约束：写操作节流（连点防抖）、失败把 `code/message` 原样显示在面板内；
  接口若升级要求 wbi 签名（返回 -403 等）→ 面板顶部给出"接口受限，已降级只读"提示，
  绝不反复重试轰炸。

### 3.4 自有 UI 设计（Shadow DOM，样式隔离）

- 主题：读 `document.cookie` 的 `theme_style=dark` → 深色调色板（跟随壳内 B站原生
  深色）；否则浅色。若检测到页面被壳的反色兜底接管（html filter: invert），
  不做额外处理（反色会连带作用于本面板，视觉一致）。
- 结构（两面板均 Shadow DOM）：
  - **UP主卡 + 相关推荐**（右栏）：头像/名字/粉丝数、投稿数徽标；视频条目 =
    封面+时长角标+标题两行+UP主+播放/弹幕数；点击 → `location.href=/video/<bvid>`
    （整页导航，document-start 脚本自动重跑，与现有点卡片路径一致）。
  - **评论区**（左栏底部）：顶栏（评论数 + 排序切换 热度/时间）+ 发表框（登录时：
    头像+textarea+发表按钮；未登录：提示"登录后可评论"）+ 评论列表（头像/名字/
    内容/时间/点赞/回复按钮，UP主徽标，置顶评论，折叠子回复"共N条 回复"展开）。
- 文案安全：所有插入内容经 escapeHTML；表情 emote 走 API 返回的 emote 表替换为图。
- 交互反馈：发表/点赞按钮防抖 + 失败 toast（面板内条，不用 alert）。
- SPA 路由：B站视频页是 SPA —— 轮询 `location.pathname`（800ms）检测 BV 变化，
  teardown（移除自有 host、恢复原生显隐）后重建；原生相关推荐点击产生的路由变化同样覆盖。
- 尺寸自适应：面板 100% 宽、随窗口缩放；右栏宽度继承原生容器。

### 3.5 明确不碰

- 播放器/弹幕/清晰度/三连工具条 DOM —— 超分管线红线；
- `android/`（安卓端不动）；
- 顶栏/首页/搜索页（非视频页一律不激活）；
- 直播/番剧/课程页。

## 4. 任务分解

### P0 · 本次必须
- [x] 方案讨论与用户确认（本文 §1）
- [x] GOAL 文档（本文）
- [x] `extensions/bilibili-ownui.user.js`：入口门控 + 主题检测 + API 层 +
      右栏（UP卡+相关推荐）+ 评论区（列表/子回复/发评/回复/点赞）+ SPA 路由 watcher
- [x] `shell_pages.py`：`_build_inject_script` 接线（`ui_ownui` 门控 → `__MSX_OWNUI__`）
- [x] `app.py`：`ui_ownui` 进三个设置白名单（GET settings / POST 白名单 / ui_settings 轮询）
- [x] `web/index.html` + `web/app.js`：设置页「播放页自有UI」关/开行 + 回显 + 保存
- [x] `test_shell.py`：静态接线用例（文件存在/API 关键字/白名单/设置行）+ 全量回归
- [x] 版本号 → v0.3.2.0dsk（app.py / index.html 页脚 / spec）

### P1 · 交付形态
- [ ] 实测：壳内打开 B站视频页 —— 播放器正常、超分正常（切标签回来续播不受影响）、
      评论区/右栏为自有 UI、深浅主题跟随
- [ ] 实测写操作：发表评论/回复/点赞在已登录账号下成功
- [ ] 降级实测：`ui_ownui=0` 时页面恢复原生
- [ ] `desktop/README.md` 增补一节；git 提交

### P2 · 打磨（可顺延）
- [ ] 评论图片/表情完整渲染、评论区分享链接识别
- [ ] 相关推荐"换一换"/无限滚动
- [ ] UP主卡片"关注/已关注"状态（relation API）
- [ ] 自有 UI 的设置项下放（每标签开关按钮）

## 5. 风险与降级

| 风险 | 缓解 |
|---|---|
| B站改版选择器失效 | 宽匹配多候选选择器 + 15s 找不到即静默放弃（原生页面兜底）+ 失败落 shell_debug.log |
| Vue 复活被隐藏的节点（评论懒加载组件重挂） | MutationObserver 维持 display:none（幂等，不删节点不跟 Vue 打架） |
| 评论/写接口升级要求 wbi 签名 | 检测 -403/负码 → 面板一次性提示"接口受限已降级"，不重试轰炸；后续再接 wbi |
| 写操作触发风控 | 防抖节流、错误原样透出、无自动重试 |
| 与超分/画质增强脚本冲突 | 本脚本只在播放器以外的容器操作；全部 try/catch；注入顺序互不依赖 |
| 反色兜底站点配色错乱 | B站走原生深色 cookie（本就如此），面板读 cookie 配色；反色模式下接受连带反色 |
| SPA 路由切换面板残留/重复挂载 | 路由 watcher 统一 teardown→rebuild；host 带 id 幂等去重 |
| 替换静默失败（踩坑教训） | 每轮改动 grep 验证 + py_compile + test_shell 全绿 |

## 6. 验收标准

1. 壳内打开任意 BV 视频页：播放器/弹幕/三连工具条为原生，评论区与右侧栏为自有 UI，
   原生评论区/右栏内容（含其中广告）不可见。
2. 相关推荐列表可点击跳转（新 BV 生效、自有 UI 重建）。
3. 已登录：发评论/回复/点赞成功且面板即时反映；未登录：只读+登录提示。
4. 深浅主题跟随 B站原生深色；样式与页面不互相污染（Shadow DOM）。
5. 超分管线回归：`ui_sr` 开启时画面重建正常、弹幕层在画布之上、切标签续播不受影响。
6. `ui_ownui=0`（设置页关闭）→ 新标签页恢复原生页面。
7. `test_smoke.py` + `test_shell.py` 全绿；`python app.py` 行为不变。

## 7. 明确不做（范围外）

- 安卓端、直播/番剧/首页改造、原生三连工具条替换、播放器换皮。
