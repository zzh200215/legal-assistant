@echo off
rem #45 pilot daily check (triggered by AIBG-Pilot-DailyCheck)
cd /d "%~dp0.."
set DATABASE_URL=mysql+pymysql://root:123456@localhost:3306/aibg
python -B scripts\pilot_daily_check.py --url http://127.0.0.1:8001 --alert >> data\pilot-daily-check-run.log 2>&1
