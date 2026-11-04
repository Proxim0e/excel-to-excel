# Ассистент оценки тендеров на мед. изделия

Общаться и думать **на русском**. Работа идёт на двух компьютерах (дом и работа) с одной копией репозитория
через GitHub — память Claude не переносится между ними, поэтому всё нужное для продолжения работы живёт здесь,
в [docs/LOGBOOK.md](docs/LOGBOOK.md) и [docs/ROADMAP.md](docs/ROADMAP.md).

## Начало сессии

1. `git pull` текущей ветки (сейчас рабочая — `feature/stage5-history`).
2. `python -m src.envcheck` — среда совпадает с `requirements.txt`? Если нет: `powershell -ExecutionPolicy Bypass -File setup_env.ps1`.
   Разная среда на двух ПК уже ломала запуск (02.11.2026: PyMuPDF не той версии — Anexa 22 не нашлась).
3. Ответы человека с другого ПК (карточки, решения по офертам, «медицинское ли изделие») приезжают с `git pull`
   в `knowledge/answers/<ПК>.jsonl` и подмешиваются в `data/knowledge.sqlite` сами при открытии окна (вручную —
   `python -m src.history sync`).
4. Прочитать верхние записи [docs/LOGBOOK.md](docs/LOGBOOK.md): что сделано и что открыто.

## Конец сессии

Дописать запись в `docs/LOGBOOK.md` (сверху: дата · ветка · компьютер — Сделано / Обнаружено / Решено /
Открыто / Цифры), закоммитить вместе с кодом и файлом ответов `knowledge/answers/` и запушить.

## Документы

- [docs/CONCEPT.md](docs/CONCEPT.md) — цель и принципы (оригинал — документ Claude Docs «Концепция: AI-ассистент
  оценки тендеров на мед. изделия», вкладки «Концепция» и «Кодбук — черновик»).
- [docs/ROADMAP.md](docs/ROADMAP.md) — этапы 0–5 и их состояние; [docs/CODEBOOK.md](docs/CODEBOOK.md) — цвета,
  вердикты и правила решения; [docs/adr/](docs/adr/) — архитектурные решения.

## Запуск по тендеру

| Шаг | Команда | Результат |
|---|---|---|
| Окно приложения (всё ниже — кнопками) | `python src/main.py` без параметров (или `python -m src.ui`) | при запуске — «Как будем работать?» (новый тендер / родительский файл / скачанный); этапы, прогресс, лог, вопросы; «Настройки» — `data/settings.json` поверх `src/config.py` |
| Скачивание, классификация, Anexa 22, заполнение | `python src/main.py --tender <номер>` (без окна; `--console` — по родительскому файлу) | `resources/result_<дата>.xlsx`, `downloads/<тендер>/manifest.sqlite` |
| Уточнения | `python -m src.clarificari <result.xlsx> downloads/<тендер> --dir downloads/<тендер>/Clarificari --out <копия.xlsx>` | колонки «clarificări (runda N)» |
| AMDM | `python -m src.amdm check <result.xlsx>` (`--offline` — только локальная копия реестра) | `*_AMDM.xlsx`, лист «Verificare AMDM» |
| Оценка | `python -m src.evaluate <result.xlsx> downloads/<тендер> [--dry-run] [--online]` | решения и вопросы в `data/knowledge.sqlite` |
| Тесты | `pytest` | |

`downloads/`, `resources/*.xlsx`, `resources/amdm_registry.sqlite`, `data/` — не в git (ответы человека из
`data/knowledge.sqlite` переносятся через `knowledge/answers/`, см. выше): на другом ПК их нет,
тендер нужно скачать заново (или скопировать папку вручную).

## Правила, о которых договорились

- DM аппарата у его запчасти / аксессуара / комплекта не подходит — нужен ISO на изделие (02.11.2026).
- Модули не вызывают `logging.basicConfig(force=True)` при импорте — только в `if __name__ == "__main__"`: иначе
  снимается обработчик окна и лог в окне пропадает (04.11.2026).
- Писать в Excel только в пустые ячейки; ручные правки человека не перезаписывать.
- Ветки этапов идут цепочкой `stage2-anexa22` → `stage4-amdm` → `stage5-history`; исправление старого этапа
  делается в его ветке и вливается в `stage5-history`.
