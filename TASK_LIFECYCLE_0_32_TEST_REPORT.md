# Проверки lifecycle 0.32 — 2026-09-09

Итог runtime-приёмки 2026-09-12: **PASS**, включая A23 на настоящем Ubuntu.
CI 34676350881: 518 passed (Python 3.11), 518 passed (3.12), Windows 516 passed /
2 POSIX-only skipped. Все quality/build steps PASS. Новые real LLM runs
pkhzwq5y/aqq_2xnn и cancel ieb8htit приведены в release matrix. Ниже сохранены
исторические результаты; прежние строки «A23 не закрыт» не являются текущим статусом.

Продолжение R1 от 2026-09-12: schema 2/provenance и saved-root-before-discovery,
namespace/custom origins на реальных venv, persistent root scan с CAS и отменой.
Последние два live проверяют context parent/task/job IDs и пять checks PASS;
повтор Web subprocess cancellation — 0.313 s. Артефакты и актуальные счётчики
в [release matrix](RELEASE_COMPLETION_0_32_ACCEPTANCE.md). A23 Unix/symlink не закрыт.

Актуальное дополнение 2026-09-12: [матрица и live-артефакты](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Добавлены реальные process cancellation/preflight/descendant tests, SQLite busy
task/job, queue wait, fair cursor, copied legacy DB, terminal approval replay,
safe execution DTO и HTTP fallback tests. Два real LLM end-to-end и браузерные
verification/reload/restart/offline выполнены. Исторические результаты ниже
сохранены для трассировки; они не заменяют новые release gates.

Этот отчёт сохраняет факты проведённых тестов. Новая матрица
[C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md) требует отдельного
сопоставления доказательств; существующие PASS не закрывают её автоматически.

Изолированные сценарии находятся в `tests/test_task_lifecycle_acceptance.py`.
Каждый из пяти сценариев выполняется дважды на чистых временных SQLite.
Пользовательские workspace, задачи, сервер и базы не изменяются.

| Сценарий | Проверяемое свойство | Результат |
| --- | --- | --- |
| Длительное ожидание без вывода | Ожидание 1.5 s превышает lease 1 s; независимые task/job heartbeat сохраняют владение и revision | PASS ×2 |
| Конкурентный takeover | Два потока одновременно претендуют на истёкшую аренду; один победитель; прежний worker не renew/finish/finalize новую generation | PASS ×2 |
| Штатный handoff | Живую аренду нельзя захватить через recovery; после partial/checkpoint новый owner сохраняет task ID, scope, права и routing | PASS ×2 |
| Crash после job commit | Отдельный Python-процесс завершается через os._exit(73) до финализации saved task; reconciliation переводит running в blocked | PASS ×2 |
| Crash после terminal outbox commit | Отдельный процесс завершается до projection; новое соединение применяет событие ровно один раз | PASS ×2 |

Команда целевой проверки:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_task_lifecycle_acceptance.py tests/test_task_lease_lifecycle.py -ra
```

Результат: **24 passed in 11.67 s**. Проверяется сохранение error code,
отсутствие автоматического повторного запуска terminal job и идемпотентность
повторной сверки. Предыдущий модуль дополнительно проверяет отмену,
устаревшую projection и реальный subprocess без stdout.

Полная регрессия: **449 passed, 1 skipped in 98.28 s**. Пропущено создание
symlink, недоступное в текущей Windows-среде. Ruff check и format (106 файлов),
mypy (31 source files) — PASS. Runtime-код в этом тестовом этапе не менялся.

Границы доказательства: задержка контролируемая, а не реальный медленный LLM;
тесты используют настоящие SQLite, потоки и аварийно завершённые процессы.
Это не доказательство всех переходов scheduler между implementation/repair units,
не полный A01–A34 и не основание объявлять всю версию production-ready.
Ранее выполненный live continuity с реальными API описан в IMPLEMENTATION_STATUS.md.
