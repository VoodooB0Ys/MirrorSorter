# 洞鉴虚明 v0.3.1.37dsk —— 播放页/信息流 4 处视觉调整（交付说明）

时间：2026-10-09 21:5x
产物：`desktop\dist\洞鉴虚明v0.3.1.37dsk.exe`（21,278,164 字节）

## 用户本轮要求（原话，m03204）

> 相关推荐的标题字体可以更大一些，播放页面左侧可以更宽一点，这样就能把视频播放挤在更中心一点的位置，这个左侧和B站播放页背景一个月颜色就行。信息来源和头像下方的信息来源都改成跟随强调色，而不是使用蓝色和红色

## 逐项：改了哪一处 + 改后实测

### ① 「相关推荐」标题字号加大

- 文件：`desktop\extensions\bilibili-ownui.user.js:404`（自有右栏 UI，Shadow DOM 内的样式串）
- 改前 `.rel{font-size:15px;font-weight:600;padding:4px 12px}` → 改后 `.rel{font-size:19px;font-weight:700;padding:6px 12px}`

实测（真窗壳 + 真 B 站播放页，CDP 读 Shadow 内计算样式）：

```
"rel": {"text": "相关推荐 已按机器筛选 · 相关:探索=", "font": "19px", "weight": "700", ...}
PASS  「相关推荐」标题字号已加大(≥18px)
```

### ② 播放页左侧加宽（按窗口宽度比例，把视频推向中间）

- 文件：`desktop\shell_pages.py:35-36` 新增 `TAB_INSET_RATIO = 0.055`、`TAB_INSET_MIN_L, TAB_INSET_MAX_L = 28, 170`（逻辑像素）
- `desktop\shell_pages.py` 的 `_tab_geo(form, home, key)` 改为调用新增的 `_tab_dx(form, key)`：主信息流控件（key=0）留白 0；外部页签留白 = `round(窗体客户区宽度 × 5.5%)`，钳在 28~170 逻辑像素。**右侧不加留白**，页签控件右沿仍与窗体齐平（不裁右侧栏）。
- 按比例（而不是固定增量）是为了任意窗口尺寸下左右观感一致。

实测（应用自身控件几何，物理像素）：

```
[几何] 主控件 x=0 w=2138  /  页签控件 x=118 w=2020  ⇒ 左留白=118 物理px (5.52%), 右沿余量=0
PASS  外部页签左留白按窗口宽度比例(≈5.5%)   | 5.52% (=118/2138)
PASS  页签控件右沿仍齐平(不裁右侧栏)        | 余量=0
[构图] 视频(#bilibili-player) 页内 x=41 w=870 → 窗内 左=179 物理px, 视频宽=1305
PASS  视频区被左留白推离左边缘(≥60 物理px)   | 左=179
```

（旧行为是固定 20 逻辑像素 ≈30 物理像素；现在 118 物理像素，约为原来的 3.9 倍。）

### ③ 左侧留白底色与 B 站播放页背景同色（不留分界线）

- 文件：`desktop\app_window.py` 新增 `BG_PAGE = "#17181a"`（数值取自用户截图采样 rgb(23,24,26)），`webview.create_window(..., background_color=BG_PAGE, ...)`（原来是 `BG_DARK "#0f1115"`）
- 文件：`desktop\shell_pages.py` 新增 `_apply_page_bg(self, dark)`：深色主题时窗体背景 `FromArgb(23,24,26)`、白天为白色；`_theme_watch` 里主题变化时调用；`init_ui()` 里原来硬编码的 `form.BackColor = FromArgb(15,17,21)` 改为按主题取值（否则它会覆盖 pywebview 的 `background_color`）。

实测（窗体 PrintWindow 抓图，取样左留白与紧邻播放页像素）：

```
[底色] 窗体 PrintWindow 1425x883
留白像素(x=26)=(23, 24, 26)   播放页像素(x=103)=(23, 24, 26)   色差=0
PASS  留白底色与B站播放页底色一致(无明显分界线)   | 色差=0
```

截图：`desktop\_vis\play_ui_window.png`（真窗壳、真 B 站播放页；可见左侧一条与播放页同色的加宽底色带）。

### ④ 两处「信息来源」改成跟随强调色

- 文件：`desktop\web\style.css:163` `.plat-bili, .plat-tb, .plat-xhh { color: var(--accent); }`（顶栏头像下方 `<em class="plat-tag plat-tb|plat-xhh">`，见 `web\index.html:274,280,286`）
- 文件：`desktop\web\style.css:343` `.card .badge.plat-bili, .card .badge.plat-tb, .card .badge.plat-xhh { color: var(--accent); }`（信息流卡片标题前的来源徽标，标记由 `web\app.js:806` 生成）
- 文件：`desktop\web\style.css:166` 无头像时的字母占位圆 `.avatar-ph` 底色 `#5b8def` → `var(--accent)`
- 改前是品牌色：`.plat-tb{#7fa3e8}`（蓝）、`.plat-xhh{#e08a8d}`（红），白天模式另给 `#3f6fc4`/`#c2454a`；这些硬编码值已全部移除（全仓 grep `#7fa3e8|#e08a8d|#3f6fc4|#c2454a` 无命中）。

## 验证环境与结果

- 探针 `desktop\_play_ui_check.py`（AST OK）走**真实路径**：起真窗壳（隔离端口 8138 + 隔离数据目录 `_tmp_data_ui37`，不动用户实例）→ 在应用页调 `window.pywebview.api.open_in_app(真B站视频,'','tab')` → 量控件几何 / 窗体像素 / 页面计算样式。
- 全量判定：`RESULT: ALL_PASS`（7 项，见 `desktop\_vis\play_ui_check.log`）。
- 回归测试（改后全部重跑，2026-10-09 22:0x）：
  - `test_shell.py` → `RESULT: ALL_PASS (115 checks)`（其中新增/更新 5 条：bug4 改为断言 owner 关系且代码里不再有 TopMost；bug12 改为断言文字色跟随强调色且实底仍取消；新增 视觉1/2/3 三条）
  - `test_smoke.py --skip-ball` → `==== 388 PASS / 0 FAIL ====`
  - `test_rules.py` → `==== 84 PASS / 0 FAIL ====`
- 打包与启动验证：`dist\洞鉴虚明v0.3.1.37dsk.exe`（21,278,164 字节，21:53:59）；隔离端口 8139 + 隔离数据目录 `_tmp_data_exe137` 起该 exe → 窗口标题 `洞鉴虚明 v0.3.1.37dsk`、8139 LISTENING（pid 21048，onefile 引导 pid 15008）、数据目录正常生成 `data.db`/`shell_debug.log`，随后结束进程、端口无残留。

## 用户怎么看到这四处变化

直接运行 `desktop\dist\洞鉴虚明v0.3.1.37dsk.exe`（就是平时用的那个桌面程序），点开任意 B 站视频：
- 左边缘多出一条与播放页同色的加宽底色带（约窗口宽度的 5.5%），视频/右栏整体右移、更靠中间；
- 右栏「相关推荐」标题明显更大更粗；
- 信息流里卡片标题前的来源标签、以及顶栏头像下方的来源标签，颜色跟着当前主题的强调色走（不再是固定的蓝/红）。

无需任何开关，也没有测试入口。

- 版本号：`app.py:66`、`web\index.html:244` 页脚、`test_shell.py:406` 段标题均已是 `v0.3.1.37dsk`。
