# 项目清理 + Git 版本控制接入报告

日期：2026-10-10
仓库：`https://github.com/Xblyymdf/MirrorSorter.git`
分支：`desktop-embedded-browser`
提交：`30a1690`

---

## 一、Git 版本控制

此前工作区代码已推进到 **v0.3.1.41dsk**，但最后一次提交停在 **v0.3.1.8dsk**，中间三十多个版本几乎没入库。
本次一次性补齐。

| 项目 | 数值 |
|---|---|
| 提交文件数 | 38 |
| 新增行 | 8,890 |
| 删除行 | 560 |
| 推送 | `origin/desktop-embedded-browser`（远程新建该分支） |

### `.gitignore` 重写要点
- 构建产物：`desktop/build/`、`desktop/dist/`、`releases/`、`android/**/build/`
- 临时探针：`desktop/_*`（含 `_tmp*/`、`_verify*/`、`_chk_pid*/` 等），白名单只放行 `desktop/_vis/REPORT_*.md`
- 用户数据：`desktop/data.db*`、`desktop/_tb_login_profile/`
- 本机环境：`.workbuddy/`、`.workbuddy-ai/`、`_local/`、`android-sdk/`
- 常见垃圾：`__pycache__/`、`*.py[cod]`、`*.log`、`desktop/*.spec`、`nul`

> 注意：`desktop/_probe_run.py` 是**已跟踪**文件，不受 `desktop/_*` 影响。

---

## 二、清理结果

按 `_cleanup_manifest_2026-10-10.txt` 回收 **A / B / C / D / F / H** 六类，**G 类探针脚本与 E 类产品文件全部保留**。

| 类别 | 内容 | 项数 |
|---|---|---|
| A | 构建产物/缓存（`build/`、`dist/`、`__pycache__/`） | 3 |
| B | 临时浏览器档案/测试数据目录 | 79 |
| C | 手工版本备份（`backups/`，有 git 后冗余） | 1 |
| D | 验证报告目录 `_vis/`（截图/日志，**保留 7 份 REPORT**） | 250 |
| F | 散落的探针产物（png/json/log/txt） | 410 |
| H | 杂项（旧 `*.spec`、怪名文件等） | 16 |
| **合计** | | **759 个顶层目标 / 约 4.5 GB** |

### 空间变化

| | 清理前 | 清理后 |
|---|---|---|
| 仓库总占用 | ~6.4 GB | **2.0 GB** |
| `desktop/` | ~3.0 GB | **269 MB** |

`dist/` 里原本堆了 **120 个历史 exe**（v0.2.6.7a → v0.3.1.41dsk），全部清除。

### 全部进回收站，可还原
`D:\$Recycle.Bin\S-1-5-21-...-1001`，本次共 7,982 条元数据命中本项目（7,198 条唯一路径）。
需要哪个文件，直接在回收站里还原即可；例如最新的
`desktop/dist/洞鉴虚明v0.3.1.41dsk.exe`。

---

## 三、保留项

- **G 类 221 个探针脚本**（用户明确要求保留）：已逐个比对确认，**一个不少**。
- **E 类产品文件**：`installer/`、`extensions/`、`web/`、`app.py`、`shell_*.py`、`README.md` 等，未动。
- `desktop/_mem_reqD18.md`：属 E 类，未勾选，保留。
- `desktop/tb_debug.jsonl`：不在清单内，保留（已被 `.gitignore` 覆盖）。

---

## 四、遗留与后续可选

1. **`desktop/nul`**（35 B，Windows 保留名）
   `os.remove` / `MoveFileExW` 均返回 ERROR 5；本环境禁止调用 `cmd.exe`，无法用 `del \\?\...\nul`。
   已被 `.gitignore` 覆盖，无害。若想清掉，在资源管理器里手动删除即可。

2. **可进一步清理的大块**（均为已忽略内容，本次未勾选）
   - `android-sdk/` — 878 MB
   - `releases/` — 553 MB（9 月的旧 APK 与 v0.2.8.5a 旧 exe）
   - `_local/` — 72 MB

---

## 五、验证

- `git status --porcelain -uall` → **0 条**（工作区干净）
- 产品文件完整性抽查：`app.py`(704 KB)、`web/index.html`(48 KB)、`web/app.js`(160 KB)、
  `web/style.css`(63 KB)、`extensions/bilibili-cfilter.user.js`(34 KB)、`installer/app_installer.nsi`、
  `shell_float.py`、`test_shell.py`、`README.md` 全部在位。
- `desktop/_vis/` 仅剩 7 份 `REPORT_*.md`。
