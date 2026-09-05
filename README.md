# 洞鉴虚明 (Mirror·Sorter)

本地优先的多平台内容分拣器：聚合 **B站 / 百度贴吧 / 小黑盒** 三平台内容，
经规则引擎 + AI（OpenAI 兼容接口）筛选排序，输出可交互的推荐流，内置 AI 聊天。

- **单文件后端**：`app.py` 只用 Python 标准库，零第三方依赖
- **前后端一体**：前端为原生 HTML/JS，由本地 HTTP 服务直接伺服
- **一套代码，两端运行**：电脑版直接跑；安卓版把同一套 Python 服务嵌进 APK

## 仓库结构

```
├─ desktop/   电脑版：Python 本地服务 + 浏览器界面（app.py + web/ + 一键脚本）
├─ android/   安卓版：Chaquopy 壳，APK 内自带 Python 运行时，WebView 加载本地服务
├─ releases/  打包产物输出目录（APK / exe，git 忽略）
└─ .gitignore
```

> 登录凭证（B站 Cookie / 贴吧 BDUSS / 小黑盒登录态）只存在本机数据目录，
> 仓库源码不包含任何用户数据与密钥。

## 电脑版

见 [desktop/README.md](desktop/README.md)。快速开始：

```bat
cd desktop
启动.bat          :: 启动本地服务并自动打开浏览器 (需 Python 3.10+)
构建exe.bat       :: PyInstaller 打包单文件 exe 到 desktop\dist\
python test_smoke.py   :: 回归测试
```

- 运行数据存于 `%LOCALAPPDATA%\LocalLow\MirrorSorter`（不污染仓库目录）
- 平台采集：B站 Web API / 贴吧移动接口 / 小黑盒签名 API（含扫码登录）

## 安卓版

见 [android/README.md](android/README.md)。快速开始：

```bat
android\build_apk.bat    :: 同步桌面版源码 → Gradle 构建 → 产物到 releases\
```

- 一键脚本自动从 `../desktop` 同步 `app.py + web/` 进 APK assets
- 产物：`releases/洞鉴虚明-<版本号>-debug.apk`（约 35MB，debug 签名直接安装）
- 版本命名：安卓版以 `e` 结尾（如 `0.1.5.21e`），桌面版以 `a` 结尾（如 `0.2.6.3a`）

## 声明

- 本项目仅供学习交流，**严禁商用**
- 采集仅限各平台公开接口，请控制频率、遵守平台条款
- AI 功能需自行配置 OpenAI 兼容接口（如 DeepSeek），Key 只存本机
