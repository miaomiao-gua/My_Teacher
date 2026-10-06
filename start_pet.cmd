@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MyTeacher 桌宠

REM ===========================================================================
REM  双击我就行：打开桌宠页面（Live2D 形象 + 气泡 + 能直接跟她说句话）
REM  - 服务没在跑的话，我会顺手把 start_agent.cmd 拉起来，等它就绪再开页面
REM  - 自检（不产生任何副作用）：
REM        cmd /c "set DRY=1&& start_pet.cmd"            只打印会开哪个网址
REM        cmd /c "set DRY=1&& set PORT=5002&& start_pet.cmd"   看「服务没起」那条路
REM  为什么要写成这样：cmd 对**带中文的括号块**和 LF 换行都很容易解析错
REM  （实测报过 "'1`' is not recognized" 这类怪错），所以这里只用标签跳转 + CRLF。
REM ===========================================================================

if not defined PORT set PORT=5001
set URL=http://127.0.0.1:%PORT%/agent/pet
set COUNT=0

netstat -ano | findstr /r /c:"LISTENING" | findstr ":%PORT%" >nul 2>&1
if not errorlevel 1 goto ready

echo [提示] Agent 服务（端口 %PORT%）没在跑。
if defined DRY echo [自检] 正式运行到这里时，会先拉起 start_agent.cmd，再等它就绪。
if defined DRY goto bye

echo [启动] 正在把它拉起来…
start "My_Teacher Agent 服务" cmd /c start_agent.cmd

:wait
timeout /t 1 >nul
set /a COUNT=%COUNT%+1
netstat -ano | findstr /r /c:"LISTENING" | findstr ":%PORT%" >nul 2>&1
if not errorlevel 1 goto ready
if %COUNT% lss 40 goto wait

echo.
echo [警告] 等了 40 秒服务还是没起来。请手动双击 start_agent.cmd，看那个黑窗口报什么错。
echo        下面仍会试着打开桌宠页面，但气泡和聊天要等服务起来才有。
pause

:ready
if defined DRY echo [自检] 服务在跑，本来会打开：%URL%
if defined DRY goto bye
start "" "%URL%"

:bye
exit /b 0
