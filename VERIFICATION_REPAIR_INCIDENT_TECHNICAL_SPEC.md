# ТЗ 0.31: подтверждаемая repair-задача из verification evidence

## Дополнение к завершению 0.32 — запланировано

Нормативное дополнение: [ТЗ R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
и [порядок реализации](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).
Оно уточняет сохраняемый verification context, полный check plan, устойчивое
владение/reconciliation, сквозной REPAIR, Web/API и live/release gates.
Исходные A01–A34 и ограничения прав остаются обязательными. Опубликованная ветка
и 449 passed не означают закрытие production-приёмки. В этом этапе изменены
только документы; код новых требований ещё предстоит реализовать.

## Запланированное уточнение 0.32

[ТЗ жизненного цикла и контекста проверки](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md)
L01–L13/A01–A34 дополняет repair incident. Typed approval по-прежнему создаёт
новую allow-write задачу; source read-only не меняется. Новая repair execution
получает согласованный root/environment context, независимое обновляемое
владение task/job и terminal projection с защитой generation.

Исправление lease, evidence freshness и согласованности статусов не является
повышением прав source-задачи. Historical root-less evidence требует новой
правильной проверки перед выбором repair targets. Health/HTTP 202 и создание
relation не заменяют live FAIL→REPAIR→PASS. Исторические проверки 0.31 сохранены,
но длительный lifecycle последнего инцидента ещё не прошёл приёмку 0.32.

## 1. Назначение

Этап устраняет разрыв между безопасной read-only проверкой и исправлением
подтверждённых ошибок. Продолжение `verification-only` никогда не повышает права
исходной задачи. Для записи создаётся новый repair-инцидент через отдельное
типизированное действие пользователя.

Архитектурный принцип: текст промпта документирует правила, но соблюдение
обеспечивают серверная машина состояний, неизменяемые evidence, approval seal и
централизованный write-gate. История чата и semantic classifier не являются
источником полномочий.

## 2. R01 — один инцидент на одну корневую причину

Read-only verification является разведкой и источником доказательств. Runtime
группирует failed checks в repair proposals по устойчивому `root_cause_key`.
Несвязанные причины не объединяются. Пользователь выбирает proposals явно.

Каждая подтверждённая proposal создаёт новые `task_id` и `job_id`, workflow
`project-change`, mode `allow-write` и начальную фазу REPAIR. Исходные task/job,
их права, routing, objective, blocker и evidence остаются неизменными.

## 3. R02 — серверная машина состояний

Допустимый граф:

`PROPOSED → AWAITING_APPROVAL → APPROVED → REPAIRING → INDEPENDENT_VERIFY → COMPLETE`.

Терминальные альтернативы: `BLOCKED`, `STALE_EVIDENCE`, `CANCELLED`, `FAILED`.
Все переходы проверяет runtime. Недопустимый переход атомарно отклоняется и
записывается в append-only журнал.

## 4. R03 — хэшированный Repair Action Plan

До записи runtime формирует канонический план: source IDs/revision, project root,
root cause, evidence IDs, разрешённые paths, предполагаемые изменения, checks и
лимиты. Хранятся `plan_sha256` и `evidence_snapshot_sha256`.

Подтверждение пользователя связывается с обоими хэшами, source revision,
workspace/project root, path allowlist, actor/session и временем. Изменение любого
поля аннулирует approval. Устаревшее подтверждение не переиспользуется.

## 5. R04 — механический write-gate

Перед каждой файловой или shell-мутацией единая runtime policy проверяет:

- отдельную repair-задачу в APPROVED/REPAIRING;
- `allow_write=true` и подтверждённый approval seal;
- совпадение plan/evidence hashes и source revision;
- project root внутри workspace и target внутри утверждённого allowlist;
- связь work unit с failed evidence;
- lease/generation, conflict lock и оставшийся repair budget;
- отсутствие drift исходных файлов после подтверждения.

Гейт нельзя обойти prompt-текстом, semantic classifier, env-переменной, сменой
модели или сообщением «продолжай». Он покрывает write/edit/delete/rename/mkdir,
patch, форматирование с записью и mutating shell commands.

## 6. R05 — типизированный API без semantic routing

Web API предоставляет:

- `POST /api/jobs/{source_job_id}/repair-proposals`;
- `POST /api/jobs/{source_job_id}/repair-task`.

Создание принимает `confirmed=true`, `proposal_id`, expected checkpoint revision,
оба SHA-256, выбранные evidence IDs и idempotency key. Проверяются CSRF,
thread/workspace ownership, состояние source job, failed evidence, root и scope.
Endpoint не вызывает `resolve_intent`, LLM-классификатор или chat router. Повтор
одного idempotency key возвращает прежний результат.

## 7. R06 — доказательства как недоверенные данные

Immutable snapshot содержит check ID/name, команду, cwd/root, Python executable,
exit code/status, duration/time, bounded redacted output, full-output SHA-256,
truncation marker, paths/lines и runner correlation ID. stdout/stderr никогда не
исполняются как инструкции. Секреты удаляются до persistence и UI.

## 8. R07 — project root, окружение и drift

Root наследуется из ProjectCheckRunner и подтверждается ближайшим
`pyproject.toml`; общий `/workspace` не подменяет вложенный проект. Root проходит
resolve/symlink confinement. Python берётся из окружения проекта. Удаление `.venv`,
глобальная установка и смена системного Python требуют отдельного действия.

Перед первой записью и VERIFY runtime сверяет tree/target digests, manifest hash,
evidence fingerprints и revision. Drift закрывает gate состоянием
`STALE_EVIDENCE` и требует новой verification/approval.

## 9. R08 — ограниченные work units

Каждая unit имеет один root cause, evidence IDs, concrete mutation target,
непересекающийся path allowlist, expected effect и check command. Факт чтения,
случайный search hit, manifest name или текст лога не дают provenance. При
недостатке evidence создаётся точный structured blocker.

Параллельные repairs используют locks/conflict detection либо отдельные worktree.
Mutation receipt обязателен. Для одного локального дефекта допускается fast path,
но отдельная task, approval, gate, journal и независимый VERIFY сохраняются.

## 10. R09 — durable operational periods и transfer of command

Одна bounded unit является операционным периодом. На границе сохраняются phase,
план/hashes, receipts, changed files, checks, blocker, бюджеты и конкретная next
operation. Новый worker/provider продолжает по persisted state, не по чату.
Смена исполнителя записывается как transfer of command и не расширяет права.

## 11. R10 — независимый Safety Verifier

После REPAIR свежий read-only verifier читает фактический diff и запускает
runtime-owned ProjectCheckRunner. Самоотчёт repair-модели не является evidence.
Safety verdict может заблокировать завершение. COMPLETE требует mutation receipts,
PASS обязательных checks, совпадающих hashes/root и отсутствия открытого veto.

## 12. R11 — лимиты и provider lifecycle

Отдельно считаются plan revisions, approvals, repair cycles, safety rejections,
model calls и provider attempts. «Продолжай» не сбрасывает ceilings. Terminal
BLOCKED содержит failed checks, evidence, changes, counters и required action.

Rate limit/quota/timeout/circuit state имеет TTL и перепроверяется на командных
точках. Смена модели не теряет checkpoint и не даёт полномочий.

## 13. R12 — журнал и связь задач

Атомарно сохраняются source/repair IDs, relation `verification_repair`, source
revision, proposal, approval actor, idempotency key и hashes. Append-only ledger
фиксирует propose/request/confirm/invalidate/create/transfer/gate/mutation/verify/
veto/block/complete. Исправление записи выполняется компенсирующим событием.

## 14. R13 — безопасное самоизменение

Если ремонтируется Deep Context Agent, работающий процесс не изменяет собственную
исполняемую копию. Используется отдельный worktree/окружение. Установка,
перезапуск, публикация и rollback — отдельные подтверждённые этапы после PASS.

## 15. R14 — Web UX

Карточка допустимой failed read-only проверки показывает кнопку
«Исправить найденные ошибки / Create repair task». Диалог показывает source IDs,
root, failed checks, root-cause proposals, paths, scope и hashes; объясняет создание
новой allow-write task и неизменность source. Подтверждение вызывает typed API,
не чат. UI предотвращает double submit, восстанавливает relation после reload и
показывает REPAIR/INDEPENDENT_VERIFY/terminal blocker.

## 16. R15 — миграции и совместимость

Schema changes additive, transactional, idempotent и concurrency-safe. Старые
verification jobs читаются; при недостатке provenance repair proposal безопасно
блокируется. Task ID и job ID всегда подписаны отдельно.

## 17. R16 — observability invariants

Журнал и evidence имеют schema/version, size ceilings, UTF-8 validation, secret
redaction и SHA-256 полного содержимого. Проверяются дешёвые инварианты размера,
чтобы повторное кодирование или runaway logs выявлялись рано.

## 18. Приёмка A01–A30

1. Failed verification создаёт proposal без мутации.
2. Разные root causes разделяются; связанные симптомы группируются явно.
3. Source task/job остаются неизменными по защищённым полям.
4. Repair получает новые IDs, project-change, allow-write и REPAIR.
5. Без `confirmed=true` создание невозможно.
6. Semantic classifier и chat router не вызываются.
7. Неверные revision/plan/evidence hashes дают conflict.
8. Idempotency не создаёт дубликат.
9. Passed/foreign/unknown evidence отклоняется.
10. Вложенный project root и project Python выбираются правильно.
11. Выход root/target за workspace запрещён.
12. Write-gate запрещает запись до approval и вне allowlist.
13. Изменение plan/evidence/tree даёт STALE_EVIDENCE.
14. Инструкции из stdout/stderr не исполняются.
15. Секреты не попадают в БД, SSE, UI и JSONL.
16. Каждая unit связана с одним root cause и concrete target.
17. Параллельный конфликт детектируется.
18. Mutation receipt сохраняется атомарно.
19. Verifier работает read-only в свежем контексте.
20. Самоотчёт исполнителя не создаёт PASS.
21. Safety veto блокирует COMPLETE.
22. Лимиты нельзя сбросить continuation-сообщением.
23. Restart/provider handoff восстанавливает state без replay side effects.
24. Provider failures истекают по TTL.
25. UI требует явное подтверждение и предотвращает double submit.
26. Reload восстанавливает source→repair relation.
27. Работающий DCA не переписывает runtime-копию.
28. Полный сценарий verify FAIL → proposal → approval → repair → independent PASS.
29. Длинное «продолжи и исправь» не повышает права.
30. Ruff, format, pytest, mypy, compileall, frontend, migration, package и
    изолированные live API/UI regressions имеют фактический PASS.
