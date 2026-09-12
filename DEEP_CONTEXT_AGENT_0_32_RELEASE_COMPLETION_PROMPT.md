# Промпт 0.32: завершить production-приёмку

Статус: приёмка 0.32.0 PASS, обновлено 2026-09-12. Выполненные пункты и пакет:
[матрица доказательств](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
R1 schema 2 и paged root discovery реализованы 2026-09-12. При продолжении
не переписывай их заново: сохраняй контекст/cursor regression и пройденный A23.
Live harness дополнительно проверяет durable parent context и task/job attribution.
R6 CI выявил ожидание checkpoint/delta futures на новых dependencies: проверь
явный durability="sync", малый executor и повтор полного CI/live. Отменённые
диагностические прогоны не считать PASS; не снимать прежние assertions.
Реализуй [дополнение R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
вместе с [исходным ТЗ](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md).
Не называй документацию реализацией, контролируемую задержку реальным LLM,
публикацию ветки production-релизом. Не повышай права через текст модели.

1. Прочитай глобальные/связанные ТЗ, промпты и AGENTS. Зафиксируй HEAD, dirty state,
   версии и существующие доказательства. Сохрани пользовательские изменения.
2. Создай таблицу C01–C18 с привязкой A01–A34, файлами и evidence. Отдели уже
   реализованное от отсутствующего; не переписывай исправные механизмы.
3. R1: добавь failing tests roundtrip/context drift/неверного package origin.
   Реализуй единый typed persisted context, миграции и bounded preflight.
4. R1: подключи context к tool, development VERIFY, verification-only, repair,
   CLI/Web/recovery. Исключи повторный случайный выбор root после restart.
   Добавь paging resolver и structured ambiguity, проверь confinement.
5. R2: сформируй required plan из запроса/конфигурации; реализуй receipts и gate
   одной revision. Проверь optional mypy, missing dependency, cancel и stale.
6. Запусти C01–C06; исправляй конкретные падения, повтори проверки. Запиши evidence
   до перехода к следующему блоку. Недоступное окружение не исправляй правкой source.
7. R3: воспроизведи SQLite busy, потерю одного lease, queued expiry и handoff gaps.
   Реализуй bounded retries, раздельные reasons и gates до side effects.
8. R3: добавь устойчивый курсор/справедливость reconciliation и безопасный legacy
   migration/reconciliation_required. Протестируй больше одной пачки записей,
   рестарт и активного владельца. Не угадывай task/job связи.
9. Запусти C07–C10 и прежние lifecycle-тесты дважды на новых БД. Проверь отсутствие
   повторных мутаций и записи устаревшим owner. При FAIL не расширяй таймаут вслепую.
10. R4: подготовь вложенный Ozon-like fixture с собственным venv. Проведи полный
    scheduler FAIL→явно разрешённый REPAIR→PASS, yield/cancel/restart, C11–C12.
11. R5: реализуй общий outcome/DTO/SSE и понятный UI по Web-промпту 0.32.
    Проверь C13–C14 в браузере, включая две вкладки, reload/offline и секреты.
12. R6: используй существующие разрешённые credentials без печати. Запусти два
    чистых реальных LLM end-to-end, отдельно controlled HTTP timeout/fallback.
    Фиксируй attempts, provider/model, duration, context/check/mutation receipts;
    не включай ключи/личные данные в публикуемые отчёты. Это C15–C16.
13. Исправь найденные ошибки по evidence и повтори затронутые сценарии плюс
    регрессию; не подменяй нестабильный сценарий ослабленным утверждением.
14. C17: выполни весь quality gate, wheel/sdist/clean-install smoke, проверку
    миграции копии старой БД и повторного старта. Не обслуживай live smoke
    пользовательским сервером/данными. Зафиксируй границы поддержки symlink.
15. C18: сопоставь все A01–A34 и C01–C18 с текущими доказательствами. Любой
    незакрытый обязательный gate означает отсутствие production PASS.
16. Обнови глобальные документы, статус, тестовый отчёт и changelog по фактам.
    Только после полного gate обнови release version согласованно во всех
    manifests. При разрешённой публикации проверь secrets, commit/push и remote SHA;
    release/tag не создавать заранее. Итог: файлы, tests, live evidence, риски.

Работай последовательно: tests → реализация → повтор tests → запись evidence.
Если требуется новое разрешение/внешнее условие, верни точный blocker и уже
полученные результаты; не объявляй задачу завершённой ради формального PASS.
