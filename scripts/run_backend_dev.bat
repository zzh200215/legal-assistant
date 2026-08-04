@echo off
cd /d "%~dp0.."
"D:\AI\ACD\envs\llmXM\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8001 >> logs\backend.out.log 2>> logs\backend.err.log
