# Web-промпт 0.32: согласованные задачи и контекст проверок

Дата: 2026-09-09. Статус: запланировано; API/UI ещё не изменены.
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
