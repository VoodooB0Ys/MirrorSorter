@echo off
chcp 936 >nul
cd /d "%~dp0.."

rem ══ 洞鉴虚明 安装包一键构建 ════════════════════════════════════════
rem 版本号唯一来源: web/index.html 页脚(与 app.py APP_VER 保持一致)。
rem 本脚本自动: 选 Python → 备好 PyInstaller → onedir 载荷 → NSIS 打包(版本自动传入)。
rem 产物: dist\洞鉴虚明vX.Y.Zdsk_安装包.exe
rem
rem ★ 为什么不再用 installer-app.spec (v0.3.1.46dsk 修):
rem   .gitignore 把 desktop/*.spec 当"每次构建自动生成"的产物忽略掉, 所以那个
rem   spec **从来不在库里** —— 一次清理或换台机器就没了, 本脚本随即在 [1/3]
rem   报 "Spec file not found" 直接失败(用户报的就是这个)。
rem   现在改成**纯命令行参数**建载荷, 与 构建exe.bat 完全同一个思路:
rem   不依赖任何未入库的文件, 换机器/清理后照样能跑。
rem ★ 改打包参数时请与 构建exe.bat 同步 —— 两条路径必须等价, 否则会产出坏包。
rem ══════════════════════════════════════════════════════════════════

rem ── 选 Python ──
rem 优先项目约定的打包用 3.14(第三方库只保证有 cp314 wheel, 见项目笔记),
rem 再退回 PATH 上的 python / py -3 —— 换台机器没有 C:\Python314 也能用。
rem ★ 不能用裸 python: 机器上的 python 可能解析到别的解释器(实测解析到没装
rem   PyInstaller 的那个, 直接报 "No module named PyInstaller")。
set "PYCMD="
if exist "C:\Python314\python.exe" set "PYCMD=C:\Python314\python.exe"
if not defined PYCMD ( where python >nul 2>nul && set "PYCMD=python" )
if not defined PYCMD ( where py >nul 2>nul && set "PYCMD=py -3" )
if not defined PYCMD (
  echo [错误] 未检测到 Python。请先安装: https://www.python.org/downloads/
  echo 安装时记得勾选 "Add Python to PATH"
  pause
  exit /b 1
)
echo Python: %PYCMD%

%PYCMD% -c "import re,io;t=io.open(r'web/index.html',encoding='utf-8').read();m=re.search(r'<footer>(.*?)</footer>',t);v=(m.group(1).split('<')[0].replace('洞鉴虚明','').strip() if m else '');io.open(r'_ver.tmp','w',encoding='ascii').write(v)"
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

rem ── PyInstaller: 先查再用, 缺了才装 ──
rem 旧写法是无条件 pip install --upgrade, 断网时**已经装好也会失败**;
rem 现在已装就直接过, 只有真缺了才联网。
%PYCMD% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo [0/3] 未检测到 PyInstaller, 正在安装 ...
  %PYCMD% -m pip install --upgrade pyinstaller
  if errorlevel 1 (
    echo [错误] PyInstaller 安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
) else (
  echo [0/3] PyInstaller 已就绪
)

echo [1/3] PyInstaller onedir 载荷(约 1-2 分钟, 请勿关闭本窗口)...
rem --onedir: 安装包要的是**目录**载荷(NSIS 用 File /r 整目录收进去), 不是 onefile。
rem --upx-exclude: 58MB 的模型不该被 UPX 反复尝试压缩(与 spec 里的 upx_exclude 等价)。
%PYCMD% -m PyInstaller --noconfirm --clean --onedir --noconsole ^
  --add-data "web;web" --add-data "extensions;extensions" ^
  --add-data "WebView2Runtime;WebView2Runtime" --add-data "model;model" ^
  --hidden-import ftrt --icon djxm_icon_128x128.ico ^
  --upx-exclude ft_model.bin ^
  --name 洞鉴虚明 app_window.py
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
