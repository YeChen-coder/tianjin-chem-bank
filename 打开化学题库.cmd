@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 尚未准备好程序运行环境，请联系维护者按 README 完成一次性安装。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" chem_bot.py start --open
if errorlevel 1 pause
