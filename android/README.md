# Mirror·Sorter · Android (Chaquopy shell)

Embeds the desktop app (`desktop/app.py` + `web/`) unchanged into an Android app:
the APK ships its own Python 3.12 runtime, unpacks the Python sources into the
app's private directory at startup, runs the local HTTP service on a background
thread, and loads `http://127.0.0.1:8000` in a fullscreen WebView. Zero changes
to the business code.

## Layout

```
android/
├─ app/build.gradle                 # Chaquopy 15.0.1 + AGP 8.2.2, Python 3.12
├─ app/src/main/
│  ├─ AndroidManifest.xml           # INTERNET permission + cleartext (127.0.0.1 only)
│  ├─ java/com/mirrorsorter/app/MainActivity.java
│  │                                # unpack assets → launcher.start() → poll port → WebView
│  ├─ python/launcher.py            # Python bootstrap: env vars / patches / runs app.py in a thread
│  ├─ assets/python_src/            # mirror of desktop sources (app.py + web/*), synced by script before build
│  └─ res/mipmap-*/ic_launcher.png  # generated from desktop/djxm_icon_128x128.ico
├─ sync_app_src.bat                 # syncs latest desktop sources into assets
└─ gradlew.bat                      # wrapper (download mirror: Tencent)
```

## One-click Build

Run `android\build_apk.bat`: syncs desktop sources → Gradle build → copies the
APK to `releases/` at the repo root as `洞鉴虚明-<version>.apk` (version read
automatically from `versionName` in `app/build.gradle`).

Versioning convention: Android versions end with **e** (e.g. `0.1.1e`), desktop
with **a** (e.g. `0.2.6.2a`); bump versionCode/versionName in `app/build.gradle`
on release.

Manual build (Git Bash):

```bash
export JAVA_HOME='D:\INFORM~1\ANDROI~1\JDK-17~1.1_1'
cd "D:\Information Sorter\android"
cmd //c sync_app_src.bat          # sync latest desktop sources first
cmd //c "gradlew.bat assembleDebug --no-daemon"
```

## Local Build Environment (installed, do not delete)

| Component | Location |
|---|---|
| JDK 17 | `D:\Information Sorter\android-sdk\jdk-17.0.20.1+1` |
| Android SDK (platform 34 / build-tools 34) | `D:\Information Sorter\android-sdk` |
| Gradle 8.5 | `D:\Information Sorter\android-sdk\gradle-8.5` |

Artifact: `releases/洞鉴虚明-<version>.apk` (~35MB, debug-signed, installable directly).

Note: the local path contains spaces, so env vars / `local.properties` use 8.3
short paths (`D:\INFORM~1\ANDROI~1`) to dodge cmd quoting issues;
`gradle.properties` / wrapper are preconfigured.

## Install on a Phone

Copy the APK to the phone and open it (allow "unknown sources"), or:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## How It Works & Adaptations

- **Data directory**: `launcher.py` sets `MIRRORSORTER_DATA_DIR=<filesDir>/data`,
  natively supported by app.py's DATA_DIR override → settings / logins / history
  all live in app-private storage.
- **No browser popup**: `launcher.py` omits `--auto-open` from `sys.argv` and
  patches `webbrowser.open` to a no-op.
- **External links**: the WebView hands all non-127.0.0.1 URLs and
  `window.open(_blank)` to the system browser (Bilibili video pages, Tieba posts, etc.).
- **Self-contained QR login**: a phone cannot scan its own screen, so on touch
  devices `web/app.js` shows a "📱 login on this device" button under the
  Bilibili / HeyBox QR code → opens the system browser → confirms inside the
  corresponding app → the shell polls and completes login automatically.

## Known Limitations (TODO)

1. **Tieba login**: implemented via an in-app WebView login page (LoginActivity,
   captures BDUSS + nickname/avatar via newmoindex); cookies are cleared each
   time the login page opens, so switching accounts is painless.
2. **No background fetching**: the OS may kill the process in the background;
   manual open-and-refresh works fine. Scheduled auto-fetching would need a
   foreground service (persistent notification).
3. **Debug signature**: a release keystore + `assembleRelease` is needed before
   proper distribution.
4. **Icon**: plain scaled square, no adaptive rounded / maskable variant.

## Added in 0.1.3e

- **Chat edge gestures**: swipe left from the right screen edge to open AI chat;
  swipe right from the left edge inside chat to go back. Bottom tabs are now
  Home / Records / Settings only, with a back button in the chat header as fallback.
- **Settings import/export**: settings rail → data management → export/import.
  Exports include AI config / filtering rules / platform weights / preference
  notes / black & white lists / UI preferences; login credentials are excluded
  by default (opt-in, lets the importing device skip QR login). On Android the
  import reads via an AndroidBridge clipboard bridge, smart-parses JSON out of
  mixed text, and merges only the selected categories.
