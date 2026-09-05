# 洞鉴虚明 · 安卓版 (Chaquopy 壳)

把桌面版 (`desktop/app.py` + `web/`) 原样嵌进安卓应用: APK 内自带 Python 3.12
运行时, 启动时把 Python 源码解包到应用私有目录, 在后台线程跑起本地 HTTP 服务,
再用 WebView 全屏加载 `http://127.0.0.1:8000`。业务代码零改动。

## 目录结构

```
android/
├─ app/build.gradle                 # Chaquopy 15.0.1 + AGP 8.2.2, Python 3.12
├─ app/src/main/
│  ├─ AndroidManifest.xml           # INTERNET 权限 + cleartext(仅127.0.0.1)
│  ├─ java/com/mirrorsorter/app/MainActivity.java
│  │                                # assets解包 → launcher.start() → 轮询端口 → WebView
│  ├─ python/launcher.py            # Python 启动器: 设环境变量/打补丁/线程跑 app.py
│  ├─ assets/python_src/            # 桌面版源码镜像(app.py + web/*), 构建前由脚本同步
│  └─ res/mipmap-*/ic_launcher.png  # 由 desktop/djxm_icon_128x128.ico 生成
├─ sync_app_src.bat                 # 从 ../desktop 同步最新源码到 assets
└─ gradlew.bat                      # Wrapper(下载源已改腾讯镜像)
```

## 一键打包

双击或运行 `android\build_apk.bat` 即可：自动同步桌面版源码 → 构建 →
把 APK 复制到仓库根的 releases/，命名 `洞鉴虚明-<版本号>-debug.apk`（版本号自动读自
`app/build.gradle` 的 versionName）。

版本命名约定：安卓版版本号以 **e** 结尾（如 `0.1.1e`），与桌面版的 a 结尾
（如 `0.2.6.2a`）区分；发版时改 `app/build.gradle` 里的 versionCode/versionName。

手动构建 (Git Bash):

```bash
export JAVA_HOME='D:\INFORM~1\ANDROI~1\JDK-17~1.1_1'
cd "D:\Information Sorter\android"
cmd //c sync_app_src.bat          # 先同步桌面版最新源码
cmd //c "gradlew.bat assembleDebug --no-daemon"
```

## 本机构建环境(已装好, 勿删)

| 组件 | 位置 |
|---|---|
| JDK 17 | `D:\Information Sorter\android-sdk\jdk-17.0.20.1+1` |
| Android SDK (platform 34 / build-tools 34) | `D:\Information Sorter\android-sdk` |
| Gradle 8.5 | `D:\Information Sorter\android-sdk\gradle-8.5` |

产物: `releases/洞鉴虚明-<版本号>-debug.apk` (约 35MB, debug 签名可直接安装)。

注意: 本机路径含空格, 环境变量/`local.properties` 均用 8.3 短路径
(`D:\INFORM~1\ANDROI~1`)绕过 cmd 的引号问题; `gradle.properties`/wrapper 已配好。

## 安装到手机

APK 传到手机直接点开安装 (需允许"未知来源"), 或:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## 运行原理与已适配项

- **数据目录**: `launcher.py` 设 `MIRRORSORTER_DATA_DIR=<filesDir>/data`,
  app.py 的 DATA_DIR 覆盖机制原生支持 → 设置/登录/历史全部存应用私有目录。
- **不弹浏览器**: `launcher.py` 里 `sys.argv` 不带 `--auto-open` 并给
  `webbrowser.open` 打了空补丁。
- **外链跳转**: WebView 把非 127.0.0.1 的 URL 和 `window.open(_blank)`
  全部转交系统浏览器 (B站视频页/贴吧帖等)。
- **本机扫码登录**: 手机上无法"自己扫自己", `web/app.js` 在触屏设备上会在
  B站/小黑盒二维码下方显示「📱 本机登录」按钮 → 打开系统浏览器 →
  跳进对应 App 确认 → 壳内轮询自动完成登录。

## 已知限制(待做)

1. **贴吧登录**: 已用应用内 WebView 登录页实现 (LoginActivity, 抓 BDUSS +
       newmoindex 取昵称/头像); 每次打开登录页自动清 Cookie, 可随意换号。
2. **无后台抓取**: 进程退到后台可能被系统回收, 纯手动打开刷新没问题;
   需要"定时自动抓"得加前台服务 (通知栏常驻)。
3. **debug 签名**: 正式分发前应做 release 签名 (keystore + `assembleRelease`)。
4. **图标**: 直接缩放的方图, 未做自适应圆角/maskable。

## 版本 0.1.3e 新增

- **聊天边缘手势**: 手机上从屏幕右缘向左滑打开 AI 聊天, 聊天里左缘向右滑返回;
  底部 Tab 只剩 首页/记录/设置, 聊天页左上角有返回按钮兜底。
- **设置导入导出**: 设置栏 → 数据管理 → 设置导入/导出。导出含 AI 配置/筛选规则/
  平台权重/偏好备注/黑白名单/界面偏好, 登录凭证默认不导(勾选后导入方可免扫码登录)。
  手机端经 AndroidBridge 剪贴板桥一键读取, 智能解析(可从夹杂文本中抠出 JSON)后
  按类勾选合并导入, 只覆盖勾选项。
