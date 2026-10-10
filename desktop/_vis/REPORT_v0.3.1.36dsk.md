# 洞鉴虚明 v0.3.1.36dsk —— 悬浮窗黑屏 / 置顶范围 / 关窗仍出声 + 全屏黑屏

交付物：`desktop\dist\洞鉴虚明v0.3.1.36dsk.exe`（21,276,464 字节，2026/10/9 21:06）
直接双击即可，修复是**默认路径**，不需要任何开关、环境变量或测试入口。

## 1. 改了哪三处（对应用户报的三条）

### ① 悬浮窗播放黑屏（根因）
`desktop\shell_pages.py:962` 类属性名不一致：出厂版写的是 `_float_of = {}`（带下划线），
而读它的四处全写 `float_of` —— `send_to_window`（:1450）、`dispose`（:1407）、
`dock_to_main`（:1501）、`on_fs_changed`（:1075）。

后果：控件迁进悬浮窗体后第一句 `_Pool.float_of[key] = form` 就
`AttributeError: type object '_Pool' has no attribute 'float_of'`，而 `send_to_window`
整段被自己的 `except` 吞掉 ⇒ **控件虽然进了悬浮窗，但后面的几何、`Visible=True`、
`BringToFront`、`Focus`、补呈现全没执行 = 内容整块黑**（页面其实活着，信标还在跑）。

改法：统一为 `float_of`，并加注释锁定名字（四处调用点不动）。

### ② 关闭悬浮窗后后台还有声音（同一处根因）
`dispose()` 里同一句抛错被外层 `except Exception: pass` 吞掉 ⇒ 后面的
`IsMuted = True` 与 `c.Dispose()` 全不执行 ⇒ **播放实例没销毁，声音继续**。
名字修好后销毁链恢复；`IsMuted` 兜底保留。

### ③ 悬浮窗压在整个电脑最上层
`desktop\shell_float.py` 的 `_make_win` 里删掉 `form.TopMost = True`，改为：

```python
try:
    form.Owner = main          # main = win.native，主窗口
except Exception:
    pass
```

⇒ 悬浮窗只保持在**本软件**之上；QQ 之类别的应用可以正常盖住它，切回本软件时它仍在主窗口之上。

### 另：全屏播放整屏黑（前一轮同类问题）
`shell_pages.set_fullscreen`：进入网页全屏时把宿主窗口一起最大化 + 控件 `Dock=Fill` +
`_retext_later(...)`（同一控件上分几次补「隐藏→显示→置顶」，把被 DirectComposition
打断的合成层重新 present），退出时按进入前记录的现场还原（含被临时隐藏的兄弟控件）。

## 2. 改前 / 改后同一场景实测（不是"应该修好了"）

| 指标 | 改前（`_vis\float_probe_before.log`） | 改后（`_vis\float_probe_final.log`） |
|---|---|---|
| 悬浮窗画面（PrintWindow） | 黄=0.0207% 蓝=0.0456% 亮度=7.5 = **整块黑（窗体底色）** | 黄=0.1076% 蓝=48.1461% 亮度=49.3 = **页面+视频平面都在** |
| 关窗后新增页面心跳 | **2 条（含 `t=tick`）= 还在出声** | tick=**0**（只剩 3 条页面被隐藏时的临终信标） |
| 悬浮窗 exstyle | — | `0x50000`、`TOPMOST=False`、`owner=0x1E0A76`（=主窗口） |
| 别的应用 vs 悬浮窗 z 序 | — | 别的应用=61 < 悬浮窗=68 < 主窗口=69（越小越靠上） |
| msedgewebview2 进程数 | — | 7（标签页）→ 7（悬浮窗）→ 6（关窗后） |

全屏（`_vis\black_probe_final.log`）：进全屏后整屏扫描到探针画面（黄点=214、蓝点=681，
黄范围 (9,12)–(474,417)），退出全屏恢复原窗口；`RESULT: ALL_PASS`。

回归测试：`test_shell.py` **ALL_PASS (112 checks)**、`test_smoke.py --skip-ball`
**388 PASS / 0 FAIL**、`test_rules.py` **84 PASS / 0 FAIL**。

打包产物冒烟：隔离端口 8139 起新 exe，窗口标题 `洞鉴虚明 v0.3.1.36dsk`、端口正常监听。

> 说明：探针里"洋红替身窗口压在悬浮窗之上"的那条**像素级**检查未通过（替身 tkinter 窗口
> 被 Win32 判为可见、z 序也确实在悬浮窗之上，但整屏抓图没抓到它的底色）；层级结论依据的是
> exstyle（非 WS_EX_TOPMOST）+ owner=主窗口 + z 序三项机制证据，以及实机截图中悬浮窗画面本身。
