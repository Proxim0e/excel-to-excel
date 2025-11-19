#!/usr/bin/env bash
# Unix helper: создаёт виртуальное окружение .venv и устанавливает зависимости

set -e

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "Python не найден. Установите Python и повторите."
  exit 1
fi

PY=python3
if ! command -v $PY >/dev/null 2>&1; then
  PY=python
fi

if [ ! -d ".venv" ]; then
  $PY -m venv .venv
fi

# Активируем окружение
# shellcheck source=/dev/null
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Dependencies installed."
echo "Чтобы запустить скрипт: source .venv/bin/activate && python excel_lot_mvp.py"