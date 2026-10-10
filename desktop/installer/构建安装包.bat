@echo off
chcp 936 >nul
cd /d "%~dp0.."

rem ══ 洞鉴虚明 安装包一键构建 ════════════════════════════════════════
rem 版本号唯一来源: web/index.html 页脚(与 app.py APP_VER 保持一致)。
rem 本脚本自动: 读版本 → PyInstaller onedir 载荷 → NSIS 打包(版本自动传入)。
rem 产物: dist\洞鉴虚明vX.Y.Zdsk_安装包.exe
rem ══════════════════════════════════════════════════════════════════

python -c "import re,io;t=io.open(r'web/index.html',encoding='utf-8').read();m=re.search(r'<footer>(.*?)</footer>',t);v=(m.group(1).split('<')[0].replace('洞鉴虚明','').strip() if m else '');io.open(r'_ver.tmp','w',encoding='ascii').write(v)"
set /p VER=<_ver.tmp
del _ver.tmp >nul 2>nul
if not defined VER (
  echo [错误] 未能从 web/index.html 页脚解析版本号, 请检查页脚格式: 洞鉴虚明 vX.Y.Zdsk
  pause
  exit /b 1
)
echo 版本: %VER%

if not exist "installer\redist\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" (
  echo [错误] 缺少 installer\redist\MicrosoftEdgeWebView2RuntimeInstallerX64.exe
  echo        ^(微软官方 WebView2 离线运行时, 从 https://go.microsoft.com/fwlink/p/?LinkId=2124703 下载^)
  pause
  exit /b 1
)
if not exist "installer\tools\NSIS-3.11\Bin\makensis.exe" (
  echo [错误] 缺少 installer\tools\NSIS-3.11\Bin\makensis.exe
  pause
  exit /b 1
)

echo [1/3] PyInstaller onedir 载荷(installer-app.spec)...
python -m PyInstaller installer-app.spec --noconfirm --distpath dist
if errorlevel 1 (
  echo [错误] onedir 打包失败。
  pause
  exit /b 1
)
if not exist "dist\洞鉴虚明\洞鉴虚明.exe" (
  echo [错误] 未产出 dist\洞鉴虚明\洞鉴虚明.exe
  pause
  exit /b 1
)

echo [2/3] NSIS 编译安装包(APPVER=%VER%)...
echo     ^(压缩约需 1-3 分钟, 期间无进度输出属正常, 请勿关闭^)
cd installer
tools\NSIS-3.11\Bin\makensis.exe /DAPPVER=%VER% app_installer.nsi
if errorlevel 1 (
  echo [错误] 安装包编译失败。
  pause
  exit /b 1
)
cd ..

echo.
echo [3/3] 完成！安装包: dist\洞鉴虚明%VER%_安装包.exe
pause
