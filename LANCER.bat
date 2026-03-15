@echo off
title CryptoScan v6 -- Deep Intelligence
color 0A
cd /d "%~dp0"
echo.
echo  ============================================================
echo   CRYPTOSCAN v6  --  Deep Intelligence
echo  ============================================================
echo.
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERREUR] Python non trouve.
    echo  Telechargez : https://www.python.org/downloads/
    echo  Cochez "Add Python to PATH"
    pause & exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo  [OK] %%i
if not exist "server.py" ( echo  [ERREUR] server.py introuvable & pause & exit /b 1 )
dir /b *.html >nul 2>&1
if errorlevel 1 ( echo  [ERREUR] Aucun fichier .html trouve & pause & exit /b 1 )
echo  [OK] Fichiers trouves
echo.
echo  Lancement... Le navigateur s'ouvrira automatiquement.
echo  NE FERMEZ PAS cette fenetre.
echo  Ctrl+C pour arreter.
echo.
python server.py
echo.
echo  Serveur arrete.
pause
