@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "data" mkdir "data"

where python >nul 2>nul
if errorlevel 1 (
	echo [错误] 未找到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH。
	pause
	exit /b 1
)

python -c "import flask, waitress, rapidocr_onnxruntime" >nul 2>nul
if errorlevel 1 (
	echo 检测到依赖缺失，正在安装...
	python -m pip install -r requirements.txt --quiet
	if errorlevel 1 (
		echo [错误] 依赖安装失败，请手动执行: python -m pip install -r requirements.txt
		pause
		exit /b 1
	)
)

set "LIB=%~1"
if "%LIB%"=="" set "LIB=..\一人之下_漫画"
set "SCRIPT_DIR=%CD%"

echo.
echo 使用漫画目录: %LIB%

call :check_running
if not errorlevel 1 (
	echo 检测到服务已在运行，正在打开浏览器...
	start "" "http://127.0.0.1:7878"
	exit /b 0
)

echo 正在后台启动服务...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $argsList=@('app.py','--library',$env:LIB,'--host','127.0.0.1','--port','7878','--engine','auto'); $workDir=$env:SCRIPT_DIR; if ([string]::IsNullOrWhiteSpace($workDir)) { throw '缺少启动目录' }; $p=Start-Process -FilePath 'python' -ArgumentList $argsList -WorkingDirectory $workDir -WindowStyle Hidden -PassThru; New-Item -ItemType Directory -Path 'data' -Force | Out-Null; $p.Id | Set-Content -Path 'data\\server.pid' -Encoding ascii"
if errorlevel 1 (
	echo [错误] 启动后台服务失败。
	pause
	exit /b 1
)

for /L %%i in (1,1,20) do (
	call :check_running
	if not errorlevel 1 (
		echo 启动成功，正在打开浏览器。
		start "" "http://127.0.0.1:7878"
		exit /b 0
	)
	>nul timeout /t 1
)

echo [警告] 服务可能仍在启动中，将先打开浏览器。
start "" "http://127.0.0.1:7878"
exit /b 0

:check_running
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:7878/api/library' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
exit /b %errorlevel%
