@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run Install.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m unittest discover -s tests -t . -b
pause
