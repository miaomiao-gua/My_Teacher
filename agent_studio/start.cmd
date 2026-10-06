@echo off
cd /d "%~dp0"
title AI 教学 Agent 工作台（端口 5001）

REM ===========================================================================
REM  双击我启动工作台。界面： http://127.0.0.1:5001/
REM  关掉这个黑窗口 = 停止服务。
REM
REM  注意：cmd 对「带中文的括号块」和 LF 换行极容易解析错（实测报过
REM  `'Teacher' 不是内部或外部命令`），所以这里只用标签跳转，文件存成 CRLF。
REM ===========================================================================

echo ============================================================
echo   AI 教学 Agent 工作台
echo   启动后浏览器打开： http://127.0.0.1:5001/
echo   关掉这个黑窗口 = 停止服务
echo ============================================================
echo.

set PYEXE=
py -3.14 -c "import fastapi, uvicorn" >nul 2>&1
if not errorlevel 1 set PYEXE=py -3.14
if defined PYEXE goto run

set PYEXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
if exist "%PYEXE%" goto run2

echo [错误] 找不到装好依赖的 Python 3.14。
echo        请执行： py -3.14 -m pip install fastapi uvicorn httpx pydantic python-docx pypdf Pillow
goto end

:run
echo [启动] 使用 py -3.14
%PYEXE% run.py --port 5001
goto end

:run2
echo [启动] 使用 %PYEXE%
"%PYEXE%" run.py --port 5001
goto end

:end
echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
