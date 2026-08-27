@echo off
chcp 65001 >nul
cd /d "%~dp0"
title RedNote2TG Bot

set PYTHON_CMD=python
if exist "%~dp0.venv\Scripts\python.exe" set PYTHON_CMD="%~dp0.venv\Scripts\python.exe"
if exist "%~dp0venv\Scripts\python.exe" set PYTHON_CMD="%~dp0venv\Scripts\python.exe"

echo ========================================================
echo               RedNote2TG 采集与推送服务
echo ========================================================
echo.

%PYTHON_CMD% -m rednote2tg

echo.
echo ========================================================
echo 服务已退出 (ExitCode: %errorlevel%)
echo ========================================================
pause
