# CLI (план)

Команды (в будущем):
- excel2excel process --config configs/default.yaml
  - Основной проход: создать/обновить листы, записать поля, сохранить книгу.
- excel2excel scrape --url <tender_url>
  - Проверка скрапа без записи в Excel.
- excel2excel validate-config --config configs/default.yaml
  - Проверка ключей и значений конфигурации.

Параметры:
- --resource-dir, --template-file, --append-to-existing, --percent-threshold, --mapping-file