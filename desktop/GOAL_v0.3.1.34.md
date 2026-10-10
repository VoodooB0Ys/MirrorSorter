# GOAL · v0.3.1.34dsk —— 「信息以新窗口形式打开」(默认开)

> 目标版本 **v0.3.1.34dsk**。上一任务为 v0.3.1.33dsk(弹幕列表偶发空白根治)。

## 需求(用户原话)
> 给软件增加一个默认开启的主题设置 ui 功能：信息以新窗口形式打开。顾名思义，现在点开一个
> 信息会以标签页的形式展示，而这个功能就是让信息以单独一个窗口的形式展示，然后这个窗口也能
> 再塞回去变成标签页，标签页也能拽出来当悬浮窗口。

拆成三条可验收的行为：
1. **设置**：主题设置栏里新增一个开关「信息以新窗口形式打开」，**默认开启**。
2. **打开方式**：开启时点开信息(卡片/消息/账号等任何链接) → 弹**独立悬浮窗**，不再是标签页；
   关闭时回到原来的「主窗口内新标签页」。
3. **双向迁移**：悬浮窗可**塞回**主窗口变成标签页；主窗口的标签页也能**拽出来**当悬浮窗。

> **交互修正(用户后续反馈)**：「从悬浮窗口合并到界面里应该是拽着选项卡放到选项栏里就能合并」
> —— 合并的主路径改为**拽住悬浮窗标题栏拖到主窗口标签栏、松手即合并**(与浏览器把窗口拖进
> 另一个窗口标签栏一致)；标题栏右侧的 `⤓` 按钮保留为等价的可发现入口。

## 方案：控件实例跨窗迁移(页面不重载)
主窗口壳是单 pywebview 窗口 + 每标签一个 WebView2 控件的**控件池**(`shell_pages.py`)。
悬浮窗不做"重新开一个窗口再重新加载页面"，而是把**同一个 WebView2 控件**在
主窗体与悬浮窗体之间迁移(`Control.Parent` 换父容器)：

- 新模块 `shell_float.py`：无边框 WinForms 窗体 + 自绘迷你标题栏(页面标题 / `⤓ 塞回标签页`
  / `✕ 关闭`) + 边缘缩放热区 + 拖动移动；配色吃主界面 `ui_bg_scheme`。
- **拖到标签栏松手即合并**：标题栏拖动走**自绘**路径(`bar.Capture = True` + 跟手改
  `form.Location`)而不是 `HTCAPTION` 系统模态循环 —— 模态循环会把鼠标事件吞进系统消息
  循环，我们收不到"松手落点"。松手时用 `main.PointToClient(Cursor.Position)` 判断是否落在
  标签栏高度带内(±8px 容差)，是则 `api.window_dock(tid)`。拖动全程通过
  `shell_tabstrip.update.highlight(on)` 给标签栏整条染色+强调色描边，提示"可合并到这里"。
- `shell_pages.py` 新增 `send_to_window` / `dock_to_main` / `control`：只做父容器迁移 +
  重新锚定四边，**不 create、不 Navigate、不 Dispose** → 控件实例不变，播放进度/滚动位置/
  表单原样保留。`dispose` 修正为按**实际父容器**移除(悬浮标签关闭不泄漏)。
- `ShellApi`(app_window.py)：`open_in_app(url, title, mode)` 按设置决定进悬浮窗还是标签页；
  新增 `tab_detach`(标签→悬浮窗) 与 `window_dock`(悬浮窗→标签页)；`_render` 过滤悬浮标签
  (它们不出现在主窗口标签栏)；`tab_close` 兼容悬浮标签；`update_title` 同步悬浮窗标题。
  `gui_ready` 里 **`install_strip` 必须先于 `install_floats`**(后者要拿标签栏高度
  `tabstrip_h` 做落点判定)。
- 标签栏拖出：`shell_tabstrip.py` 在可关闭标签上按下并拖动超过阈值 → `api.tab_detach`，
  并吞掉随后的 `MouseClick`(不误切/误关)。设置关闭时 `tab_detach` 直接返回 `off`。
- 设置：`ui_new_window`(默认 `"1"`) 入 DEFAULTS 与 `/api/settings` 读写白名单；
  `web/index.html` 主题设置栏新增开关行，`web/app.js` 负责回显与保存(切换即生效)。

## 验收
- `test_shell.py` 全绿(99 checks，含悬浮窗/拖出/塞回语义 + 拖到标签栏合并接线)。
- `_verify_v34.py` 49 项全过(版本/接线/部件/迁移不变量/ShellApi 语义/拖拽接线/拖到标签栏
  合并/设置往返)。
- 真机 GUI 冒烟 `app_window.py --e2e-float <url>`：`新窗口 → **鼠标拖到标签栏松手合并** →
  拖出 → 关闭` 全链路的控件归属全部正确(悬浮窗态父窗体=悬浮窗、标签态父窗体=主窗口、
  控件实例不变)。拖拽由 `_e2e_drag_to_tabbar` 注入真实光标位置 + 向迷你标题栏投递
  LEFTDOWN/MOUSEMOVE/LBUTTONUP 复现；若该环境无法注入输入则回退 `window_dock` 并记录。
  **源码与打包 exe 各跑通 2 次**：`drag-merge -> merged=True`、
  `highlight 调用序列=[..., True, ..., False]`、`floats=[]`、控件父窗体变回主窗口。
- `py_compile` / `node --check` 干净；`test_smoke.py` 无新增回归
  (唯一失败项 `悬浮球:进程建窗自关` 是**本机环境问题**：连裸 `tkinter` 建窗都挂住，
  与本改动无关，该路径在 app.py 的 tkinter 段)。
- 重建 exe `dist\洞鉴虚明v0.3.1.34dsk.exe`(用户日常双击 exe 运行)。
