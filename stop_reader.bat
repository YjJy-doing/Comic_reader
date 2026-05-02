@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "STOPPED=0"

if exist "data\server.pid" (
    set /p PID=<"data\server.pid"
    if not "%PID%"=="" (
        taskkill /PID %PID% /F >nul 2>nul
        if not errorlevel 1 set "STOPPED=1"
    )
    del /f /q "data\server.pid" >nul 2>nul
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$targets = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(\\.exe|w\\.exe)?$' -and $_.CommandLine -match 'app\\.py' -and $_.CommandLine -match '--port\\s+7878' }; foreach ($p in $targets) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; if ($targets.Count -gt 0) { exit 0 } else { exit 1 }"
if not errorlevel 1 set "STOPPED=1"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":7878 .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>nul
    if not errorlevel 1 set "STOPPED=1"
)

if "%STOPPED%"=="1" (
    echo 已停止漫画阅读器后台服务。
) else (
    echo 未检测到正在运行的后台服务。
)

pause
