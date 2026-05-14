@echo off
title Dashboard Chicar
cd /d "D:\Downloads\AgenteReels\agente_reels_shorts\reels_to_shorts"
python -m streamlit run dashboard.py --server.headless true >> log_dashboard.txt 2>&1
