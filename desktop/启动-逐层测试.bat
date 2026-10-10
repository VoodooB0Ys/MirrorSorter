@echo off
chcp 936 >nul
cd /d "%~dp0"
title 洞鉴虚明 - 视频截屏黑屏 逐层排查

echo ================================================================
echo   视频截屏黑屏 ^· 逐层加回（每次选一个数字；测完关掉程序再重开）
echo ================================================================
echo.
echo   1 = 纯网页播放（不注入任何脚本 / 不加浏览器参数）        [已知正常]
echo   2 = 默认配置（视频零 CSS：兜底CSS关 + 画质增强滤镜关）    ^<^< 先试这个
echo   3 = 在 2 的基础上，只加回画质增强滤镜（看它是否是元凶）
echo   4 = 在 2 的基础上，只加回兜底CSS(opacity/filter)
echo   5 = 老样子（兜底CSS + 画质增强 全开）—— 对照 v0.3.1.10
echo   6 = 默认 + 不使用硬件视频解码（保全部功能的候选修法）
echo   7 = 默认 + 关掉两个 DComp overlay 特性（引擎级候选修法）
echo.
set /p CH=请输入 1-7 后回车:

rem ---------- 找 dist 下最新的 exe ----------
set "EXE="
for /f "delims=" %%f in ('dir /b /o-d "dist\*.exe" 2^>nul') do if not defined EXE set "EXE=dist\%%f"
if not defined EXE (
  echo [错误] 没找到 dist\*.exe，请先构建。
  pause
  exit /b 1
)

if "%CH%"=="1" (
  set "MSX_BASIC=1"
) else if "%CH%"=="2" (
  rem 默认配置：什么都不用设（源码默认即视频零 CSS）
) else if "%CH%"=="3" (
  set "MSX_BQ=1"
) else if "%CH%"=="4" (
  set "MSX_OVERLAY_CSS=1"
) else if "%CH%"=="5" (
  set "MSX_BQ=1"
  set "MSX_OVERLAY_CSS=1"
) else if "%CH%"=="6" (
  set "MSX_NO_WV2_FLAG=1"
  set "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--disable-accelerated-video-decode"
) else if "%CH%"=="7" (
  set "MSX_NO_WV2_FLAG=1"
  set "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--disable-features=kDirectCompositionLetterboxVideoOptimization,kDirectCompositionUnlimitedOverlays"
) else (
  echo 输入无效：%CH%
  pause
  exit /b 1
)

echo.
echo 正在用 [选项 %CH%] 启动: %EXE%
start "" "%EXE%"
echo.
echo 请在播放视频时用 QQ 截图试一下，然后关掉程序、再运行本脚本选下一个数字。
timeout /t 4 /nobreak >nul
exit /b 0
