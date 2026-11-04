"""
  python -m src.ui [<result.xlsx> <папка тендера>]

Окно приложения: запуск тендера, оценка, уточнения; лог и этап; вопросы человеку.
"""
import sys

from src.ui.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
