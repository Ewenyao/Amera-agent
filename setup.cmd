@echo off
rem One-time setup: create a local venv and install ollama-agent into it.
rem After this, agent.cmd will use .venv\Scripts\python.exe automatically.
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [setup] no usable Python found. Install Python 3.9+ first.
    exit /b 127
)

if exist ".venv\Scripts\python.exe" (
    echo [setup] .venv already exists, skipping creation
) else (
    echo [setup] creating .venv ...
    %PY% -m venv .venv || exit /b 1
)

echo [setup] installing ollama-agent into .venv ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -e . || exit /b 1

echo [setup] done. Try:  agent --version
