# Web-промпт 0.32: согласованные задачи и контекст проверок

## Актуальное дополнение 0.33

Применяй [блочный промпт](EXPLICIT_RESUME_IMPLEMENTATION_PROMPT.md) и
[ТЗ R1–R6/A01–A12](EXPLICIT_RESUME_TECHNICAL_SPEC.md). Сохранённые задачи
продолжаются typed действиями с task/revision/idempotency, не классификацией
текста. При quarantine — read-only сверка и явная новая связанная проверка.
Не снимай quarantine, не повышай права и не повторяй неизвестные записи из UI.
Действия находятся в сворачиваемой панели чата; заголовок и composer закреплены.
Сохраняй черновик и показывай recovery при неподтверждённой отправке worker.
Итоги тестов: [приёмка 0.33](EXPLICIT_RESUME_ACCEPTANCE.md).

Дата: 2026-09-09; обновлено 2026-09-12. Браузерная приёмка execution cards,
reload/restart/offline и двух вкладок выполнена: [evidence](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Общий release gate остаётся отдельным и не заявляется автоматически.

## Завершение production-приёмки

Применяй [дополнение R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
и [порядок реализации](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).
Для Web обязательны R5/C13–C14 и backend-контракты R1–R3:

- Единый outcome/projection DTO; отдельно task/job/request/diagnostic IDs.
- Virtual root, environment label, required checks и актуальность evidence;
  partial/stale/unavailable не отображаются как итоговый PASS.
- Получать из schema 2 только compact receipt/context ID, не prefix/launch host path,
  список dependencies или private origins. root-discovery partial содержит cursor и
  scanned count; продолжение использует серверный checkpoint, не повторный полный audit.
- Повторное открытие страницы, SSE replay и две вкладки не создают новые
  approvals, executions или мутации; кнопки используют revision/idempotency.
- Offline не выглядит как живой worker; pending означает синхронизацию.
- Выбранные thread/job сохраняются отдельно для каждой вкладки. Новый чат
  очищает старую карточку; запоздавший fetch/SSE другого чата не меняет её.
  Восстановление выбора выполняет только GET, не resume или approval.
- Показать конкретную причину остановки и разрешённое действие восстановления;
  расширенные lease/generation сведения скрыть в технических подробностях.
- Не отдавать host paths, owner tokens, ключи и полные prompts через публичные
  DTO/ошибки. Проверить CSRF и область доступа к диагностике.
- Приёмка в реальном браузере: reload после BLOCKED, reconnect после restart,
  две вкладки и отсутствие сервера; приложить проверяемый отчёт без секретов.

## Исходные обязательные требования

Работай по [ТЗ 0.32](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md)
L01–L13, особенно L04/L05/L06/L11 и A08–A12/A18–A26/A30–A34.
Дополняй существующий чат без новой вкладки Autopilot.

Архитектурный контекст и источники — раздел 1.1 ТЗ. UI отображает доказуемый
переход и принятую передачу управления, но не выдаёт approval/lease от имени
модели. Graph checkpoint и доступная RAG-память не доказывают активность worker.

1. Используй backend lifecycle controller и VerificationContext; frontend не
   продлевает аренды и не исправляет SQLite статусы самостоятельно.
2. Общий submit/recovery/finalize обеспечивает совпадение execution outcome в
   job, saved task, Web task и diagnostics. Отсутствие ACK проекции показывай как
   «Синхронизация результата / Finalization pending», а не новый running.
3. Добавь additive DTO с отдельными saved_task_id, job_id, web_task_id,
   diagnostic_id, execution generation, status/phase/reason, projection_state,
   verification context ID, virtual root, environment label и check results.
   При недоступном metadata показывай «Нет данных / Unavailable», не PASS.
4. Обычная карточка: действие, статус, проект, окружение и итог проверок.
   Например: «Остановлена / Blocked · Ozon · .venv проекта · pytest PASS,
   Ruff FAIL». Общий verdict для этого состояния не зелёный.
5. В раскрываемой диагностике покажи причину loss, last heartbeat,
   task/job lease remaining, время/counters, context hash и environment identity.
   Owner tokens, секреты и реальные host paths не передавай клиенту.
6. При task/job рассогласовании показывай persisted reconciliation status и
   понятное действие; не предлагай бесконечную команду «продолжи» для обхода loss.
7. Сохрани раздельные права Ask/Plan/Agent и typed «Create repair task» 0.31.
   Read-only FAIL не становится allow-write от resume или выбора модели.
8. Resume использует ID+revision и сохранённый root/venv; не угадывает nested
   project из журнала. Root/environment drift требует новой проверки scope и
   обновлённого context, conflict показывает актуальный server state.
9. Missing venv/tool dependency показывай как проблему окружения с перечнем
   missing checks и доступным setup action. Не добавляй глобальную установку,
   автоматическое удаление `.venv` или скрытый fallback Python в кнопку retry.
10. SSE terminal event публикуй из durable outcome; replay по sequence после
    reconnect/restart не создаёт worker или новую approval. Projection pending
    получает последующее state update после ACK. При остановленном сервере UI
    показывает потерю связи, не симулирует продолжение работы.
11. Сохрани sticky header, пять режимов, смену модели, Enter-send, keyboard focus
    и мобильную компоновку. Heartbeat не вызывает постоянные aria-live сообщения.
    Labels новых полей — русский / English; технические подробности сворачиваются.
12. Добавь meaningful API/SSE/browser tests: долгий provider, lease renewal,
    blocked projection, stale running reconciliation, DB lag, duplicate terminal,
    cancel/takeover, restart, nested root/venv, denied environment, secrets.
13. Live browser на отдельном порту: отправь fixture-задачу, дождись verification,
    повтори после reload/restart, сопоставь UI с SQLite и receipts. Один HTTP health
    или строка кнопки в bundle не доказывают выполнение repair/verification.
14. Пересобери bundle, запусти TypeScript и frontend checks. В отчёте укажи
    конкретные выполненные browser/API сценарии и ещё не проверенные пункты.

Критерий приёмки: завершившаяся execution не остаётся визуально/логически running;
каждая проверка имеет подтверждённые root и environment, а итоговая готовность
следует из runtime receipts и согласованного terminal state.
