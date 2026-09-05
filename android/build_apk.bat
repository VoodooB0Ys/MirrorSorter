@echo off
python "%~dp0build_apk.py"
if errorlevel 1 pause
