@echo off
chcp 936 >nul
cd /d "%~dp0"

set "PYCMD="
where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD ( where py >nul 2>nul && set "PYCMD=py -3" )
if not defined PYCMD (
  echo [错误] 未检测到 Python。请先安装: https://www.python.org/downloads/
  echo 安装时记得勾选 "Add Python to PATH"
  pause
  exit /b 1
)

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw app_window.py
) else (
  start "" /min %PYCMD% app_window.py
)

echo 洞鉴虚明已启动，应用窗口应自动弹出(内置浏览器版)。
echo 关闭应用窗口即退出服务；如需外部浏览器访问: python app.py --auto-open
timeout /t 2 /nobreak >nul
exit /b 0
