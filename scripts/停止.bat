@echo off
chcp 65001 >nul
title 西瓜短剧Agent 停止器
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
