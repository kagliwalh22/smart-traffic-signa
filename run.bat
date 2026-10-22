@echo off
echo ========================================================
echo   Starting Smart Traffic Signal & Vision System
echo ========================================================

echo [1/2] Starting Vision AI Backend (Flask + YOLO on :5000)...
start "Traffic Vision Backend" cmd /k "python vision/main.py"

echo [2/2] Starting React Frontend Dashboard (Vite on :3000)...
start "Traffic Frontend Dashboard" cmd /k "cd frontend && npm.cmd run dev -- --host 0.0.0.0 --port 3000"

echo.
echo Both services have been launched!
echo - Frontend Dashboard: http://localhost:3000
echo - Backend API:        http://localhost:5000
echo ========================================================
