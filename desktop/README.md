# Mirror·Sorter · Desktop

> Current version follows the `web/index.html` footer (e.g. v0.2.9.0a). Android lives in `android/`.

A locally-running content sorter: aggregates Bilibili / Baidu Tieba / HeyBox
content → rule + AI filtering → interactive feed + source status panel + AI chat.
**Zero third-party backend dependencies** (server is Python standard library only);
all data stays in the user directory (`%LOCALAPPDATA%\LocalLow\MirrorSorter`) —
the repo itself never stores user data.

**Since v0.2.9 the desktop runs in its own app window with a built-in browser:**
the window is powered by [pywebview](https://github.com/r0x0r/pywebview) (MIT)
on the system's Edge WebView2 runtime. Clicking any video/post opens it as a
**tab inside the same window** — no external browser, no extra windows.

## Quick Start (recommended)

| Action | What it does | Notes |
|---|---|---|
| Double-click `启动.bat` | starts the service → opens the **app window** | requires Python + WebView2 (preinstalled on Win10/11) |
| Double-click `dist\洞鉴虚明v0.2.9.0a.exe` | same, standalone single file | **no Python needed**, portable to any Windows PC |
| Double-click `启动-服务模式.bat` | old headless mode → opens your default browser | fallback / advanced use |
| Double-click `停止.bat` | stops all related processes | or kill via Task Manager |
| Double-click `构建exe.bat` | rebuild the exe | run after changing code |

Double-clicking again is safe: it detects the running service and just opens the window.
> ⚠ PyInstaller builds occasionally trigger antivirus false positives — if so, add `dist\洞鉴虚明v0.2.9.0a.exe` to the trust list.

## Built-in Browser Tabs (v0.2.9+)

Clicking a feed card, message entry, account link, history/saved item — anything
that used to open your system browser — now opens as a **tab in the app window**:

- A native browser-style **tab bar sits at the top of the frameless window**: `信息流`
  is the pinned home tab (cannot be closed); every video/post opens as a new tab
  showing the page title with a hover `✕` to close; click a tab to switch. Site
  pop-ups (target=_blank) become tabs too. Tabs render browser-style (rounded top,
  accent bar on the active tab).
- **Pages follow the app's dark/light theme and accent color**: with 黑夜 mode on,
  Bilibili switches to **its own native dark theme** (the shell sets the same
  `theme_style` cookie the site's own toggle writes) — no blinding white pages and
  no cheap color inversion; other sites get an auto-dark fallback. The tab accent
  bar follows your accent color from the app settings, and theme/accent are
  restored inside the shell on first launch from the settings store.
- **Switching tabs never reloads**: each tab is its own WebView2 instance sharing
  the same browser profile — the feed keeps its state, videos keep playing when
  you switch away and back.
- The window is borderless: drag the tab bar's empty area to move, `─ □ ✕` window
  buttons at the right end, and the window edges (left/right/bottom, plus the top
  edge of the tab bar) resize it.
- The tab bar is part of the window chrome (WinForms-drawn) — it never overlaps or
  blocks any website's own UI.
- Site logins (e.g. Bilibili) persist across restarts (WebView2 non-private mode).
- No WebView2 runtime on the machine? The app falls back to the old
  external-browser mode automatically (with a one-time notice) — never a blank window.

## UI Overview

- **Left · settings rail**: AI config (OpenAI-compatible API, DeepSeek by default) / preference notes / black & white lists (keywords · authors) / data management; collapsible to a narrow rail
- **Top bar**: filter level (strict/standard/relaxed), search, history, saved, account menu
- **Middle · feed**: cards = platform badge + title + summary + author/heat/time + **AI recommendation reason** + 👍👎⏳🚫 + open on platform; clicking a card opens the original post and records it into history
- **Large panel**: data source status (per-platform status / today's intake / filtered counts / AI calls / cost) + history + saved
- **Right · AI chat**: tell the AI what you like; chat signals feed into future scoring

## Refresh & New Content

- The "↻" button **force-fetches and rotates sources** (overall ranking / hot pages 1-3 / partition rankings, 4 sources per round), so every refresh brings new content
- Works without AI configured: ranking = frequently-watched authors > fresh faces > personal frequent keywords > popularity

## Bilibili Account (optional, improves recommendations)

"Account → Bilibili account":

1. Click "Open Bilibili login page" → open bilibili.com in a browser where you are already logged in
2. Press F12 → Network → refresh the page → click any request → copy the whole Cookie from Request Headers
3. Paste it into the input box → "Save & login"

The app then reads your **watch history / favorites** and builds a profile
(frequent authors / frequent keywords / favorites): without AI it weights the
ranking; with AI it is injected into the scoring context. Cookies are stored
only in the local data.db.

## AI Config (optional)

Without configuration: ranked by popularity, fully functional (no recommendation reasons / AI filtering).
Once configured: flame/low-quality score filtering (level = threshold) + personalized recommendation reasons + AI chat.

1. Left settings → API URL (default `https://api.deepseek.com/v1`, any OpenAI-compatible service works)
2. Enter an API key (DeepSeek gives free credit on signup; Zhipu / Qwen also work — just change URL + model)
3. Save → refresh the feed; today's AI cost shows in the right panel

## AI Floating Ball (Windows desktop only)

Left settings → "AI 悬浮窗" → enable: a small always-on-top ball stays on the screen
over any app (position is remembered; right-click the ball to exit; it disappears
automatically if the service stops). The ball is a rotating **wireframe dodecahedron
hologram** (20 vertices / 30 edges / 12 pentagon faces) drawn in real 3D with
depth-cued edges, edge bloom and scanlines; its color follows the main UI accent.

**Clicking the ball expands it in place into a compact chat panel** (no separate
window; "─ 收起" collapses it back into the ball). Hovering only highlights the ball
— it never opens the panel on its own; opening is click-only. Drag the ball onto a
screen edge and release to tuck it away: the ball slides off-screen and a thin
restore strip stays on that edge (click the strip to pull the ball back). The panel
is a chat stream with two modes above the input box:

- **验证真实性 (Fact check)**: paste a comment/claim → the service searches the web
  (360 → Bing → DDG fallback chain, credibility-ranked domains), reads the top pages and
  the AI returns a verdict (true / false / uncertain) with confidence, reasons,
  the knowledge errors / logical fallacies it spotted, and clickable evidence links
- **生成回怼 (Clapback)**: paste a comment to rebut → the AI first names the other side's
  knowledge errors and logical fallacies, then writes first-person clapback lines
  (addressing "you", never advising the user): N one-liners + N paragraphs + 1 long
  piece, where N is the gear setting (1/2/3). **Each part is shown the moment it is
  generated** — no waiting for the whole run — and every line is one-click copyable

Gear drawer (⚙): thinking depth (快/标准/深挖), savagery (温和/毒舌/阴阳, the top gear
uses spicier forum-style wording) and count (1/2/3, which only changes how many
one-liners and paragraphs you get; the long piece is always 1).

Both modes need the AI config above; results carry an "offline" warning when no web
evidence could be retrieved. Nothing about your browser is read — the service fetches
public pages itself.

## Run from Source

```
cd desktop
python app_window.py     # app window (built-in browser tabs) — same as 启动.bat
python app_window.py --server   # headless service + default browser (old behavior)
python app.py            # equivalent headless service (CLI)
# windowless access: http://127.0.0.1:8000
```

## Layout

```
desktop/
├── app.py            # backend: fetching + AI scoring + SQLite + all APIs + local server
├── app_window.py     # window shell (exe entry): frameless pywebview window + tabs
├── shell_tabstrip.py # native browser-style tab bar (also the borderless title bar)
├── shell_pages.py    # per-tab WebView2 control pool (switching never reloads)
├── web/index.html    # UI skeleton
├── web/style.css     # styles
├── web/app.js        # frontend logic (openExternal routes links into shell tabs)
├── test_shell.py     # shell/tab-manager smoke tests (no GUI)
├── data.db           # data (auto-created; delete to reset)
├── 启动.bat / 启动-服务模式.bat / 停止.bat / 构建exe.bat
└── dist\洞鉴虚明v0.2.9.0a.exe  # standalone single-file build
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "未检测到 WebView2 运行时" notice | install the [WebView2 Evergreen runtime](https://developer.microsoft.com/microsoft-edge/webview2/) — Win10/11 usually have it already |
| Page shows "offline sample data" | no network or Bilibili rate-limiting; click "Refresh now" and retry later |
| AI calls fail | check API URL / key / model in settings; DeepSeek keys start with `sk-` |
| Start over | delete `data.db` (or Account → clear all data) |
| Port busy | automatically falls back 8000 → 8010 → 8020 |
