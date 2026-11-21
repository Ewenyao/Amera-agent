@echo off
rem Run ollama-agent from source without pip install.
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m ollama_agent %*
