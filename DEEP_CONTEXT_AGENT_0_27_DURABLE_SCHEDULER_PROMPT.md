# Промт реализации Deep Context Agent 0.27: durable scheduler

Работай автономно, последовательно и доказательно. Цель — устранить блокировку
долгой persistent-задачи общим 900-секундным deadline и циклы discovery без
реализации. Выполняй требования `DURABLE_EXECUTION_SCHEDULER_TECHNICAL_SPEC.md`
S01–S12 и acceptance A01–A20. Не выдавай этот промт за выполненный код.

Production-архитектура: фазовый автомат + persistent scheduler + evidence-driven
progress. Раздельные таймауты являются базовой конфигурацией, а не единственным
исправлением. Пользователь не должен рассчитывать размер этапов.

## Обязательные инварианты

1. Не увеличивай один общий timeout как основное решение.
2. Не создавай отдельную обходную логику для Web: CLI/Web используют один runtime.
3. Не запускай полный audit для project-change/project-test.
4. Не считай heartbeat, HTTP/model success, duplicate read/search или prose новым
   прогрессом.
5. Не заставляй read-only режим записывать и не создавай фиктивные мутации.
6. Handoff не сбрасывает токены, стоимость, active time, tools, retries и права.
7. Новый unit deadline допустим только после валидного handoff; wall TTL конечен.
8. Provider switch не повторяет подтверждённые side effects.
9. Старые SQLite мигрируются additive; пользовательские проекты и данные не удалять.
10. PASS возможен только по ToolMessage/process/API/browser evidence текущего прогона.

## Шаг 1. Baseline и воспроизведение

- Зафиксируй branch/commit/status и сохрани чужие изменения.
- Запусти текущие Ruff/pytest/mypy/Web checks до правок.
- На копии/чистой SQLite воспроизведи incident shape: несколько discovery units,
  soft yield, общий 900-second task deadline, 0 changed files/checks.
- Экспортируй redacted parent/child evidence. Не включай ключи или полный prompt.
- Составь трассировку: submit → job → units → model attempts → tools → terminal.

## Шаг 2. Модель времени

- Введи и валидируй model-turn, unit, active-task и wall-clock budgets S01.
- Legacy `AGENT_TASK_TIMEOUT_SECONDS` оставь для одноходовых операций.
- Используй monotonic elapsed внутри процесса и persisted accumulated active time.
- Пауза/очередь/downtime не расходуют active time; wall TTL расходуется.
- Добавь doctor/settings/README/env и тесты граничных значений.

## Шаг 3. Persistent scheduler

- Добавь additive SQLite schema для queue/claims/phase transitions/time ledger.
- Реализуй atomic enqueue, claim, heartbeat, yield/requeue, pause, terminal и
  expired-lease reconciliation.
- Отдели выполнение job от Web request/SSE lifecycle.
- Гарантируй одну mutation-capable unit на job через generation/revision fencing.
- Не держи SQLite lock во время сети, LLM или tool execution.
- Сохрани совместимость с jobs/checkpoints/receipts 0.19–0.26.

## Шаг 4. Фазовый автомат

- Реализуй явные DISCOVER, PLAN, IMPLEMENT, VERIFY, REPAIR, COMPLETE и terminal
  states из S03.
- Разрешай только валидные переходы; каждый фиксируй с reason/evidence.
- Project-change начинает с bounded discovery или сразу с known next operation.
- Возврат к discovery из implement/verify допускай только как одну targeted unit.
- Completion требует выполненных проверок, когда они предусмотрены задачей.

## Шаг 5. Discovery ceilings

- Реализуй отдельные counters/limits S04.
- Deduplicate list/search/range evidence между units и restart.
- После ceiling запрети ещё одну полную discovery unit.
- Потребуй PLAN/IMPLEMENT, допустимую reasoning escalation или blocker.
- Для exact path выбирай read_file; denied/error не обходи вариациями запроса.

## Шаг 6. Evidence-driven renewal

- Нормализуй information/implementation/verification/decision evidence.
- Считай delta по durable receipts, content version, range union, check identity и
  executable operation fingerprint.
- Renew unit только при валидном delta. Heartbeat обновляет liveness, но не budget.
- Плановый soft yield отображай отдельно от failed.
- После отсутствия delta выполни bounded replan/escalation и точный terminal.

## Шаг 7. Structured next operation

- Добавь строгую модель S06 с operation allowlist, workspace target, required
  evidence, expected effect, verification и blockers.
- Валидируй до commit handoff; не сохраняй raw shell как доверенную команду.
- Следующая unit получает минимальный evidence summary и начинает с операции.
- Если операция устарела после external change, reconciliation формирует точечное
  чтение, а не полный inventory.

## Шаг 8. IMPLEMENT и blocker

- В allow-write mode unit должна получить mutation receipt или один из blocker S07.
- Blocker содержит missing item, проверенные evidence и безопасное действие.
- Запрети пустые/фиктивные файлы ради progress counter.
- Ask/Plan/read-only возвращают анализ/план без мутаций и без ложного failure.

## Шаг 9. Model routing

- Свяжи discovery stall с существующим adaptive router.
- Reasoning escalation применяй только среди допустимых профилей.
- Сохраняй Manual/local-only/context/tools/cost/latency/rights.
- Передавай escalation-модели covered/denied operations, чтобы исключить replay.
- При отсутствии перехода заверши `discovery_exhausted_without_implementation`.

## Шаг 10. Диагностика

- Добавь phase/time/discovery/change/check/renewal/escalation fields S09.
- Parent journal связывает units/model attempts/tools и terminal.
- Исправь представление `units=0/11`: показывай completed/yielded/failed отдельно.
- Сохрани redaction, retention, crash recovery и диагностический lookup.
- Legacy terminal code расшифруй, но не переписывай исторические доказательства.

## Шаг 11. Web/API

Выполни отдельный Web-промт 0.27. Web submit должен возвращаться быстро, scheduler
работает независимо от SSE. Реализуй reconnect/replay, revision-safe controls,
фазы, бюджеты и понятный blocker без отдельной вкладки Autopilot.

## Шаг 12. Автоматические тесты

Покрой A01–A19 детерминированными clock/provider/tool fixtures:

- разделение четырёх timeout;
- active time pause/downtime и wall TTL;
- handoff с/без evidence;
- discovery ceiling и targeted discovery;
- structured next operation validation;
- allow-write mutation либо blocker, read-only без записи;
- verification/repair/complete;
- crash exact-once, два worker, stale lease;
- provider timeout/rate-limit/fallback/escalation;
- migration реальной копии 0.26;
- Web 202/SSE replay/reload/pause/resume/cancel;
- development → log analysis → resume development;
- миллион строк без помещения корпуса в prompt.

Не используй real sleeps в unit-тестах. Проверяй schema и invariants, а не только
строки UI.

## Шаг 13. Live-приёмка

- Используй отдельные временные workspace/data dir/thread для каждого прогона.
- Существующие ключи загружай штатно, никогда не печатай.
- Повтори исходный service-layer сценарий минимум дважды.
- Обязательно: submit → disconnect → reconnect → process restart → resume →
  подтверждённая мутация → реальный check → terminal.
- Отдельно проверь controlled blocker без безопасной мутации.
- Сверь exact-once receipts и отсутствие project-audit manifest.
- При найденной ошибке исправь код и повтори весь релевантный контур.

## Шаг 14. Финализация

- Запусти Ruff check/format, полный pytest, mypy, compileall, pip check, Web build
  и tests, wheel/sdist, diff/secret/artifact scan.
- Обнови глобальные ТЗ/prompts, Web-ТЗ, system prompt, README, env, changelog и
  implementation status только по фактическим результатам.
- Повышай версию и публикуй Git только после полного PASS и прямого пользовательского
  разрешения. Не force-push. Не включай `.env.local`, SQLite, logs, caches, модели,
  workspace и live reports.

## Ожидаемый итоговый отчёт

Укажи root cause, изменённые файлы, миграцию, матрицу S01–S12/A01–A20, точные
команды и результаты проверок, два live evidence ID, provider/model, ограничения,
commit/tag/remote status. Если хотя бы один обязательный пункт не доказан, используй
статус PARTIAL/BLOCKED, а не production PASS.
