@echo off
cd /d "%~dp0..\frontend"
"D:\AI\neo4j\npm.cmd" run dev -- --host=127.0.0.1 --port=5173 >> ..\logs\frontend.out.log 2>> ..\logs\frontend.err.log
