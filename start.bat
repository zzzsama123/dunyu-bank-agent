@echo off
cd /d "%~dp0"
if exist "%~dp0portable_build\runtime\python\python.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable_build\scripts\launch.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1"
)
pause