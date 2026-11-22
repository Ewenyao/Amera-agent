@echo off
rem Run ollama-agent from source without pip install.
rem Picks a usable Python: repo .venv -> py launcher -> python -> python3.
setlocal
set "AGENT_HOME=%~dp0"
set "PYTHONPATH=%AGENT_HOME%src;%PYTHONPATH%"

set "PY="
if exist "%AGENT_HOME%.venv\Scripts\python.exe" set "PY=%AGENT_HOME%.venv\Scripts\python.exe"

if not defined PY (
    py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
    python3 -c "import sys" >nul 2>nul && set "PY=python3"
)

if not defined PY (
    echo [agent] no usable Python found. Need Python 3.9 or newer.
    echo [agent] install it, then retry. Or run manually: python -m ollama_agent
    exit /b 127
)

%PY% -m ollama_agent %*
