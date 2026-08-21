@echo off
chcp 65001 >nul
echo === 启动 A股情绪监控网页(本地) ===
echo 访问 http://localhost:8000
echo 按 Ctrl+C 停止
echo.
cd /d "%~dp0\site"
python -m http.server 8000
