# GOAL · v0.3.1.9dsk —— 截屏黑屏「零性能代价根治」

> 目标版本 **v0.3.1.9dsk**。上一任务为 v0.3.1.8dsk（commit 6d031f3）。

## 背景
v0.3.1.8dsk 用浏览器级 `--disable-direct-composition` 把 GDI 截屏黑帧清零了，但用户实测
**"特别特别卡，有弹幕时更卡"** —— 该开关把整条"呈现到窗口"从 DirectComposition 换回
传统合成路径，大窗口 + 弹幕（大量 DOM 动画）时合成代价暴涨。**用性能换正确性，不可接受。**

## 目标
在不牺牲 GPU/DComp 合成性能的前提下，让播放器视频帧可被 GDI 系截屏（微信/QQ/PrntScr 的
BitBlt）取到 —— 即**零性能代价根治**。

## 方案：页内破坏 video 的 overlay 提升资格
- 根因不变：Chromium 经 DirectComposition 把视频层当**独立平面**呈现，GDI 截屏只读 DWM
  合成主面、读不到该平面 → 播放器区域黑。
- 新手段：给播放器 `<video>` **无条件**注入视觉无感的 `border-radius:0.5px!important`。
  Chromium 的 overlay 候选判定中，带圆角裁剪的 quad 一律不提升为独立平面
  （`rounded_corner_bounds` 非空即否决）→ 视频帧并入主合成面 → 既能被截屏取到，又完全
  保留 GPU/DComp 合成（超分 WebGL 亦不受影响）。0.5px 圆角肉眼不可见。
- 实现位置：`shell_pages.py` 的 `_build_inject_script`，B站 host 门控，**与 ui_sr /
  ui_ownui 开关无关**（用户实证"不开超分也黑"，必须无条件注入）。
- 回退：`app_window.py` / `shell_pages.py` 恢复为只带
  `--disable-direct-composition-video-overlays`（冗余声明，无害）。

## 验收
- `_fs_cap.py src`（窗口态 + 软件全屏，各 N=10 帧 BitBlt 自动判黑）**黑帧 = 0**。
- 性能恢复：走默认 DComp 合成路径，弹幕不卡（用户主观确认）。
- py_compile / node --check 干净；test_shell 76 PASS；smoke 389 PASS / 0 FAIL。
- 重建 exe `dist\洞鉴虚明v0.3.1.9dsk.exe`（用户日常双击 exe 运行）。
