@echo off
chcp 65001 >nul
title 西瓜短剧Agent 启动器
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败，请查看上方提示或 logs 目录日志。
  pause
)
