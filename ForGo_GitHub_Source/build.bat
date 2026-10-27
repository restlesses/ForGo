@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ForGo 3.4.0 Game Hub - Build Windows EXE

echo.
echo  ================================================
echo       ForGo 3.4.0 Game Hub - сборка приложения для Windows
echo  ================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [ОШИБКА] Python Launcher не найден. Установи Python 3.11 или 3.12 с сайта python.org
  echo и обязательно отметь Add Python to PATH.
  pause
  exit /b 1
)

set "PYTHON=py -3.12"
py -3.12 --version >nul 2>nul
if errorlevel 1 set "PYTHON=py -3.11"
%PYTHON% --version >nul 2>nul
if errorlevel 1 (
  echo [ОШИБКА] Для сборки нужен Python 3.11 или 3.12 x64.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo [1/4] Создаём виртуальное окружение...
  %PYTHON% -m venv .venv
  if errorlevel 1 goto :error
)

echo [2/4] Обновляем pip...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/4] Устанавливаем зависимости...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/4] Собираем ForGo.exe...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name ForGo --icon assets\ForGo.ico --version-file version_info.txt --add-data "assets;assets" ForGo.py
if errorlevel 1 goto :error

if not exist "dist\ForGo.exe" (
  echo [ОШИБКА] PyInstaller завершился, но dist\ForGo.exe не найден.
  goto :error
)

echo.
echo  ГОТОВО! Приложение находится здесь:
echo  %CD%\dist\ForGo.exe
echo.
echo  Скопируй ForGo.exe в удобную папку и запускай.
echo  При первой сборке интернет нужен для скачивания библиотек.
echo.
pause
exit /b 0

:error
echo.
echo [ОШИБКА] Сборка не завершилась. Прочитай сообщение выше.
echo Если ошибка связана с сетью, проверь соединение и запусти build.bat ещё раз.
pause
exit /b 1
