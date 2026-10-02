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

echo [1/2] 检查/安装 PyInstaller ...
%PYCMD% -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo [错误] PyInstaller 安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo [2/2] 打包中（约 1-2 分钟，请勿关闭本窗口）...
rem 从 web/index.html 页脚读取版本名(如 "洞鉴虚明 v0.2.6.0a")作为 exe 名(去空格)
%PYCMD% -c "import re,io;t=io.open(r'web/index.html',encoding='utf-8').read();m=re.search(r'<footer>(.*?)</footer>',t);io.open(r'web\_appname.txt','w',encoding='gbk').write((m.group(1).split('<')[0].replace(' ','') if m else ''))"
set /p "APPNAME=" < web\_appname.txt
del web\_appname.txt >nul 2>nul
if not defined APPNAME set "APPNAME=洞鉴虚明"
echo 版本名: %APPNAME%
%PYCMD% -m PyInstaller --noconfirm --clean --onefile --noconsole --add-data "web;web" --icon djxm_icon_128x128.ico --name %APPNAME% app_window.py
if errorlevel 1 (
  echo.
  echo [错误] 打包失败，请把上面的报错信息截图反馈。
  pause
  exit /b 1
)

echo.
echo 完成！双击运行: dist\%APPNAME%.exe
pause
