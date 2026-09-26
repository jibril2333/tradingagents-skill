@echo off
rem Run ta.py with the skill runtime (setup_runtime.py creates it).
set "PY=%TRADINGAGENTS_SKILL_PYTHON%"
if "%PY%"=="" set "PY=%USERPROFILE%\.tradingagents-skill\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo {"status": "setup_required", "error": "Runtime not found. Run scripts\\setup_runtime.py with Python 3.10+ or set TRADINGAGENTS_SKILL_PYTHON."}
  exit /b 2
)
"%PY%" "%~dp0ta.py" %*
