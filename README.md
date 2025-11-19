```text
Excel Lot MVP
--------------

В репозитории:
- excel_lot_mvp.py — основной скрипт (переместите его из .venv/Scripts в корень или в папку scripts/)
- requirements.txt — зависимости
- install_deps.bat — Windows helper (создаёт .venv и устанавливает зависимости)
- install_deps.sh — Unix helper
- .gitignore — исключает .venv, .idea, артефакты

Как запустить (Windows):
1. Склонируйте репозиторий.
2. Откройте PowerShell в папке проекта.
3. Запустите install_deps.bat.
4. Активируйте окружение: call .venv\Scripts\activate.bat
5. Запустите: python excel_lot_mvp.py

Linux/macOS:
1. ./install_deps.sh
2. source .venv/bin/activate
3. python excel_lot_mvp.py

Примечание:
- Не коммитьте .venv и выходные файлы (например, ++lots_result.xlsx). Они включены в .gitignore.