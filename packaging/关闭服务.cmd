@echo off
chcp 65001 >nul
"%~dp0杭电通知集.exe" --stop
if errorlevel 1 pause
