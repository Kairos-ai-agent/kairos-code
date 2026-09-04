@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set KAIROS_PORT=9527
wscript.exe start.vbs
