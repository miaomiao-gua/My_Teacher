@echo off
chcp 65001 >nul
cd /d "%~dp0"
title My_Teacher Agent 服务 (端口 5001)

echo ============================================================
echo   My_Teacher Agent 服务
echo   启动后浏览器打开： http://127.0.0.1:5001/agent/app
echo   关闭这个黑窗口 = 停止服务
echo ============================================================
echo.

REM 优先用 py 启动器找 Python 3.14（项目依赖装在这个版本里）
py -3.14 -c "import fastapi, uvicorn" >nul 2>&1
if not errorlevel 1 (
    echo [启动] 使用 py -3.14
    py -3.14 run_agent.py --port 5001
    goto :end
)

REM 兜底：直接用已知路径
set PYEXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
if exist "%PYEXE%" (
    echo [启动] 使用 %PYEXE%
    "%PYEXE%" run_agent.py --port 5001
    goto :end
)

echo [错误] 找不到装好依赖的 Python 3.14。
echo        请在项目目录执行： py -3.14 -m pip install -r server\requirements.txt

:end
echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
