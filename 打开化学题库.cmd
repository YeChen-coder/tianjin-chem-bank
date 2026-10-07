@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "CHEM_PYTHON=.venv\Scripts\python.exe"
if not exist "%CHEM_PYTHON%" set "CHEM_PYTHON=python\python.exe"
if not exist "%CHEM_PYTHON%" (
  echo 尚未准备好程序运行环境，请联系维护者按 README 完成一次性安装。
  pause
  exit /b 1
)
"%CHEM_PYTHON%" chem_bot.py start --open
if errorlevel 1 pause
