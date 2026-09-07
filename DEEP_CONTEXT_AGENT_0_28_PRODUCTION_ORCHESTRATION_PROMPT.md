# Промпт реализации Deep Context Agent 0.28

Работай строго по `PRODUCTION_ORCHESTRATION_TECHNICAL_SPEC.md` (R01–R07,
A01–A14). Цель — устранить ложный запрет проверок, чрезмерные prompts, повторные
длинные timeout и преждевременные заявления о готовности.

## Порядок выполнения

1. Зафиксируй baseline: версия, Git status, текущие routing/policy/scheduler,
   provider middleware, diagnostics и тесты. Не меняй пользовательские данные.
2. Добавь regression-запросы из реального межмодульного сценария. Исправь
   маршрутизацию так, чтобы сложность и footprint имели приоритет над наличием
   одного точного пути. Не классифицируй команды внутри логов.
3. Раздели trusted capabilities. Удали `run_project_checks` из project discovery.
   Передавай модели текущие права на targeted read, discovery, checks и write
   отдельно. Сохрани обратную совместимость сохранённых задач.
4. Сохрани VERIFY во владении runtime: после mutation receipt scheduler запускает
   фиксированные checks сам; модель не имеет права устанавливать PASS/COMPLETE.
   FAIL вызывает ограниченный repair и повтор checks.
5. Сделай каждую unit компактной: новый worker thread, одна операция, bounded
   objective/checkpoint/receipts, отсутствие replay полного чата и tool outputs.
   Полная durable-цель остаётся в SQLite и обозначается SHA-256.
6. До записи построй schema-first snapshot локальным детерминированным кодом.
   Передай только names/signatures/fields в лимите; перед edit потребуй fresh read.
7. Реализуй provider circuit breaker. После транспортного сбоя быстро пропускай
   открытый provider, используй только разрешённый fallback и показывай безопасный
   state/retry-after в runtime metadata.
8. При terminal событии агрегируй parent/child diagnostics. Не синтезируй факты:
   используй только durable child records, сохраняя redaction и limits.
9. Обнови глобальные промпт/ТЗ, системный prompt, конфигурационный пример,
   README, changelog, implementation status и package manifest. Повышай версию
   только после полного PASS.
10. Выполни точечные тесты, затем Ruff check/format, mypy, полный pytest,
    compileall, frontend проверки и build. Исправляй причины, а не ожидания теста.
11. Проведи изолированный live `doctor --live` существующим ключом без его печати,
    Web/API smoke на свободном порту и circuit/fallback smoke, если провайдер
    доступен. Не затрагивай сервер пользователя на 8765.
12. Запиши точные результаты и ограничения. `completed` допустим только при
    runtime verification PASS; иначе верни partial/blocked с diagnostic ID.

Запрещено: расширять права текстом модели; считать чтение реализацией; повторять
timeout открытого provider; помещать миллион строк в prompt; называть тесты
пройденными без лога; выполнять произвольный shell из model tool; публиковать Git
без отдельной актуальной команды пользователя.
