# GOAL · v0.3.1.8dsk —— 整屏截屏黑屏「真因定位并根治」

> 目标版本 **v0.3.1.8dsk**。上一任务（SR截屏黑屏残留）为 v0.3.1.7dsk（commit 4f15fc5）。

## 一句话目标
用户提出两条反证：**①不开超分也黑；②全屏（软件全屏）时更明显**。据此推翻"这是超分画布问题"的旧判断，改用受控实验定位真因，一次修对。

## 真因（本轮实证定位，非推测）
Chromium 经 **DirectComposition（DComp）** 把视频层作为**独立平面**呈现到屏幕；GDI 系截屏（微信/QQ/PrntScr 的 BitBlt 屏幕 DC）只读桌面合成面，**读不到该独立平面** → 截到的就是黑色播放器底。开不开超分都成立（不带超分时就是裸 `<video>` 层被提升），窗口态会犯、全屏态更频繁。

### 受控实验（本机 2560×1600，`_fs_cap.py` 连发 BitBlt + 自动判黑）
| 配置 | 窗口态 | 全屏态 |
|---|---|---|
| 只带 `--disable-direct-composition-video-overlays`（v0.3.1.7 现网配置） | **8/8 黑**、5/8 黑 | 5/8 黑 |
| **加 `--disable-direct-composition`** | **0/10 黑** | **0/10 黑** |
| `--disable-accelerated-video-decode`（软件解码） | 2/10 | 5/10（**无效**） |
| `--disable-gpu` | 0/10（有效但**整条渲染退软件**：视频/弹幕极卡、超分 WebGL 报废） | 0/10 |
| `--disable-gpu-compositing` | 1/10，且**全屏窗口截成空白**（渲染坏） | 失败 |

结论：**必须关掉 DComp 呈现**（`--disable-direct-composition`），它只把"呈现到窗口"这一步换回普通路径，**GPU 渲染照旧**——带它跑超分，探针仍报 `mode=acnet/高/2x`、`canvas 1724x969` 已建、8/8 非黑，管线正常。

## 修复设计
1. **浏览器级（决定性）**：`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--disable-direct-composition-video-overlays --disable-direct-composition"`。
   - `app_window.py`：进程早期 setdefault（覆盖 pywebview 主控件；允许用户环境变量覆盖）。
   - `shell_pages.py`：新标签页 `CreationProperties.AdditionalBrowserArguments` 追加同一组（第二条路，不赌"属性 vs 环境变量"语义）。
   - ⚠️ 实证：WebView2 里环境变量与属性是**合并**语义（不是覆盖），故无需担心被盖掉。
2. **页内冗余兜底**：`bilibili-sr.user.js` 的 0.5px 圆角规则保留（仅启发式，非决定性）。
3. **取证工具**：新增 `_fs_cap.py`（窗口态 vs 软件全屏态连发 BitBlt 判黑）；`_wv2_args_check.py` / `_wv2_args_exe.py`（抓进程命令行核对开关是否进到 WebView2，源码 vs exe 一致性）。

## 顺带查清的两个疑点
- **"exe 比源码更容易黑"不是结构差异**：抓进程命令行证明 exe 与源码**同一套系统 WebView2、同一 user-data-dir、同款开关、同样有 GPU 进程**；`DATA_DIR` 也相同。最可能是用户双击的是**旧版 exe**（`dist/` 里并存 0.3.1.4 与更新版本），或状态差异。
- **软件全屏 = `shell_tabstrip._fill_bounds()`**（拉伸铺满、留 1px 不判独占全屏）。它让窗口盖满整屏，正是最易触发视频层独立呈现的形态。

## 验收标准
- py_compile + node --check 全过；test_shell 全过；test_smoke 无新增 FAIL。
- `_fs_cap.py`：窗口态/全屏态均 0 黑帧。
- 带该开关跑超分：探针报 SR 激活（canvas 已建）且 0 黑帧。
- 重构建 exe（用户日常双击 exe 运行，应用层改动必须重打包才生效）。
