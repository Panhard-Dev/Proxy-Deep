@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0iniciar_windows.ps1" %*
if errorlevel 1 (
  echo.
  echo Nao foi possivel iniciar. Leia a mensagem acima.
  pause
)
