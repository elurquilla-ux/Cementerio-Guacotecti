@echo off
chcp 65001 >nul
title Instalando Cementerio General
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
if errorlevel 1 pause
