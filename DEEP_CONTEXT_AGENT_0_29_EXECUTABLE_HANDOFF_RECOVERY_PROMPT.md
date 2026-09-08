# Промпт реализации Deep Context Agent 0.29

Работай строго по
`EXECUTABLE_HANDOFF_RECOVERY_TECHNICAL_SPEC.md` (E01–E12, A01–A18) и сохраняй
все действующие ограничения предыдущих ТЗ. Цель этапа — устранить incident
`103bdbab6c558da6761380dc`: persistent `project-change` получил разрешение записи,
но три work unit завершились без мутации, потому что runtime передал
`edit_file` с пустым `target`, пустыми `blocking_conditions` и пустыми checks,
после чего ошибочно сообщил, что точная недостающая предпосылка сохранена.

## Обязательный порядок работы

1. Зафиксируй baseline: Git status, версию, схему `autopilot.sqlite3`, код
   формирования/валидации `next_operation`, phase transitions, Web DTO/SSE,
   diagnostics и существующие тесты. Не изменяй пользовательские workspace,
   активный сервер на порту 8765 и секреты.
2. Добавь воспроизводящий regression fixture с данными incident:
   `project-change`, `allow-write`, широкая цель «реализовать сервисный слой,
   CLI-команды, FastAPI + UI и тесты», переход в IMPLEMENT, операция
   `edit_file`, пустой `target`, отсутствие mutation receipt и checks.
3. Введи schema-validated `ExecutableOperationContract`. Для mutating operation
   обязательны непустой нормализованный target внутри workspace, вид операции,
   ожидаемый эффект, preconditions, bounded patch/input, evidence references и
   allowlisted verification plan. Невалидный contract нельзя выдавать worker.
4. Добавь goal compiler/decomposer: широкая подтверждённая цель преобразуется в
   durable dependency graph небольших конкретных операций. Выбирай первый
   готовый leaf по реальным schema snapshot, manifest и receipts; не помещай в
   work unit весь проект, чат или миллион строк.
5. Раздели `missing user information`, `missing project evidence`, `permission
   denied`, `invalid planner output`, `unsupported operation` и `verification
   unavailable`. Пустой target является внутренней ошибкой планирования, а не
   нехваткой информации у пользователя.
6. При невалидном handoff не увеличивай implementation attempt и не запускай
   model worker. Выполни bounded deterministic repair, затем не более одного
   targeted-discovery/reasoning replan в рамках прежних прав и бюджетов. Если
   executable target всё ещё отсутствует, сохрани точный structured blocker.
7. Сделай blocker доказуемым: `error_code`, `category`, `summary`,
   `missing_prerequisites[]`, `attempted_operation`, `candidate_targets[]`,
   `evidence_ids[]`, `required_action`, `retryable` и redacted source. Нельзя
   писать «точная причина сохранена», если обязательные поля пусты.
8. Свяжи IMPLEMENT с VERIFY. Для каждого leaf заранее известен минимальный
   verification plan; после mutation receipt его запускает runtime. Ошибка
   ProjectCheckRunner сохраняет безопасный stderr/category, а не исчезает как
   безымянный `error`. COMPLETE без обязательного PASS запрещён.
9. Обнови SQLite миграцию и crash recovery. CAS revision, lease generation,
   idempotency и exact-once side effects обязательны. Resume продолжает тот же
   leaf или replan, но не повторяет подтверждённую запись и не запускает полный
   аудит.
10. Обнови Web API/UI: карточка показывает конкретный текущий файл/компонент,
    причину перепланирования, mutation/check counters и пригодный для действия
    blocker. Физические пути, prompt bodies, raw exceptions и ключи не выводи.
11. Обнови глобальные ТЗ/промпт, system prompt, README, changelog, implementation
    status и конфигурационные примеры при появлении новых настроек. Исторические
    документы не выдавай за реализованное состояние.
12. Выполни unit/integration/API/browser regressions, затем Ruff check/format,
    mypy, полный pytest, compileall, frontend checks и package build. Исправляй
    первопричину, а не ослабляй критерии.
13. Проведи изолированный live `doctor --live` существующим ключом без его
    печати. На свободном порту и чистых `AGENT_DATA_DIR`/workspace повтори
    исходную широкую задачу минимум два раза: один обычный прогон и один прогон
    с принудительно пустым planner target. Первый должен дойти до реальной
    mutation + runtime verification; второй — восстановиться через replan либо
    вернуть полный structured blocker. Порт 8765 не трогай.
14. Повышай версию и объявляй production readiness только после A01–A18 и
    записи точных команд, counts, job/task IDs и ограничений. Публикация Git
    выполняется только по отдельной актуальной команде пользователя.

## Запрещённые упрощения

- увеличивать timeout, число units или размер prompt вместо исправления handoff;
- заставлять модель угадывать пустой target;
- считать чтение, поиск, heartbeat или model prose реализацией;
- создавать фиктивную правку ради mutation receipt;
- перекладывать декомпозицию или размер этапов на пользователя;
- обозначать внутренний planner defect как `missing_information`;
- терять точную ошибку ProjectCheckRunner;
- повторять полный discovery после resume;
- сообщать, что prerequisite сохранён, без заполненной persisted структуры;
- завершать allow-write job без фактической мутации и runtime-owned PASS, кроме
  честного terminal `partial/blocked` с проверяемой причиной.

Финальный результат этапа: широкая инженерная задача автоматически превращается
в проверяемые операции над конкретными файлами; каждый handoff исполним до запуска
worker; невозможность реализации объясняется точным persisted blocker, а не общей
фразой.
