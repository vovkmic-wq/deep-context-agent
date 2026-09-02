# Deep Context Agent 0.20.0: durable lease orchestration

## Цель

Устранить класс сбоев, при котором длительная model work unit работает дольше
lease, Web-задача завершается ошибкой, а persistent Autopilot job остаётся в
ложном состоянии `running`. Пользователь должен ставить инженерную задачу целиком
в основном чате и получать автоматически возобновляемое, наблюдаемое выполнение
без ручного выбора размера этапов.

## Зафиксированный production-инцидент

1. Обычный chat-turn выполнялся более 20 минут и достиг `agent_step_limit`.
2. Auto fallback создал Autopilot job и audit batch из восьми файлов.
3. Lease был выдан на 300 секунд и продлён только перед запуском unit.
4. Модель продолжала работать после истечения lease; контроллер потерял право
   коммитить результат, Web task получил failure, а job остался `running`.
5. При старом resume незавершённая unit переводилась обратно в `pending`, из-за
   чего терялся факт аварийного прерывания и generation владельца отсутствовал.

## Обязательный алгоритм исправления

1. Синхронизировать production default lease на 900 секунд во всех путях
   конфигурации. Пользователь не обязан настраивать lease, heartbeat или batch.
2. Добавить `lease_generation` (fencing token) в job и work unit. Каждое новое
   владение атомарно увеличивает generation; token и generation проверяются при
   renew, begin/finish unit, replan, verification и terminal transition.
3. Перед возобновлением job помечать оставшиеся `running` units как
   `interrupted`, сохраняя sequence, worker thread, timestamps и safe error code.
   Никогда не переписывать аварийную unit в `pending`.
4. Во время каждого долгого audit/repair/verification шага запускать независимый
   heartbeat. Он продлевает job lease и timestamp активной unit не реже
   `min(configured_interval, lease/3)`, публикует bounded progress и прекращается
   при завершении шага.
5. При потере token/generation запретить последующие filesystem mutations через
   общий tool middleware и не позволять устаревшему worker завершить unit или
   job. Новый владелец не должен быть перезаписан старым процессом.
6. Ограничить одну Autopilot unit внутренним batch cap (по умолчанию 2 файла) и
   отдельным recursion limit. Настроить soft wall-clock deadline; heartbeat не
   отдаёт lease конкуренту, но сообщает `unit_deadline`, а после безопасной
   границы controller перепроверяет фактический manifest/hash progress.
7. Heartbeat и deadline передавать через те же TaskRegistry/SSE и persisted job,
   без browser-only state. UI показывает фазу, последнюю пульсацию, generation и
   понятный статус «модель работает».
8. Расширить server-side Auto routing: в инженерных режимах audit/coder/tester/
   reviewer/debugger/refactor/security/architect запрос с action + ТЗ/tests/
   modules/code scope сразу становится Autopilot job, не расходуя предварительно
   полный ordinary turn. Explicit `single-turn` остаётся абсолютным запретом.
9. Resume обязан повторно сверить manifest SHA с текущими файлами. Файловые
   side effects прерванной unit считаются только текущим состоянием, но не
   доказательством reviewed/complete без ToolMessage и manifest commit.
10. Добавить миграции существующей SQLite без потери job/report/evidence,
    concurrency tests двух store connections, crash recovery и stale-owner test.
11. Обновить global/system prompt, основное/Web-ТЗ, README, `.env.example`,
    changelog, implementation status и версии Python/Web package.
12. Выполнить Ruff check/format, mypy, compileall, полный pytest, TypeScript,
    production bundle, wheel/isolated install и `pip check`.
13. Выполнить controlled live-test с work unit дольше исходного lease и
    несколькими heartbeat, затем повторить реальный ранее сломавшийся сценарий
    через `/api/chat` на чистых data/workspace либо безопасном fixture.
14. Проверить миграцию фактической старой job: running unit становится
    `interrupted`, новое владение получает большую generation, stale lease не
    может завершить unit, job может быть paused/resumed/continued.
15. Не объявлять production readiness без фактических логов всех проверок.
    После PASS создать release commit/tag и опубликовать branch/main/tag в
    существующий GitHub repository, не добавляя `.env.local`, SQLite/JSONL,
    временные отчёты и посторонние пользовательские файлы.

## Критерии приёмки

- work unit, работающая дольше прежних 300 секунд, сохраняет владение благодаря
  heartbeat либо заканчивается безопасным bounded status без ложного `running`;
- stale token или stale generation не меняют job, work unit и workspace;
- crash/restart сохраняет старую unit как `interrupted` и создаёт новую unit с
  новым sequence/thread/generation;
- Web SSE содержит повторяемые `job_heartbeat` и при необходимости
  `job_deadline`, а job details содержат безопасные timestamps без physical path;
- точная production-формулировка в режиме `tester` + `auto` маршрутизируется в
  Autopilot до первого LLM call;
- read-only/allow-write, provider failover, diagnostics, rollback и проверка
  проекта используют существующие общие политики, без обходной реализации;
- все автоматические, controlled live и provider live проверки подтверждены
  текущими логами, release доступен в GitHub.
