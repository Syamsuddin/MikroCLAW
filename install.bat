@echo off
REM ============================================================
REM  MikroCLAW - launcher installer untuk Windows (double-click)
REM  Menjalankan install.ps1 dengan ExecutionPolicy Bypass.
REM  Argumen apa pun diteruskan ke install.ps1.
REM ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
