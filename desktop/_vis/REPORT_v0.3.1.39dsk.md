# 洞鉴虚明 v0.3.1.39dsk — 启动卡死修复 + 导出默认脱敏

产物：`desktop\dist\洞鉴虚明v0.3.1.39dsk.exe`（21,279,744 字节，2026/10/9 22:49:12）
版本：`desktop\app.py:66 APP_VER = "v0.3.1.39dsk"`、`desktop\web\index.html:244` 页脚同版本

本轮两件事：① 修「一直卡死无法运行」（m03991）；② 导出设置默认脱敏、含个人信息的那份改成显式可选项（m04225 / m04371 / m04401）。

---

## 一、启动卡死：根因是一条 UPDATE 在写锁上等满 10 秒

### 根因（有逐语句计时证据）

`desktop\_db_lock_bench.py --steps`（复制用户库副本，另开一条连接 `BEGIN IMMEDIATE` 持写锁，包装 `sqlite3.connect` 给每条语句计时）：

```
[现状] 合计 10884 ms / OperationalError: database is locked
  PRAGMA journal_mode=WAL                        0.3 ms
  PRAGMA busy_timeout=10000                      0.0 ms
  executescript(3233 字符 DDL)                    0.1 ms
  5 条 ALTER TABLE ... ADD COLUMN                各 0.0 ms
  SELECT v FROM settings WHERE k='feed_version'  0.0 ms
  SELECT COUNT(*) FROM conv                      0.0 ms
  UPDATE chat SET cid=1 WHERE cid=0          10883.3 ms   <<< 全部耗时都在这一条
```

`desktop\app.py` 的 `db()` 给每个线程建一条新连接，连接创建时无条件执行这条 UPDATE —— 它曾是**本连接唯一的写语句**，于是每开一个新线程都要抢一次写锁；`PRAGMA busy_timeout=10000` 让它等满 10 秒后抛 `sqlite3.OperationalError: database is locked`。

窗口壳的注入串构建跑在 WinForms UI 线程的 `on_ready` 回调里：`desktop\shell_pages.py:1242 inj = _build_inject_script(box.get("port") or 8000)`，`_build_inject_script`（`desktop\shell_pages.py:484`）中途要读十几次设置（`sget` 是纯读，但首次会建连接 ⇒ 触发上面那条写）。撞上 10 秒 ⇒ 整窗「未响应」、注入串永不落地（`_dbg_log("on_ready: AddScript called ...")` 永不出现），用户看到的就是「双击后一直卡死」。

### 修法（两行守卫 + 注释记录根因）

`desktop\app.py:302-316`：

```python
# 老库消息(无cid)归入默认会话1。
# ⚠ 必须先判有无再写: 这条 UPDATE 曾是本连接的**唯一写语句**, 无条件执行时
# 只要别的线程/进程正持写锁, 就要在 PRAGMA busy_timeout=10000 上等满 10 秒,
# 然后抛 `sqlite3.OperationalError: database is locked`。
if conn.execute("SELECT 1 FROM chat WHERE cid=0 LIMIT 1").fetchone() is not None:
    conn.execute("UPDATE chat SET cid=1 WHERE cid=0")
conn.commit()
```

- 修后复测 `desktop\_db_lock_bench.py fixed`：**写锁争用下 新线程首次 `app.db()` = 1 ms，无错误**（原 10851 ms + locked）。
- 安全性核对：`desktop\app.py` 里 4 处 `INSERT INTO chat`（`:10942`、`:10952`、`:10969`、`:11181`）都显式写 `cid`，所以首次迁移后不会再有 `cid=0` 行；守卫后这条写语句在稳态**永不执行**。用户库副本 `SELECT COUNT(*) FROM chat WHERE cid=0` = 0。
- 曾试过「每进程一次性初始化 + 锁」的更大改法，因其余语句实测都 ≤0.3 ms、不值得动结构，**主动回退**，只保留这一处守卫。

### 真实启动记录（一次干净首启 + 一次带导出，都是跑 dist 里的真 exe）

隔离环境：`MIRRORSORTER_PORT=8139`、`MIRRORSORTER_DATA_DIR=desktop\_tmp_data_launch_*`、`MIRRORSORTER_NO_OPEN=1`、`MSX_NO_WV2_FLAG=1`；脚本 `desktop\_launch_evidence.py`（只按 PID 杀自己的进程树，绝不按镜像名杀 —— 用户那份 .38 同名风险已规避）。响应性判据与任务管理器「未响应」同源：`SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG=2, 1000)`。

**启动 #1（全新空数据目录，tag t9）→ 11 PASS / 0 FAIL**（`desktop\_vis\launch_t9.log`）

| 项 | 实测 |
| --- | --- |
| 窗口出现 | 双击后 **3.0 s**，标题 `洞鉴虚明 v0.3.1.39dsk`，rect (38,38,2138,1324) |
| 窗口响应 | 采样 20 次，**无一次「未响应」** |
| HTTP 服务 | 20 次 `/api/feed` **全部 200**，最慢 30 ms |
| 首次启动免责声明 | 真鼠标点击「我已阅读并同意」@ (856,736) → 弹窗关闭（首开态） |
| 设置栏折叠项 | 真鼠标点开 `<details>` summary 3 个，全部 `open=True` |
| 进设置导入/导出 | 真鼠标点 `#btnIoOpen` @ (110,642) → 面板打开 |
| 导出选项默认态 | `checked=False`，提示「✓ 默认：只导出设置（不含 aiKey、登录账号与各平台 Cookie）」 |
| 收尾 | 无残留窗口/进程 |
| 截图 | `desktop\_vis\launch_t9_window.png` (2138x1324)、`desktop\_vis\launch_t9_export_panel.png` |

**启动 #2（同一数据目录，先注入假密钥，tag t10，带 `--export`）→ 14 PASS / 0 FAIL**（`desktop\_vis\launch_t10.log`）
窗口 3.0 s 出现、12/12 次响应、12/12 次 HTTP 200（最慢 43 ms），导出两步见下节。

> 说明：早期两次启动取证失败都是探针自身口径问题，不是产品缺陷 —— ① 窗口标题实际是 `洞鉴虚明 v0.3.1.39dsk`（中间有空格）而过滤键按 exe 名匹配；② `#btnIoOpen` 外层 `<details>` 默认折叠，折叠态下按钮虽有几何但画面上那个位置是 footer，`elementFromPoint` 命中 footer ⇒ 必须先像用户那样点开 summary。两处都已修进探针，并且「点开折叠项」本身也成了一条真交互证据。

---

## 二、导出默认脱敏：默认拿到的就是能发朋友的那份

### 后端（白名单，不是黑名单）

`desktop\app.py`：

- `:107-110` `_IO_CRED_KEYS` 末尾加入 `"ai_key"`（注释记明：以前 ai_key 不在这个集合里，勾不勾「只导出设置」都会把 aiKey 写进导出文本 ⇒ 分享即泄露密钥）。
- `:123-125` 新增脱敏白名单：`_IO_SHAREABLE = frozenset(_IO_IMPORTABLE - _IO_CRED_KEYS)` —— **只有列在这里的键才会进脱敏导出，将来新增设置项默认不导出**，避免新字段悄悄把敏感信息带出去。
- `:128-144` `settings_export_payload(include_credentials)`：`allow = _IO_IMPORTABLE if include_credentials else _IO_SHAREABLE`，`st = {k: v for k, v in rows if k in allow and v not in (None, "")}`，payload 增加 `"sanitized": (not include_credentials)`。

### 界面（默认不导出；含个人信息必须显式勾）

- `desktop\web\index.html:664-666`：`<input type="checkbox" id="ioExpPriv">`（**默认不勾**）「导出个人信息和设置（可选，默认不勾）—— 勾上才会包含 aiKey、登录账号与各平台 Cookie……」+ `#ioExpWarn` 默认提示「✓ 默认：只导出设置……可直接分享给朋友」。
- `desktop\web\app.js:3073-3084`：`_ioWarn()` 随勾选切换告警；`const priv = $("#ioExpPriv") ? $("#ioExpPriv").checked : false;` ⇒ `body: { credentials: priv }`。后端 `desktop\app.py` 的 `/api/settings/export` 不传 `credentials` 即脱敏，前后端与旧数据兼容。
- 旧的「默认勾上的『只导出设置』」勾选框（`#ioExpSafe`）已删除，全仓无残留引用。

### 证据 A：真 HTTP 接口级（`desktop\_io_export_check.py t1`）→ 13 PASS / 0 FAIL

隔离库 + 8139：注入 9 个假密钥值（`sk-SECRET-AIKEY-9f3a`、`SESSDATA=COOKIE-BILI-77`、`BDUSS=COOKIE-TB-88`、`xhh_cookie`、`xhh_pkey`、`acct_nickname`、`acct_profile`、`bili_follow_mids`、`tb_user`）与 9 个普通设置：

- 默认入参 `{}` ⇒ `sanitized is True`；**9 个假密钥值在导出明文里一个都搜不到**；settings 里无账号键；普通设置 9/9 齐全；黑名单 2 条随导出。
- 清空本机设置后用这份脱敏文件 POST `/api/settings/import` ⇒ 导入 10 项，`mode` / `m_min_views` / `ai_model` 生效，`ai_key` 仍为空（朋友拿到就能配起自己的软件，且不会带上别人的密钥）。
- 显式 `{"credentials": true}` ⇒ 才含 aiKey 与各平台凭证（自己的完整备份路径仍在）。

### 证据 B：真 exe 界面上点出来的两份文件（tag t10）

默认（不勾任何东西，直接点「生成导出内容」，228 字节，`desktop\_vis\launch_t10_exported.json`）：

```json
{"app":"洞鉴虚明","kind":"mirror-sorter-settings","v":1,"ts":...,"sanitized":true,
 "settings":{"mode":"严格","ai_model":"deepseek-chat","m_min_views":"12345"},
 "blacklist":{"authors":[],"keywords":[]},"io_prefs":{"theme":"dark"}}
```

显式勾上「导出个人信息和设置」后再次生成（`desktop\_vis\launch_t10_exported_priv.json`）：

```json
{..."sanitized":false,
 "settings":{"ai_key":"sk-LEAKCANARY-39dsk-abcdef","bili_cookie":"SESSDATA=LEAKCANARY-COOKIE-77",
             "acct_nickname":"LEAKCANARY-昵称","mode":"严格",
             "ai_model":"deepseek-chat","m_min_views":"12345"}, ...}
```

即：`LEAKCANARY` / `SESSDATA` 这些假密钥**只在显式勾选后才出现**，默认路径里搜不到。截图：`desktop\_vis\launch_t10_exported_safe.png`、`desktop\_vis\launch_t10_exported_priv.png`。

### 「个人信息」边界（我做的取舍，可一行改回）

采用偏保守的读法一：**账号凭据 + 登录态 + 账号标识 + 关注列表**（`ai_key`、各平台 Cookie、`acct_nickname` / `acct_profile`、`bili_follow_mids` / `bili_follow_names`、`xhh_*` 账号）。浏览/搜索历史、收藏、聊天记录、设备标识本来就不在导出范围内（导出只写 `settings` + `blacklist` + `io_prefs`，界面底部已写明「历史、收藏、聊天记录不属于设置，不在迁移范围内」）。仍随导出的是功能性配置（来源/权重/阈值/贴吧吧列表等）与两个用户自写备注 `prefs_note` / `dislike_note` —— 如果你要更严格（连自写备注也不给），把这两个键从 `_IO_SHAREABLE` 里去掉即可，导入侧不受影响。

---

## 三、测试 / 打包

- `desktop\test_shell.py` → **`RESULT: ALL_PASS (122 checks)`**（新增 5 条：导出脱敏 1/2/3 在 `:555`、`:563`、`:573`；启动卡死修复 1/2 在 `:579`、`:584`、`:588`，其中脱敏 2 已按 `#ioExpPriv` 默认不勾改写）
- `desktop\test_smoke.py --skip-ball` → **388 PASS / 0 FAIL**
- `desktop\test_rules.py` → **84 PASS / 0 FAIL**
- `node --check desktop\web\app.js` exit=0
- 打包（cwd=desktop，`APPDATA=C:\Users\sunsi\AppData\Roaming`，`--onefile --noconsole --add-data web/extensions/WebView2Runtime`）⇒ `desktop\dist\洞鉴虚明v0.3.1.39dsk.exe` 21,279,744 字节

## 四、如实说明的限制

1. **顾问复核在本环境不可用**（已多次确认）：`advisor_stage.define_stage` 被 `previous-stage-unresolved` 拒绝（旧阶段 S1 状态 `invalidated/unverified`、`lastFailure=subject-read-disabled`）；`consult_task` 报 `advisor-invalid-citation`，三份材料 status 全为 `unavailable`、`reason=read-tools-disabled-or-no-cwd` —— 顾问读不到工程文件。所以本报告只有自测原始证据，**没有第三方验收结论**。
2. 整屏像素判据在 WebView2 全屏/合成平面提升后不可用（注入唯一底色后全屏态整屏 0 像素命中、非全屏态命中 167,058 采样点），相关结论改用窗口几何 + 页面自报 `screenX/outerWidth` 作主证据。
3. 用户现场那份 `shell_debug.log` 显示曾同时用 3 个 `_MEI` 目录启动多个实例（反复双击）；本修复消除的是新线程首建连接的 10 秒写锁等待，稳态下不再有那句写，多实例并发也不会再被它卡住。建议仍然一次只开一个窗口。
