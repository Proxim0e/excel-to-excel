@echo off
REM Windows helper: создаёт виртуальное окружение .venv и устанавливает зависимости

REM Проверим наличие Python
where python >nul 2>&1
if ERRORLEVEL 1 (
  echo Python не найден в PATH. Установите Python и повторите попытку.
  exit /b 1
)

REM Создаём venv, если его нет
IF NOT EXIST ".venv" (
  python -m venv .venv
)

REM Активируем venv
call .venv\Scripts\activate.bat

REM Обновим pip и установим зависимости
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Dependencies installed.
echo Чтобы запустить скрипт:
echo    call .venv\Scripts\activate.bat
echo    python excel_lot_mvp.py
pause