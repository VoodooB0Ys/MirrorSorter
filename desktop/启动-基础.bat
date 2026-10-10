@echo off
chcp 936 >nul
cd /d "%~dp0"

rem ===== 基础模式: 纯 WebView2 加载B站网页(不注入任何脚本 / 不加浏览器参数) =====
rem 等同旧版 2.9 的"最基础网页播放"; 用来确认底子, 再逐层把 广告/超分/自定义UI 加回来。
set "MSX_BASIC=1"

rem 优先用已打包的最新 exe(双击即可), 找不到再回退到源码运行
set "EXE="
for /f "delims=" %%f in ('dir /b /o-d "dist\*.exe" 2^>nul') do if not defined EXE set "EXE=dist\%%f"

if defined EXE (
  start "" "%EXE%"
  echo 已用【基础模式】启动: %EXE%
  timeout /t 2 /nobreak >nul
  exit /b 0
)

set "PYCMD="
where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD ( where py >nul 2>nul && set "PYCMD=py -3" )
if not defined PYCMD (
  echo [错误] 未找到 dist 下的 exe, 也未检测到 Python。
  pause
  exit /b 1
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw app_window.py
) else (
  start "" /min %PYCMD% app_window.py
)
echo 已用【基础模式】启动(源码)。
timeout /t 2 /nobreak >nul
exit /b 0
