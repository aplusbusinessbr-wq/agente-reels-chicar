@echo off
title Dashboard Chicar
cd /d "%~dp0"
python -m streamlit run dashboard.py --server.headless true >> log_dashboard.txt 2>&1
