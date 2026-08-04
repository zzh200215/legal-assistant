@echo off
rem #41 pilot daily backup (triggered by AIBG-Pilot-DailyBackup)
cd /d "%~dp0.."
set DATABASE_URL=mysql+pymysql://root:123456@localhost:3306/aibg
python -B scripts\create_pilot_backup.py --confirm --output-dir data\backups >> data\backups\daily_backup.log 2>&1
