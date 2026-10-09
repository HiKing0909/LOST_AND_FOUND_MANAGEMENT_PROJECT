@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment. Is Python installed?
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install the requirements.
    pause
    exit /b 1
)

rem MySQL settings come from the .env file. Only ask for the password when
rem there is no .env file and no FOUNDIT_DB_PASSWORD environment variable.
if not exist ".env" (
    if not defined FOUNDIT_DB_PASSWORD set /p FOUNDIT_DB_PASSWORD=Enter MySQL password (press Enter if none): 
)

python app.py
echo.
echo FoundIT stopped. Scroll up to read any error message.
pause
