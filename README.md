# 洞鉴虚明 (Mirror·Sorter)

A local-first multi-platform content sorter for Windows & Android. It aggregates
content from **Bilibili / Baidu Tieba / Xiaoheihe (HeyBox)**, filters and ranks it
with a rule engine plus AI (any OpenAI-compatible API), and serves an interactive
feed with built-in AI chat.

- **Single-file backend**: `app.py` uses only the Python standard library — zero third-party dependencies
- **Bundled frontend**: vanilla HTML/JS served directly by the local HTTP service
- **One codebase, two platforms**: desktop runs as-is; Android embeds the same Python service inside the APK

## Repository Layout

```
├─ desktop/   Desktop: local Python service + browser UI (app.py + web/ + one-click scripts)
├─ android/   Android: Chaquopy shell, APK ships with its own Python runtime, WebView loads the local service
├─ releases/  Build artifacts (APK / exe, git-ignored)
└─ .gitignore
```

> Login credentials (Bilibili cookies / Tieba BDUSS / HeyBox session) are stored
> only in the local data directory on each device. The repository contains no
> user data or secrets.

## Desktop

See [desktop/README.md](desktop/README.md). Quick start:

```bat
cd desktop
启动.bat          :: start the local service and open the browser (Python 3.10+)
构建exe.bat       :: build a single-file exe with PyInstaller into desktop\dist\
python test_smoke.py   :: regression tests
```

- Runtime data lives in `%LOCALAPPDATA%\LocalLow\MirrorSorter` (never pollutes the repo)
- Platform adapters: Bilibili Web API / Tieba mobile API / HeyBox signed API (with QR login)

## Android

See [android/README.md](android/README.md). Quick start:

```bat
android\build_apk.bat    :: sync desktop sources → Gradle build → artifact in releases\
```

- The one-click script syncs `app.py + web/` from `../desktop` into the APK assets
- Artifact: `releases/洞鉴虚明-<version>.apk` (~35MB, debug-signed, install directly)
- Versioning: Android versions end with `e` (e.g. `0.1.5.21e`), desktop with `a` (e.g. `0.2.6.3a`)

## Feedback & Community

For bug reports, feature requests and discussion, join our QQ group: **533068204**.
Please read the [feedback guide](FEEDBACK.md) first (common issues + report template).

## Disclaimer

- This project is for learning and personal use only. **Commercial use is strictly prohibited.**
- Content aggregation uses public APIs of each platform only. Please keep request rates low and respect the platforms' terms of service.
- AI features require your own OpenAI-compatible API endpoint (e.g. DeepSeek). Keys are stored locally only.

---

反馈与交流请加 QQ 群：**533068204**（反馈前请先看上方反馈指南）
