# -*- coding: utf-8 -*-
"""标签栏拖拽的**处理器级**单测(真 WinForms, 不开窗口)。

纯函数 detach_verdict 测的是"判据对不对"(见 test_shell.py);
这里测的是"接线对不对" —— MouseDown → MouseMove → tab_detach 这条链路上,
真的事件订阅、捕获、armed/fired 状态机、以及"点击仍要正常切标签"。

做法: 建一个不 Show 的 Form, 用 Python 代理把 win.native 包一层(只为伪造 webview),
调 install_strip 装上真标签栏, 再用反射触发 Control.OnMouseDown/OnMouseMove/OnMouseUp/OnMouseClick。
不需要真鼠标, 也就不会受 Control.MouseButtons 这种全局物理状态干扰。

用户反馈的场景:
    "我只要点一下选项卡就会导致它被拽出来变成一个悬浮窗口,
     而我设定的应该是把它往下拽下去才能变成一个悬浮窗口。"
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clr  # noqa: E402
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
import System  # noqa: E402
import System.Windows.Forms as WinForms  # noqa: E402
import System.Drawing as Drawing  # noqa: E402
from System.Reflection import BindingFlags  # noqa: E402

import shell_tabstrip  # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  [OK] " if cond else "  [!!] ") + msg)
    if not cond:
        FAIL.append(msg)


class FakeWebView:
    """install_strip 只会往它身上写 Dock/Location/Size/Anchor。"""
    Dock = None
    Location = None
    Size = None
    Anchor = None


class FormProxy:
    """把真 Form 包一层, 只为了能塞一个假的 form.webview。
    (pythonnet 不允许给 .NET 对象挂任意 Python 属性, 所以只能代理。)"""

    def __init__(self, form, wv):
        object.__setattr__(self, "_f", form)
        object.__setattr__(self, "_wv", wv)

    def __getattr__(self, k):
        if k == "webview":
            return object.__getattribute__(self, "_wv")
        return getattr(object.__getattribute__(self, "_f"), k)

    def __setattr__(self, k, v):
        if k == "webview":
            object.__setattr__(self, "_wv", v)
        else:
            setattr(object.__getattribute__(self, "_f"), k, v)


class FakeApi:
    def __init__(self):
        self.calls = []

    def tab_detach(self, tid):
        self.calls.append(("detach", tid))
        return "ok"

    def tab_switch(self, tid):
        self.calls.append(("switch", tid))
        return "ok"

    def tab_close(self, tid):
        self.calls.append(("close", tid))
        return "ok"


# ---------------------------------------------------------------- 装配
form = WinForms.Form()
form.ClientSize = Drawing.Size(900, 600)
# install_strip 的入参是 pywebview 的 window: form = win.native
win = type("Win", (), {"native": FormProxy(form, FakeWebView())})()
api = FakeApi()
update = shell_tabstrip.install_strip(win, api)

strip = None
for i in range(form.Controls.Count):
    c = form.Controls[i]
    if isinstance(c, WinForms.Panel) and c.Dock == WinForms.DockStyle.Top:
        strip = c
        break
if strip is None:
    print("[!!] 装不上标签栏 —— install_strip 没把 Panel 加进窗体")
    sys.exit(1)
strip.Width = 900

TABS = [{"id": 0, "title": "信息流", "closable": False},
        {"id": 1, "title": "法国最近太烧心了", "closable": True},
        {"id": 2, "title": "李丽滢的个人空间", "closable": True}]
update(TABS, 0)

# ---------------------------------------------------------------- 反射触发鼠标事件
_CT = clr.GetClrType(WinForms.Control)
_ME = WinForms.MouseEventArgs
_L, _R = WinForms.MouseButtons.Left, WinForms.MouseButtons.Right
_P = BindingFlags.Instance | BindingFlags.NonPublic


def _inv(name, *args):
    m = _CT.GetMethod(name, _P)
    m.Invoke(strip, list(args))


def down(x, y, btn=_L):
    _inv("OnMouseDown", _ME(btn, 1, x, y, 0))


def move(x, y):
    _inv("OnMouseMove", _ME(_L, 0, x, y, 0))


def up(x, y):
    _inv("OnMouseUp", _ME(_L, 1, x, y, 0))


def click(x, y):
    _inv("OnMouseClick", _ME(_L, 1, x, y, 0))


# 标签 1 的命中点: _layout 里第一个可点区(x=6, 宽 180) → 取 (60, 16)
# 标签 2 在 x≈190 起。用 _layout 反推太脆, 这里直接扫描找命中。
def hit_x(tid):
    """找出某标签可点区的**中心** x。
    取左缘不行 —— 手抖几像素就滑到标签之间的缝隙里, 反而测不出真问题。
    做法: 扫出所有会切到该标签的 x, 取首尾中点。"""
    hits = []
    for x in range(8, 880, 2):
        api.calls.clear()
        down(x, 16)
        up(x, 16)
        click(x, 16)
        if any(c[0] == "switch" and c[1] == tid for c in api.calls):
            hits.append(x)
    return (hits[0] + hits[-1]) // 2 if hits else None


X1 = hit_x(1)
X2 = hit_x(2)
print("标签命中点: tab1 x=%s, tab2 x=%s" % (X1, X2))
chk(X1 is not None and X2 is not None, "能定位到两个可关闭标签的可点区")


def scenario(name, fn):
    api.calls.clear()
    fn()
    return list(api.calls)


# ---------------------------------------------------------------- 用例
print()
print("[1] ★ 用户报的场景: 点一下标签 → 绝不能撕出悬浮窗, 而且要正常切过去")
if X1 is not None:
    # 真实点击会夹带几像素手抖, 逐级加大都试一遍
    for jitter in ((0, 0), (3, 4), (-4, 5), (6, 6), (-8, 7), (10, 9)):
        api.calls.clear()
        down(X1, 16)
        move(X1 + jitter[0], 16 + jitter[1])
        up(X1 + jitter[0], 16 + jitter[1])
        click(X1 + jitter[0], 16 + jitter[1])
        det = [c for c in api.calls if c[0] == "detach"]
        sw = [c for c in api.calls if c[0] == "switch"]
        chk(not det and sw == [("switch", 1)],
            "手抖 %-9s → 不撕出, 且正常切到标签1 (%s)" % (str(jitter), api.calls))

print()
print("[2] ★ 往下拖够深 → 撕出为悬浮窗")
if X1 is not None:
    for dy in (30, 45, 80, 200):
        c = scenario("down", lambda dy=dy: (down(X1, 16), move(X1, 16 + dy)))
        chk(c == [("detach", 1)], "下拖 %-4dpx → %s" % (dy, c))
    # 拖出后松手的那次点击必须被吞掉(不能顺手把标签切了)
    api.calls.clear()
    down(X1, 16)
    move(X1, 60)
    up(X1, 60)
    click(X1, 60)
    chk([c for c in api.calls if c[0] == "switch"] == [] and
        [c for c in api.calls if c[0] == "detach"] == [("detach", 1)],
        "撕出后紧跟的那次点击被吞掉(不会顺手切标签): %s" % api.calls)

print()
print("[3] ★ 横向拖 / 往上拖 → 都不撕出")
if X1 is not None:
    for dx, dy in ((120, 5), (-200, 10), (300, 20), (0, -40), (5, -60), (60, 30)):
        c = scenario("x", lambda dx=dx, dy=dy: (down(X1, 16), move(X1 + dx, 16 + dy)))
        chk([x for x in c if x[0] == "detach"] == [],
            "位移 (%-5d,%-4d) → 不撕出 %s" % (dx, dy, c))

print()
print("[4] 斜向下拖: 纵向明显大于横向才认")
if X1 is not None:
    c = scenario("a", lambda: (down(X1, 16), move(X1 + 10, 16 + 40)))
    chk(c == [("detach", 1)], "偏纵向 (10,40) → 撕出 %s" % c)
    c = scenario("b", lambda: (down(X1, 16), move(X1 + 70, 16 + 40)))
    chk([x for x in c if x[0] == "detach"] == [], "偏横向 (70,40) → 不撕出 %s" % c)

print()
print("[5] 拖出去又拖回来(没到阈值) → 不撕出, 且不残留状态")
if X1 is not None:
    c = scenario("w", lambda: (down(X1, 16), move(X1, 40), move(X1, 20), move(X1, 18)))
    chk([x for x in c if x[0] == "detach"] == [], "拖出去又回来 → 不撕出 %s" % c)
    # 紧接着正常点一下, 必须还能切(状态没卡住)
    api.calls.clear()
    down(X1, 16); up(X1, 16); click(X1, 16)
    chk([c for c in api.calls if c[0] == "switch"] == [("switch", 1)],
        "之后正常点击仍能切标签: %s" % api.calls)

print()
print("[6] 固定标签(信息流 id=0)不可撕出")
if X1 is not None:
    x0 = X1 - 100 if X1 >= 100 else 10
    c = scenario("p", lambda: (down(x0, 16), move(x0, 16 + 120), up(x0, 16 + 120)))
    chk([x for x in c if x[0] == "detach"] == [], "固定标签往下拖 → 不撕出 %s" % c)

print()
print("[7] 窗口按钮区(─ □ ✕)按下不应进入拖拽")
if X1 is not None:
    c = scenario("btn", lambda: (down(890, 16), move(890, 200), up(890, 200)))
    chk([x for x in c if x[0] == "detach"] == [], "右端按钮区下拖 → 不撕出 %s" % c)

print()
print("[8] 鼠标移出标签栏(未按下) → 清理悬停, 不影响后续")
if X1 is not None:
    _inv("OnMouseLeave", System.EventArgs.Empty)
    api.calls.clear()
    down(X1, 16); up(X1, 16); click(X1, 16)
    chk([c for c in api.calls if c[0] == "switch"] == [("switch", 1)],
        "移出后仍能正常点击: %s" % api.calls)

print()
print("=" * 76)
if FAIL:
    print("失败 %d 项:" % len(FAIL))
    for f in FAIL:
        print("   - %s" % f)
else:
    print("ALL_PASS")
print("=" * 76)
sys.stdout.flush()
sys.stderr.flush()
# ★ 必须 os._exit: 本进程里有个没跑消息循环的真 WinForms Form, 走正常退出路径
#   会卡在 WinForms 的关闭流程上(表现为脚本跑完却一直不返回, 被外层 timeout 杀掉)。
os._exit(1 if FAIL else 0)
