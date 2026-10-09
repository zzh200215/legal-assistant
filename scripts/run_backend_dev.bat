@echo off
cd /d "%~dp0.."
start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_project_hidden.ps1"
