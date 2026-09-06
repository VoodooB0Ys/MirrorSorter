# Feedback Guide

For bug reports and feature requests, join our QQ group: **533068204**

## Before You Report

1. **Update to the latest version** first — many issues are already fixed.
   (Desktop: check the version in the settings footer; Android: Settings → About.)
2. **Restart the app**: close and relaunch `洞鉴虚明.exe` on desktop; on Android,
   swipe the app away in recents and reopen.
3. **Check the common issues below** — you may be able to fix it yourself.

## What to Include

| Item | Notes |
|------|-------|
| Version | Desktop e.g. `v0.2.6.7a`, Android e.g. `0.1.5.28e` |
| Platform | Desktop (browser) or Android |
| Symptom | One-sentence description + screenshot (with error text if any) |
| Steps to reproduce | What you did, and whether it happens every time |
| Timing | Roughly when it happened (so logs can be cross-checked) |

## Common Issues

**Feed shows "加载失败：TypeError: Failed to fetch"**
The backend handler raised and dropped the connection. This was a known issue in
older builds (time-triggered on weekend mornings) — update to the latest version.
If it still happens, report it with the info above.

**One platform floods the feed / stuck at "内容池补充中" (pool rebuilding)**
"Pool rebuilding" is a normal waiting indicator: when other platforms haven't
fetched yet, the feed waits instead of padding with a single platform. Report it
only if it doesn't recover for several minutes.

**AI not working / no scores**
Check the AI master switch and API key in settings (any OpenAI-compatible
endpoint; keys stay on your device). For reasoning models, make sure max_tokens
is at least 1500 — otherwise reasoning eats the whole budget and the content
comes back empty.

**Tapping HeyBox cards opens the app home / does nothing**
HeyBox deep links rely on the official HeyBox app's `heybox://` protocol. Make
sure the official app is installed and updated; re-login in settings if Tieba /
HeyBox sessions have expired.

**Migrating to a new PC / phone**
Settings → Data → export settings, then import on the other device. The export
includes login credentials (cookies / BDUSS) — **never share the exported file
with anyone**.

## Privacy

- All data (history, favorites, login credentials, AI keys) stays on your device.
  Neither the repository nor update packages contain any user data.
- Please blur out cookies / BDUSS and other credentials before posting screenshots.

---

反馈与问题请加 QQ 群：**533068204**
