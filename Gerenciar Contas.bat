@echo off
cd /d "%~dp0"
py -3 gerenciar_contas.py
if errorlevel 1 python gerenciar_contas.py
pause
