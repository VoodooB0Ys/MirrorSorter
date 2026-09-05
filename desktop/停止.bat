@echo off
chcp 936 >nul
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { ($_.Name -match 'python' -and $_.CommandLine -match 'app\.py') -or $_.Name -match '信息分拣器' -or $_.Name -match '洞鉴虚明' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('已停止 ' + @($p).Count + ' 个进程') } else { Write-Host '没有正在运行的洞鉴虚明' }"
echo.
pause
