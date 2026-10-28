@echo off
cd /d "%~dp0"
python playtester.py
if errorlevel 1 pause
