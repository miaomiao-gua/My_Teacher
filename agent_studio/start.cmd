@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI 教学 Agent 工作台（端口 5001）

echo ============================================================
echo   AI 教学 Agent 工作台
echo   启动后浏览器打开： http://127.0.0.1:5001/
echo   关闭这个黑窗口 = 停止服务
echo ============================================================
echo.

py -3.14 -c "import fastapi, uvicorn" >nul 2>&1
if not errorlevel 1 (
    echo [启动] 使用 py -3.14
    py -3.14 run.py --port 5001
    goto :end
)

set PYEXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
if exist "%PYEXE%" (
    echo [启动] 使用 %PYEXE%
    "%PYEXE%" run.py --port 5001
    goto :end
)

echo [错误] 找不到装好依赖的 Python 3.14。
echo        请先执行： py -3.14 -m pip install fastapi uvicorn httpx pydantic python-dotenv

:end
echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
