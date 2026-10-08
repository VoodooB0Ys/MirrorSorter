# GOAL · v0.3.1.4dsk —— 播放页推荐筛选 + 顶栏精简

> 目标版本 **v0.3.1.4dsk**。上一任务（播放页自有UI）见 `GOAL_v0.3.1.1.md`。

## 一句话目标
在「机器筛选 → B站」里新增开关「是否将播放页推荐视频进行筛选」（默认开）：开启后，播放页右栏推荐列表按本应用的机器筛选规则过滤，并按 **相关:探索 = 7:3** 混排（探索=与当前内容无关的泛热门视频）；同时播放页顶栏**只去掉左侧频道导航**（首页/番剧/直播/游戏中心/会员购/漫画/赛事/下载客户端），搜索框与右侧用户区保留。

## 体验设计（逐条可验收）
1. **设置入口**：机器筛选弹窗 → B站 标签页，新增复选框「是否将播放页推荐视频进行筛选」，默认勾选，随「保存 B站筛选设置」持久化（键 `m_play_related`，"1"/"0"）。
2. **关闭开关时**：播放页右栏维持现状 —— 直接展示 `archive/related` 原始列表，不过滤、不混排。
3. **开启时（默认）**：相关推荐与探索池都过一遍与信息流同源的机器筛选规则（页面端 1:1 移植，规则由壳注入 `window.__MSX_MF__`）：
   - 播放量上下限（`m_min_views` / `m_max_views`）
   - 时长上下限（`m_max_duration` / `m_min_duration`）
   - 时间窗（`m_max_age_months`，0=不限）
   - 相似标题去重（归一化后相同或互相包含，保留播放量最高的一条）
   - 情绪过滤器（`m_emotion`）：感叹号/问号轰炸、叠词轰炸、引战黑话（FLAME_SLURS）、标题党词表（CLICKBAIT_KWS）+ 句式正则（CLICKBAIT_RE）—— 与 app.py `_emo_bad` 同规则
   - 厌恶备注关键词（`dislike_keywords()`，查标题+简介）
   - 拉黑名单作者（`ablock` 表，`m_blocklist_sync` 开启时）
4. **7:3 混排**：探索池来自 `x/web-interface/popular`（同样过滤 + 去掉与当前视频/相关列表重复的条目），按「每 7 条相关后插 3 条探索」交错排列；探索条目带「探索」小标；相关区标题下注明「已按机器筛选 · 相关:探索=7:3」。
5. **顶栏精简**：视频页（ownui 激活范围）`.bili-header .left-entry` 隐藏 —— 左侧频道导航消失，**搜索框、头像/消息/动态/收藏/历史/创作中心/投稿全部保留**；只动 CSS，不碰播放器 DOM（超分管线红线不变）。
6. **降级**：popular 接口失败 → 只显示过滤后的相关列表（不空屏）；规则对象缺失/异常 → 回退现状。
7. **超分管线**：本任务零接触播放器/三连/弹幕 DOM（test_shell 继续硬检查 ownui 脚本不含播放器选择器）。

## 技术落点（按文件）
- `app.py`：`DEFAULT_SETTINGS` 加 `m_play_related="1"`；`/api/machine` GET keys 与 POST keys 各加一个键。
- `shell_pages.py`：ownui 注入段前拼 `window.__MSX_MF__ = {...}`（JSON，ensure_ascii），字段 on/min_views/max_views/max_dur/min_dur/max_age/dedup/emotion/flame/cb/cbre/dkws/ablock；任何读取异常时注入 `null`（页面端回退）。
- `extensions/bilibili-ownui.user.js`：①脚本激活即注入隐藏 `.left-entry` 的 `<style>`；②`mfFilter()` 移植规则；③`buildRelated()` 走 过滤+popular+7:3 交错；④探索条目「探索」标。
- `web/index.html`：B站筛选组加 `mPlayRel` 复选框行 + tip。
- `web/app.js`：`loadMachine` 回显 + `mSave` 提交 `m_play_related`。
- `test_shell.py`：版本断言 → v0.3.1.4dsk；新增静态检查（设置键入 API 白名单、userscript 含 left-entry 隐藏/popular/7:3 逻辑、index.html 有 mPlayRel、app.js 接线）。
- 版本号 → **v0.3.1.4dsk**（app.py `APP_VER` / web/index.html 页脚 / spec 复制改名）。

## 验收标准
- py_compile + node --check 全过；test_shell 全过；test_smoke 无新增 FAIL。
- 实测：开关关闭 → 原始相关列表；开启 → 列表出现「探索」条目且比例约 7:3；顶栏左导航消失、搜索和用户区完好；超分画面无任何变化。
