@echo off
chcp 65001 >nul
echo === A股情绪数据更新 ===
pip install -r requirements.txt
python updater.py
echo.
pause
