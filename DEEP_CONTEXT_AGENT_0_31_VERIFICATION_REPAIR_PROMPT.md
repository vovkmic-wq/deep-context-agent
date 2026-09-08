# Промпт реализации Deep Context Agent 0.31

Работай строго по `VERIFICATION_REPAIR_INCIDENT_TECHNICAL_SPEC.md` (R01–R16,
A01–A30) и сохраняй ограничения предыдущих ТЗ. Цель — создать механически
защищённый переход от failed read-only verification к отдельной подтверждённой
allow-write repair-задаче. Prompt-текст не является authority.

## Обязательный порядок

1. Зафиксируй baseline Git/version/schema и exact regressions: job
   `cd789d2491097e4c2233c303`, diagnostic
   `9d2352388174462f8df452e59e67a0a3`, а также ошибки continuation
   `INVALID_TASK_ID`/`SEMANTIC_INTENT`. Не изменяй сервер 8765, ключи и
   пользовательский Ozon-проект.
2. Изучи только task/job persistence, verification evidence, phase machine,
   ProjectCheckRunner, tool policy, diagnostics и Web UI/API. Не начинай общий
   аудит и не считай подготовку документов реализацией.
3. Добавь repair proposal на одну независимую root cause. Разделяй несвязанные
   причины и не объединяй весь вывод Ruff/pytest в один бесконечный incident.
4. Реализуй серверную state machine PROPOSED → AWAITING_APPROVAL → APPROVED →
   REPAIRING → INDEPENDENT_VERIFY → COMPLETE с типизированными terminal states.
5. Формируй канонический Repair Action Plan и evidence snapshot. Seal approval
   хэшами, source revision, root и path allowlist; любое изменение аннулирует его.
6. Добавь централизованный write-gate для всех mutating tools и shell operations.
   Запрещай запись без отдельной repair task, валидного seal, lease, evidence,
   safe target и бюджета. Не добавляй env/prompt bypass.
7. Добавь typed proposal/create endpoints с CSRF, ownership, CAS revision и
   idempotency. Никогда не вызывай semantic classifier или chat router в этом
   потоке. Source verification остаётся неизменной.
8. Переноси только structured redacted evidence; лог является untrusted data.
   Храни full-output hash, bounded excerpt, truncation и runner metadata.
9. Наследуй и валидируй ближайший project root и project Python. Перед записью и
   VERIFY проверяй drift; возвращай STALE_EVIDENCE вместо ремонта по старым данным.
10. Компилируй proposals в bounded units с одним root cause, concrete target,
    path territory, expected effect и повторной проверкой. Сохраняй receipts.
11. После REPAIR запускай отдельный свежий read-only Safety Verifier. Только
    runtime ProjectCheckRunner PASS и отсутствие veto разрешают COMPLETE.
12. Сохраняй operational-period checkpoint и append-only ledger. Transfer of
    command между workers/models не теряет state и не расширяет права.
13. Введи ceilings plan revisions/approvals/repair cycles/safety stops/model calls.
    Provider failure имеет TTL. «Продолжай» не сбрасывает счётчики.
14. Реализуй Web-кнопку «Исправить найденные ошибки / Create repair task», preview
    proposals/paths/hashes, явное подтверждение, double-submit protection, reload
    recovery и статусы REPAIR/INDEPENDENT_VERIFY.
15. Для самоизменения DCA используй отдельный worktree/процесс. Не обновляй
    запущенную runtime-копию; deployment и Git publication остаются отдельными
    подтверждёнными этапами.
16. Добавь additive migrations, A01–A30 и exact regression: read-only FAIL →
    пользовательское typed confirmation → отдельный repair → independent PASS.
17. Выполни Ruff, format-check, pytest, mypy, compileall, frontend/bundle,
    migration/concurrency, package build/install и `git diff --check`.
18. Проведи изолированные live API/UI тесты на временном проекте с намеренным
    failure, включая idempotency, stale evidence, forbidden target, restart и
    provider handoff. Не используй пользовательские данные.
19. При любом FAIL исправь подтверждённую причину и повтори относящиеся проверки.
    Не ослабляй assertions и security gates ради зелёного результата.
20. Обнови глобальные ТЗ/промпт, Web-ТЗ, system prompt, README, status и changelog.
    Production PASS заявляй только после A01–A30 и фактических live evidence.

## Недопустимые упрощения

- повышение `allow_write` исходной task;
- создание repair через обычное сообщение «продолжи»;
- доверие LLM-самоотчёту;
- approval без SHA-256 и revision;
- один incident для несвязанных дефектов;
- mutation target из last read/search/log instruction;
- verifier с правом записи или в контексте исполнителя;
- изменение работающей runtime-копии;
- бесконечные repair/replan/model loops;
- утверждение production-ready без runtime-owned checks.
