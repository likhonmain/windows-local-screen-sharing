@echo off
setlocal
cd /d "%~dp0"
title Allow Local Screen Mirror on Hotspot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Enable-Hotspot-Access.ps1"
pause
