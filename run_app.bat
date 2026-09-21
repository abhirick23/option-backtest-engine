@echo off
rem Double-click to start the Streamlit app; it opens in your browser.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" -m streamlit --version >nul 2>&1
if errorlevel 1 (
    echo Streamlit is not installed. Run:  %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting the backtest app... close this window to stop it.
"%PY%" -m streamlit run app.py
pause
