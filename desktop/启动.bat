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
  start "" pythonw app.py --auto-open
) else (
  start "" /min %PYCMD% app.py --auto-open
)

echo 洞鉴虚明已启动，浏览器应自动打开页面。
echo 如未自动打开，请手动访问 http://127.0.0.1:8000
timeout /t 2 /nobreak >nul
exit /b 0
