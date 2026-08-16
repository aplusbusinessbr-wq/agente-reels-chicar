@echo off
cd /d "%~dp0"
python reels_para_shorts.py >> log_agendador.txt 2>&1
