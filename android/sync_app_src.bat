@echo off
rem Sync desktop sources (../desktop) into Android assets before building.
setlocal
set SRC=%~dp0..\desktop
set DST=%~dp0app\src\main\assets\python_src

if not exist "%SRC%\app.py" (
  echo [ERROR] app.py not found in %SRC%
  exit /b 1
)

copy /y "%SRC%\app.py" "%DST%\app.py" >nul
if errorlevel 1 ( echo [ERROR] copy app.py failed & exit /b 1 )

for %%f in (app.js index.html style.css qrcode.js favicon.ico) do (
  if exist "%SRC%\web\%%f" copy /y "%SRC%\web\%%f" "%DST%\web\%%f" >nul
)

echo Synced: app.py + web/* into app/src/main/assets/python_src
endlocal
