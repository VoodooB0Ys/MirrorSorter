# 洞鉴虚明 v0.3.1.44dsk —— 标签页撕出悬浮窗：触发方式修正

交付物：`desktop\dist\洞鉴虚明v0.3.1.44dsk.exe`（**78,358,829 字节 = 74.7 MB**，2026/10/10 14:45）

用户原话：
> 这个磁吸窗口触发有点问题，我只要点一下选项卡就会导致它被拽出来变成一个悬浮窗口，
> 而我设定的应该是把它往下拽下去才能变成一个悬浮窗口。

---

## 1. 根因：判据是"任意方向"，而且方向语义整个反了

`shell_tabstrip.py` 里原来的撕出判据只有一行：

```python
if abs(args.X - drag["x"]) + abs(args.Y - drag["y"]) > int(22 * scale):
    api.tab_detach(tid)
```

这是 **曼哈顿距离，不带任何方向要求**。所以：

- **点一下标签时手抖二十来像素 → 直接撕出悬浮窗。** 这就是用户报的症状。
- 更糟的是**方向语义反了**。标签栏只有 `bar_h = 32 × scale` 高（实测约 32px），
  而 `on_mouse_leave` 里有一句：

  ```python
  def on_mouse_leave(_sender, _args):
      drag["armed"] = False     # ← 旧代码
  ```

  也就是说 —— **往下拖会立刻离开这 32px 高的标签栏，触发 MouseLeave，把 `armed` 清掉**。
  ⇒ **"往下拽"恰恰是唯一拽不出去的方向**；只有横向/向上抖才行。

用户想要的和实际生效的，正好是反的。

## 2. 改法

### 2.1 判据改成"只认向下"，并提到模块级做成纯函数

```python
DETACH_DY = 26     # 向下拖出的阈值(逻辑像素 × scale)
HINT_DY = 8        # 到这个位移先给视觉提示(还没到撕出阈值)

def detach_verdict(dx, dy, scale=1.0):
    """返回 "detach" / "hint" / "" """
    if dy <= 0 or dy <= abs(dx):      # 必须向下, 且明显偏纵向
        return ""
    if dy >= DETACH_DY * scale:
        return "detach"
    if dy >= HINT_DY * scale:
        return "hint"
    return ""
```

**为什么提到模块级**：这个 bug 就出在这段逻辑里，而内联在 MouseMove 里的写法在无 GUI 环境下
没法回归测试。提出来之后，`test_shell.py` 里 20 条边界用例直接就能跑（见第 4 节）。

### 2.2 按住期间必须"抓住"鼠标

往下拖必然离开标签栏，不捕获就收不到后续 `MouseMove`：

```python
def _drag_capture(on):
    if bool(strip.Capture) != bool(on):
        strip.Capture = bool(on)
```

配套改了三处：

| 位置 | 改动 | 原因 |
|---|---|---|
| `on_mouse_leave` | 按住左键时**直接 return**，不取消 `armed` | 往下拖必然离开标签栏，那正是要的动作 |
| 新增 `on_mouse_up` | 松手才放开捕获 + 清可视态 | 收尾统一走这里 |
| 新增 `on_capture_changed` | 捕获被抢走（Alt+Tab / 系统弹窗）→ 复位 | 否则 `armed` 永远 True，`on_mouse_leave` 一直提前返回 → 悬停高亮卡死 |

### 2.3 撕出后**不**立刻放开捕获

这点看着别扭，但必须这样：

```python
# ⚠️ 这里**不放开**鼠标捕获: 放开后 MouseUp 会落到 WebView2 上,
#    on_mouse_click 不触发 → fired 标记没人消费 → 下一次真点击被误吞。
```

原来的 `fired` 标记是靠"紧接着的 MouseClick"消费的。如果撕出瞬间放开捕获，鼠标已经在
WebView2 上方了，`MouseClick` 不会派发给标签栏 → `fired` 一直挂着 → **下一次点标签被吞掉**。
所以统一交给 `on_mouse_up` 收尾。

### 2.4 顺手把 `Control.MouseButtons` 换掉

旧代码判断"左键还按着没"用的是 `WinForms.Control.MouseButtons & MouseButtons.Left` ——
那是**全局物理鼠标状态**，既不适合做局部手势判定，也让这段逻辑没法脱离真鼠标测试。
改成自己维护的 `drag["down"]`（MouseDown 置 True / MouseUp 置 False）。

### 2.5 手势变严了，得让用户知道

原来一点就撕出，用户根本不知道有"拖拽"这个手势；现在变严了，必须给反馈：

- **拖动中**：被拖的标签染上强调色（`drag_hint` 状态，超过 `HINT_DY` 才亮），
  用户能看到"再往下就是悬浮窗"；
- **文案**：设置页 tooltip 与开关回显都改成明确写方向 ——
  「仍可把任意标签页**向下拖**出成悬浮窗」。

---

## 3. 改了哪些文件

| 文件 | 改动 |
|---|---|
| `desktop/shell_tabstrip.py` | 模块级 `detach_verdict()` + `DETACH_DY/HINT_DY`；`on_mouse_down/move/up/leave` 重写；新增 `on_capture_changed`、`_drag_capture`、`_drag_visual`；绘制层加拖拽高亮 |
| `desktop/web/index.html` | tooltip 文案："拖出成悬浮窗" → "**向下拖**出成悬浮窗"；版本号 → v0.3.1.44dsk |
| `desktop/web/app.js` | 开关回显文案："标签页可拖出成悬浮窗" → "标签页**向下拖**可撕出成悬浮窗" |
| `desktop/app.py` | 版本号 → v0.3.1.44dsk |
| `desktop/洞鉴虚明v0.3.1.44dsk.spec` | 新增（**不入库**：`.gitignore` 里 `desktop/*.spec` 归为构建产物，由 `构建exe.bat` 重新生成） |
| `desktop/_build_now.py` | spec 缺失时退回命令行模式（与 `构建exe.bat` 一致），全新克隆也能打包 |
| `desktop/test_shell.py` | 拖拽相关 3 项断言重写 + 20 条 `detach_verdict` 边界用例 |
| `desktop/_test_tabstrip_drag_gui.py` | 新增：真 WinForms 环境的处理器级测试 |
| `desktop/_verify_exe.py` | 由 `_verify_exe_43.py` 通用化（版本从 exe 文件名反推），加拖拽修复检查 |
| `.gitignore` | 跟踪上面两个新测试脚本 |

---

## 4. 验证

### 4.1 `detach_verdict` 边界用例（`test_shell.py`，20 条）

```
下拖够深 → detach           (0,30) (2,40) (-3,28)
点一下手抖 → 不撕出          (0,0) (3,4) (-4,5)
横向拖 → 不撕出              (120,5) (-200,10) (60,30)
往上拖 → 不撕出              (0,-60) (5,-40)
斜向: 纵向要明显大于横向       (10,30)→detach  (45,30)→""
阈值边界                    25→hint  26→detach
150% DPI 阈值同步放大        38→hint  40→detach
提示态                      8→hint  7→""
```

### 4.2 ★ 处理器级测试（`_test_tabstrip_drag_gui.py`）—— ALL_PASS

建一个不 Show 的真 WinForms Form，用 Python 代理把 `win.native` 包一层（只为伪造 `webview`），
装上真标签栏，再用反射触发 `Control.OnMouseDown/OnMouseMove/OnMouseUp/OnMouseClick`。
**不需要真鼠标**，所以不受 `Control.MouseButtons` 这类全局状态干扰。

```
[1] ★ 用户报的场景: 点一下标签 → 绝不撕出, 且正常切过去
    手抖 (0,0) (3,4) (-4,5) (6,6) (-8,7) (10,9)  —— 6 种全过
[2] ★ 往下拖够深 → 撕出悬浮窗
    30 / 45 / 80 / 200 px 全过；且撕出后紧跟的那次点击被吞掉(不会顺手切标签)
[3] ★ 横向拖 / 往上拖 → 都不撕出
    (120,5) (-200,10) (300,20) (0,-40) (5,-60) (60,30) 全过
[4] 斜向下拖: (10,40)→撕出  (70,40)→不撕出
[5] 拖出去又拖回来(没到阈值) → 不撕出, 且之后正常点击仍能切标签(状态没卡住)
[6] 固定标签(信息流 id=0) → 不可撕出
[7] 窗口按钮区(─ □ ✒)按下 → 不进拖拽
[8] 鼠标移出标签栏(未按下) → 不影响后续点击
```

### 4.3 壳层回归 `test_shell.py` —— **149 项 ALL_PASS**

### 4.4 打包产物验收 `_verify_exe.py` —— **ALL_PASS**

- 静态：`shell_tabstrip` 在 PYZ 里、`detach_verdict` / `on_mouse_up` / `on_capture_changed` 都在位、
  阈值常量 26/8 在包里；模型 58.38MB 字节数一致；页脚版本正确
- 运行期：起 exe 实测模型加载 + 三条链路 + 总开关闸门

---

## 5. 过程中踩到的两个坑（都不是产品 bug，但值得记）

### 5.1 验收脚本把服务自己弄挂了 → `RemoteDisconnected`

跑到最后一步 `POST /api/machine {"ft_on":"0"}` 时突然断连。用源码版复现**完全正常**，
说明是 exe 专属问题。翻 `app.py` 里 v0.3.1.32 的注释才想起来：

> frozen exe 的 `--server` 模式恒为 `auto_open=True`，会弹一个浏览器页指向隔离测试服务；
> **页面一关就发 `navigator.sendBeacon("/api/shutdown")` 把测试服务杀掉**。

我的验收脚本只设了 `MIRRORSORTER_PORT`，**没设 `MIRRORSORTER_NO_OPEN`** → exe 真的弹了浏览器页，
页面被关掉 → 服务自杀。已补上 `MIRRORSORTER_NO_OPEN=1`。

（副作用：之前几次验收可能在你桌面上弹过浏览器页，抱歉。）

### 5.2 ★ Python 3.12+ 的小整数**不在** `co_consts` 里

想在打包产物里核对 `DETACH_DY=26 / HINT_DY=8` 有没有进包，查 `co_consts` 结果是**没有**，
一度以为漏打包。实测本机 `compile()` 同样查不到 —— 因为 **3.12 起小整数走 `LOAD_SMALL_INT`，
值直接编码在 oparg 里，根本不进常量表**。

改成扫 `dis.get_instructions()` 的 `LOAD_SMALL_INT` 才拿到 `[0, 8, 26]`。
⇒ 以后凡是要从字节码核对小整数常量，**别查 `co_consts`**。

---

## 6. 顺带确认

- 全仓库 grep `abs(...X - ...) + abs(...Y - ...)` 只有这一处，没有同类判据遗留
- `shell_float.py` / `shell_navbar.py` / `app_window.py` 都没有这种无方向的拖拽触发
- 固定标签（信息流）依旧不可撕出；「信息以新窗口形式打开」开关关掉后拖出仍然生效（bug2 不回归）
