# Статус реализации

## Межплатформенный кандидат — 2026-09-12

Добавлен GitHub Actions workflow для Ubuntu 24.04 / Python 3.11 и 3.12,
Windows 2022 / Python 3.12. A23 использует настоящий symlink venv с Unicode/space
путём и проверяет launch, prefix, package origin, persisted context и confinement.
Публикация рабочей ветки нужна для CI; main/tag/release не изменяются.
До получения Linux evidence C18 остаётся незакрытым, версия остаётся 0.31.0.

## R1: typed context, происхождение окружения, paging — 2026-09-12

Реализована schema 2 VerificationContext: immutable/canonical hash, IDs и версии,
snapshot полномочий runner, root provenance, конфигурация/lock, code revision,
dependency versions/origins, venv launch/prefix. Компактные receipts ссылаются на
единственную запись; schema 1 не повышается до нового PASS. Saved root выбирается
до discovery при resume, REPAIR сохраняет parent context без повышения прав.
Namespace/src/custom-layout проверены реальными отдельными venv, включая чужую
checkout-копию. Root scan сохраняет frontier/offset/signature и валидирует дерево;
CAS commit не блокирует heartbeat во время filesystem IO. Отмена не теряет cursor.
Текущие evidence и оставшийся A23: [матрица](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Финальный pytest: 515 passed / 1 skipped; Ruff/format/mypy/compileall, frontend,
wheel/isolated-install PASS. Расширенный real LLM context roundtrip ×2 PASS,
повтор отмены pytest через Web — 0.313 s. Unix/symlink gate не пройден;
релиз и публикация пока не объявляются завершёнными.
Ниже сохранены результаты предыдущих итераций, не текущий список недоделок.

## R3–R6 и отмена subprocess — 2026-09-12

Реализованы bounded SQLite retry task/job, раздельные причины потери прав,
heartbeat в очереди и между units, persistent reconciliation cursor и карантин
legacy orphan tasks. Verification/preflight subprocess проверяет отмену/владение
каждые 100 ms и завершается с обычным деревом процессов, не ожидая общего timeout.
Дополнительный Web/pytest live выявил и устранил stale running job после cancel:
fenced abort_execution финализирует controller/units, не меняя нового владельца.
Повторы cancellation PASS: 0.343 / 0.312 s, последний с проверкой выхода PID.
По live evidence исправлены frozen REPAIR target/evidence и повтор approval,
переочередявший terminal job. Добавлены metadata-only execution DTO, durable SSE,
восстановление выбранного чата/job, offline и защита от чужого result card.

Два real LLM FAIL→REPAIR→PASS, browser verification, controlled HTTP fallback,
clean-wheel CLI/Web smoke и copied-DB migration выполнены. Полная регрессия:
496 passed, 1 skipped; Ruff/format/mypy/compileall/frontend — PASS.
Доказательства, run IDs и остаток: [C01–C18](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Общий release gate BLOCKED: полная typed context/provenance R1, paging resolver
и межплатформенная/symlink приёмка не закрыты. Package version 0.31.0 сохранена.
Рабочий Ozon/сервер/ключи не менялись. Ниже — исторические этапы.

## Реализация дополнения R1/R2 — в работе

2026-09-11: единый verification_passed gate подключён к runtime и model tool.
Он требует полный набор уникальных checks одного context/code/environment и
успешные exit codes. Неполный зелёный набор останавливается как
verification_evidence_incomplete без source REPAIR. Частичный tool-запрос имеет
project_pass=false. Authority/cancel проверяются между командами; выполняющийся
subprocess на этом историческом этапе был ограничен timeout; исправлено выше.
Обновлены явные test doubles полного плана, не ослаблены production gates.
Полная регрессия: **472 passed, 1 skipped in 50.90 s**. Ruff/format/mypy PASS.
Сквозные live C11–C18 не закрыты; изменения не опубликованы.

2026-09-10: transfer контекста добавлен в frozen repair evidence/relation;
проверка hash отклоняет повреждение. VERIFY использует переданную ссылку при
отсутствии собственных receipts. Preflight проверяет origin обычных top-level
пакетов в root/src; реальный временный venv с .pth в чужую копию блокируется.
Причина origin failure сохраняется в безопасном сообщении. Namespace/custom
layouts требуют дальнейшей поддержки; полный C01/C02 пока не закрыт.
Полная регрессия: **465 passed, 1 skipped in 51.98 s**. Ruff/format/mypy PASS.
Live нового сквозного scheduler-сценария не выполнялся; изменения локальные.

Следующий прогон: **461 passed, 1 skipped in 52.24 s**; mypy PASS.
Добавлены тесты и исправления environment drift, uv/poetry lock и requirements
fingerprint. Probe включает hash версий установленных distributions и повторяется
перед принятием результата. VERIFY восстанавливает root из сохранённого receipt;
конфликт явного root блокируется. Полный transfer новой repair-задаче и package
origin ещё не закрыты; live/release gate не заявлен.

Добавлено workspace-scoped content-addressed SQLite-хранилище verification
metadata с проверкой целостности и подключением к runner. Это ещё не полный
типизированный transfer-контракт R1. Default check plan включает compileall,
а mypy определяется по pyproject.toml/mypy.ini/.mypy.ini/setup.cfg.
Явный частичный запрос сохраняется и отмечается в metadata.
Промежуточная регрессия: 455 passed, 1 skipped; после неё добавлен отдельный
тест setup.cfg (целевые context/plan: 7 passed). Ruff/mypy проверяются повторно.
Матрица остатка: [C01–C18](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Новый live/release gate не выполнялся, production PASS и публикация не заявлены.

## Завершение production-приёмки 0.32 — документы подготовлены

Добавлены [промпт из 16 шагов](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md)
и [ТЗ R1–R6 с критериями C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md).
Обновлены глобальные ТЗ/промпт, Web-промпт, связанные спецификации scheduler,
continuity, orchestration, verification/repair и системные указания.
Статус новых критериев: NOT RUN до привязки актуальных доказательств.
Этот этап документационный: Python/TypeScript, БД и версии не изменялись.
Последний подтверждённый полный прогон — 449 passed, 1 skipped; он не закрывает
новые требования автоматически. Реализация и выпуск остаются отдельным этапом.

## Task lifecycle / verification context 0.32 — в работе, 2026-09-09

Дополнительная приёмка по запросу пользователя: длительное ожидание с двумя
heartbeat, конкурентный takeover, штатный handoff и два crash-window выполнены
дважды на новых БД. Целевой набор — 24 passed. Методика и границы доказательства:
[TASK_LIFECYCLE_0_32_TEST_REPORT.md](TASK_LIFECYCLE_0_32_TEST_REPORT.md).

Реализованы и проверяются: независимый saved-task heartbeat, CAS renewal без
смены revision, terminal outbox и fenced projection, связь task/job generation,
startup/периодическая сверка статусов. Добавлен статус finalization_pending в UI.
Проверки требуют окружение целевого проекта, выполняют preflight и сохраняют
fingerprint исходников/окружения; изменение исходников делает результат stale.

Промежуточный прогон 2026-09-09: pytest — 439 passed, 1 skipped (Windows symlink);
Ruff check/format — PASS; mypy — PASS (31 source files); TypeScript, frontend
tests и сборка — PASS. Тесты включают реальное временное venv/compileall и
восстановление после разрыва двух commits с проверкой generation fencing.
Live continuity — PASS: четыре реальных Web-хода (создание файла, анализ лога,
перезапуск приложения, продолжение и чтение) с существующим ключом. Данные:
`%TEMP%/dca-continuity-live-8lig0cvh`; первый request
`41713fe917174e20953104ca9a0a35eb`, последний `8aa8b6a307a9401cb1f272343921bf16`.
Дополнительный тест silent subprocess дольше исходного lease — PASS;
модуль lifecycle — 14 passed. Это отдельный реальный subprocess-тест,
не подмена live-проверки медленного LLM.
После добавления явного project_root в model tool обновлены тестовые fixtures
контракта runner; полный повторный прогон завершился без падений (90.85 s).
Это ещё не приёмка A01–A34: полный live-повтор timeout/handoff/recovery,
проверка UI и оставшиеся требования VerificationContext не завершены.
Версия 0.32 не выпущена; итоговый production PASS не заявлен.

Созданы:

- `DEEP_CONTEXT_AGENT_0_32_TASK_LIFECYCLE_PROMPT.md` — пошаговая реализация;
- `TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md` — L01–L13, A01–A34;
- `DEEP_CONTEXT_AGENT_0_32_WEB_TASK_STATE_PROMPT.md` — API/SSE/UI.

Обновлены глобальный промпт/ТЗ, Web-ТЗ, system prompt, связанные документы
scheduler, continuity, orchestration, verification/repair, README и CHANGELOG.
Повторно изучены статья Habr 1064052 и официальные материалы LangChain;
источники и конкретное применение отделены от собственных требований в ТЗ 0.32.

Incident job `9134483d5b0edd7fe90e758e`, saved task
`d97b9babc8b740e4b7ff5427770d0c87`, parent diagnostic
`a3a399f949704266abfefbbef5fb497d`: 2026-09-08 срок saved-task lease истёк в
18:37:56 по Москве, job heartbeat оставался активным до 18:39:07; в 18:39:11 job
завершился blocked/task_authority_lost, saved task осталась running.
Сохранённый VERIFY использовал `/workspace` и Python агента вместо вложенного
Ozon. pytest 11 passed — факт прежнего лога, а не новый PASS в правильном root;
Ruff check/format оставались красными. Два provider timeout примерно по 120 секунд
увеличили время исполнения, но их внешняя причина отдельно не установлена.

План: единый ownership controller с dual renewal, fenced finalization/outbox,
bounded reconciliation, валидированный root/environment context во всех VERIFY,
честный Web status и повтор инцидента с takeover/crash/timeout live-сценариями.
На исходном документационном этапе A01–A34 имели статус NOT RUN; актуальные
промежуточные результаты приведены выше. Версия остаётся 0.31.0.

Проверка документации 2026-09-09: 22 изменённых/новых файла, включая текстовый
system prompt, проверены на строгий UTF-8 без BOM/U+FFFD; 40 локальных
Markdown-ссылок существуют. Последовательности L01–L13, A01–A34, 21 шаг основного
и 14 шагов Web-промпта корректны. `git diff --check` — PASS; version files без
изменений. Это проверка документов, не выполнение приёмочных сценариев A01–A34.

## Verification repair incident 0.31.0 — реализовано, 2026-09-08

Созданы `VERIFICATION_REPAIR_INCIDENT_TECHNICAL_SPEC.md` (R01–R16, A01–A30) и
`DEEP_CONTEXT_AGENT_0_31_VERIFICATION_REPAIR_PROMPT.md`. Обновлены глобальные
ТЗ/промпт, Web-ТЗ, system prompt, README и CHANGELOG.

Этап основан на фактическом сценарии: job `cd789d2491097e4c2233c303` корректно
сохранил failed verification в read-only, но попытка продолжить его как Agent не
могла безопасно повысить сохранённое `allow_write=false`; длинная формулировка
дополнительно попала в semantic ambiguity. Целевое решение — отдельный typed
repair workflow с immutable evidence и явным подтверждением пользователя.

Зафиксированы: один incident на root cause, canonical Repair Action Plan и
evidence SHA-256, механический write-gate, STALE_EVIDENCE, bounded work units,
append-only ledger, durable transfer of command, независимый read-only verifier,
provider TTL и изоляция самоизменения в worktree.

Реализованы typed proposal/create API, отдельные task/job IDs и allow-write
repair-фаза без semantic routing. Source read-only job остаётся неизменным;
подтверждение связано с source revision, canonical plan/evidence SHA-256 и
idempotency key. Централизованный mutation gate ограничивает approved paths,
проверяет исходные target/manifest digests и отклоняет drift как
`STALE_EVIDENCE`. После mutation receipt выполняется runtime-owned read-only
ProjectCheckRunner; модельный самоотчёт не создаёт PASS. Append-only incident
ledger хранит proposal, approval, invalidation, creation и terminal outcome.

Web UI показывает отдельную кнопку «Исправить найденные ошибки / Create repair
task», preview root/checks/paths/hashes, явное подтверждение и предотвращает
double submit. Reload повторно использует persisted source→repair relation.

Финальный автоматический контур: Ruff check/format — PASS, pytest — 418 passed и
1 штатный Windows symlink skip, mypy — PASS по 29 source-файлам, compileall —
PASS, TypeScript/bundle/model-choice — PASS. Wheel/sdist 0.31.0 и изолированная
установка пакета проверены. Изолированный live Web API на портах 8879/8881/8882 подтвердил
health/UI bundle, proposal_count=1, отказ без confirmation=409, создание новой
repair task=202, идемпотентный повтор с тем же job ID и runtime version=0.31.0.
`doctor --live` подтвердил Zhipu GLM-5.3 `live_response=OK`, configured OpenAI
fallback и hybrid FastEmbed/Qdrant memory. Пользовательский порт
8765 и Ozon-проект не затрагивались.

Исторический итог этого прогона: перечисленные automated checks и ограниченные
live API-сценарии прошли. Прежняя формулировка «production PASS локально» была
слишком широкой: успешное создание repair-задачи и doctor не доказывают полный
длительный repair→VERIFY→terminal lifecycle. Последующий incident и оставшаяся
работа зарегистрированы выше в этапе 0.32; результаты старых тестов не удалены.

## Verification-only execution 0.30.0 — реализовано, 2026-09-08

Live job `b6b00104265fddec6ab4caf6`, diagnostic
`b77bc36c73c04006b549842456e6b5be` подтвердил новый incident. Прямая просьба
только завершить проверки была маршрутизирована как `project-change`: runtime
создал DISCOVER и IMPLEMENT вместо VERIFY, прочитал вложенный
`/workspace/ozon_market_analytics/pyproject.toml`, затем ошибочно назначил этот
manifest целью `edit_file`.

Фактические показатели: 1 yielded и 1 failed unit, 14 model generations, два
`OpenAITimeoutError` по 120 секунд, рост input estimate примерно 34k→45k tokens,
0 mutations и 0 successful checks. Четыре `run_project_checks` завершились
error/denied; terminal `no_verified_progress` сохранил `blocker_json={}`, а parent
diagnostic при `status=blocked` оставил `error_code=null`. Rollback успешен,
filesystem side effects отсутствуют.

Созданы `VERIFICATION_ONLY_EXECUTION_TECHNICAL_SPEC.md` (V01–V12, A01–A22) и
`DEEP_CONTEXT_AGENT_0_30_VERIFICATION_ONLY_PROMPT.md`. Обновлены глобальные
ТЗ/промпт, Web-ТЗ, system prompt, README, CHANGELOG и связанные нормативные
документы 0.28/0.29.

Реализованы отдельный workflow `verification-only`, прямой старт с VERIFY,
bounded root resolver ближайшего `pyproject.toml`, явный cwd для
ProjectCheckRunner, evidence-only REPAIR с одним target, защита manifests от
recent-read inference, обязательный structured blocker и наследование terminal
error code в Web parent diagnostics. Модель не вызывается при VERIFY PASS и при
read-only FAIL; число repair/model/provider повторов и входной контекст ограничены.

Live-приёмка на изолированной вложенной копии:

- `doctor --live`: Zhipu GLM-5.3, `live_response=OK`; основной порт 8765 не
  останавливался;
- PASS job `47fbaef53c33c58bdb4cf4fe`: первая и единственная unit `verify`,
  root `/workspace/deep-context-agent`, `verification=passed`, COMPLETE,
  0 discovery/implement/replan/model calls;
- forced failure job `51d804a22347cb6ef651372b`: одна VERIFY unit,
  `verification_failed`, непустой blocker с root и failed checks, без REPAIR или
  файловых изменений в read-only режиме;
- первоначальный live-запуск выявил слишком жадное извлечение virtual path;
  parser исправлен и оба сценария повторены на новой чистой БД.
- изолированный Web API job `4d9d37425ce9a67e425bc552` на порту 8879:
  health=ok, version=0.30.0, workflow=`verification-only`, выбран
  `/workspace/nested-demo`, `verification=passed`, terminal SSE=`completed`;
  тестовый процесс остановлен, пользовательский порт 8765 не затрагивался.

Финальный контрольный контур:

- Ruff check/format — PASS, 94 Python-файла;
- pytest — 412 passed, 1 штатный Windows symlink skip;
- mypy — PASS, 28 source-файлов; compileall — PASS;
- TypeScript, bundle и model-choice tests — PASS;
- wheel/sdist 0.30.0 собраны; `pip check` — PASS.

Статус: **0.30.0 production PASS локально после финального контрольного контура**.

## Executable handoff recovery 0.29.0 — реализовано, 2026-09-08

По job `103bdbab6c558da6761380dc` и diagnostic task
`488aa222a53743a9a04690ef6bc96262` подтверждён новый incident: 3 yielded units,
6 чтений, 5 context searches, но 0 changed files и 0 checks. Runtime сохранил
`operation=edit_file` с пустым `target`, пустыми `blocking_conditions` и
`verification_commands`, затем вернул
`implementation_blocked_missing_information`. Заявленная точная предпосылка в
checkpoint отсутствует.

Созданы `EXECUTABLE_HANDOFF_RECOVERY_TECHNICAL_SPEC.md` (E01–E12, A01–A18) и
`DEEP_CONTEXT_AGENT_0_29_EXECUTABLE_HANDOFF_RECOVERY_PROMPT.md`. Обновлены
глобальные ТЗ/промпт, Web-ТЗ, system prompt, README и CHANGELOG. Требуются
типизированный contract, preflight gate, goal graph, bounded recovery, точный
blocker, leaf verification, persistence/restart и Web observability.

Реализованы schema-validated `NextOperation`, preflight до `begin_unit`,
ограниченное targeted recovery, выбор следующего target из точных путей и
подтверждённых read receipts, запрет записи за пределами target и структурный
blocker в SQLite/Web progress. Runtime-owned verification остаётся обязательным
условием COMPLETE. Исторический incident покрыт детерминированными regression-
тестами.

Финальная проверка 2026-09-08:

- Ruff check и format-check — PASS, 92 Python-файла отформатированы;
- mypy — PASS, 28 source-файлов; compileall — PASS;
- pytest — 392 passed, 1 штатный Windows symlink skip;
- TypeScript, bundle и model-choice tests — PASS; wheel/sdist 0.29.0 собраны;
- `pip check` — PASS; `doctor --live` — GLM-5.3, `live_response=OK`;
- изолированный Web API на порту 8879 — health/runtime 200, версия 0.29.0,
  hybrid FTS5 + FastEmbed/Qdrant доступен; основной порт 8765 не остановлен;
- normal live job `e709dc83192e96b636257c92`: `/workspace/service.py`,
  1 changed file, runtime VERIFY, 1 check, `verification=passed`, COMPLETE;
- forced-invalid live job `b5d53085935d9ce498f48cb2`: пустой mutating target
  отклонён до implementation worker (`phase_attempts=0`), сохранён непустой
  blocker `planner_contract_invalid`; пользовательские файлы не затронуты.

Статус 0.29.0: **production PASS локально**. Git-публикация в этом ходе не
выполнялась, поскольку она требует отдельной актуальной команды по промпту 0.29.

## Production orchestration 0.28.0 — реализовано, 2026-09-07

Выполнены R01–R07 и A01–A14 из
`PRODUCTION_ORCHESTRATION_TECHNICAL_SPEC.md`. Межмодульные запросы теперь
маршрутизируются как persistent `project-change`, а точный файл с pytest сохраняет
узкую область и получает независимое разрешение checks. `run_project_checks`
удалён из project-discovery gate; актуальные права передаются модели раздельно.

Runtime-owned VERIFY уже не зависит от добровольного tool call модели и только
PASS фиксированного ProjectCheckRunner допускает COMPLETE. Work-unit objective
ограничен 12 000 символами с SHA-256 полной durable-цели. Добавлен локальный
schema-first snapshot Python contracts (до 200 файлов/500 symbols/12 000 символов)
без тел файлов. Provider circuit открывается после одного транспортного сбоя на
300 секунд и показывает безопасный state/retry-after. Terminal parent агрегирует
persisted descendant provider/tool/rollback/filesystem evidence.

Автоматическая приёмка: Ruff check/format — PASS; `mypy src` — PASS;
`pytest -ra` — **389 passed, 1 skipped** за 40.43 s; skip относится только к
недоступному Windows symlink. `compileall`, `pip check`, TypeScript noEmit,
bundle/model tests и сборка wheel/sdist 0.28.0 — PASS.

Изолированный live `doctor --live`: `zhipu/glm-5.3`, ответ `OK`, circuit threshold
1/cooldown 300. Web smoke на `127.0.0.1:8878`: `/api/health` и `/` — HTTP 200,
версия 0.28.0; сервер штатно остановлен, порт 8765 не затронут. Forced transport
failure Zhipu подтвердил немедленную попытку OpenAI fallback; OpenAI вернул внешний
`429 OpenAIRateLimitError`, поэтому успешный live fallback не заявляется. Локальный
детерминированный circuit regression подтверждает skip открытого primary.

## Durable scheduler 0.27.0 — реализован, 2026-09-07

По diagnostic `83bbc5d39bf7410b87462378c5259487` зафиксирован новый инцидент:
job `5dc5c86b57fd25f873da7acd` после 7 soft yields остановлен общим
`task_deadline` за 902.141 s; 12 чтений/960 уникальных строк, 0 changed files,
0 checks. Provider и vector backend не были причиной остановки.

Созданы `DURABLE_EXECUTION_SCHEDULER_TECHNICAL_SPEC.md`, основной промт 0.27 и
отдельный Web-промт. Обновлены глобальные ТЗ/промпт и capability-aware system
prompt. Выбран production-подход: phase machine + persistent scheduler +
evidence-driven renewal; раздельные timeout — базовая настройка.

Реализованы независимые model/unit/active/wall бюджеты, конечный автомат фаз,
ограничение discovery, durable next operation и receipts, lease/CAS fencing,
crash recovery без списания downtime, Web recovery и SSE replay. Сохранённая
задача возобновляется по прежнему job/task identity без запуска project audit;
повторная правка одного файла учитывается как новый подтверждённый прогресс.

Автоматическая регрессия 2026-09-07: Ruff check/format — PASS; `mypy src` — PASS;
`pytest -ra` — **383 passed, 1 skipped** за 38.57 s. Skip относится только к
недоступному созданию Windows symlink. TypeScript noEmit, bundle/model tests,
compileall, pip check и сборка wheel/sdist 0.27.0 — PASS.

Live evidence на независимых чистых SQLite, существующие ключи без печати:

- `doctor --live`: primary `zhipu/glm-5.3`, fallback `openai/gpt-5.6-sol`,
  `live_response=OK`, hybrid FastEmbed/Qdrant configured;
- PASS №1: job `0d68f3fc4102d74b33c776ad`, task
  `5fbacdd4058141a3a1ed8365576fce3c` — 5 yields, 14 чтений, 5 диапазонов,
  restart/resume, 1 exact-once mutation; primary timeout корректно использовал
  OpenAI fallback;
- controlled blocker: job `38d77c035c8167fbf0ec0e32` — после полезных units
  модель трижды не дала нового evidence; точный `no_verified_progress`, без
  небезопасной записи;
- PASS №2: job `7b6355a08d9ce5e506b2122c`, task
  `493661fade674d1fb11ad1c279f91d2b` — 6 yields, 14 чтений, 5 диапазонов,
  restart/resume, 1 exact-once mutation.

Оба успешных Web API/SSE прогона подтвердили partial terminal replay после
перезапуска и отсутствие project-audit manifest. Физические временные каталоги,
SQLite и live reports не входят в release-артефакты.

Изолированный HTTP smoke на `127.0.0.1:8877`: `/` и `/api/runtime` вернули 200,
версия 0.27.0, retrieval `hybrid`, model-turn timeout 180 s, active-task budget
14 400 s и discovery ceiling 2. Сервер штатно остановлен; порт 8765 не затронут.

## Bounded progress recovery 0.26.0 — реализовано, 2026-09-06

Выполнены `DEEP_CONTEXT_AGENT_0_26_BOUNDED_PROGRESS_RECOVERY_PROMPT.md` и
`AUTOPILOT_PROGRESS_RECOVERY_TECHNICAL_SPEC.md` (P01–P15, A01–A26). Исправлен
инцидент 0.25.0, где успешные шаги модели исчерпывали recursion/failure retries,
а runtime не мог доказать следующий шаг. Кодовая версия повышена до 0.26.0.

| Область | Состояние |
| --- | --- |
| P01/P02: runtime ledger, forced checkpoint, soft yield | Реализовано: receipts, counters и next operation сохраняются до ответа модели |
| P03/P04: интервалы чтения, виды прогресса, лимиты | Реализовано: versioned range union, page/byte budgets, duplicate-range guard |
| P05/P06: узкие units и execution escalation | Реализовано: soft yield не расходует failure retries, escalation ограничена правами и бюджетами |
| P07/P08: parent/child диагностика и UI counters | Реализовано: parent task связывает child attempts, необработанные ошибки журналируются с redaction |
| P09: миграции/restart/crash safety | Реализовано и проверено продолжением после перезапуска на той же SQLite |
| P10/P11: автоматические и повторные live-тесты | PASS; точные результаты приведены ниже |
| P12–P15: выпуск, adaptive routing, memory UI, retrieval eval | Реализовано; финальная публикация фиксируется коммитом релиза |

Дополнительно реализованы единый Unicode-aware token estimator, сохраняемые Web-
настройки adaptive profiles, фактический индикатор `FTS5/BM25 + vector`, локальный
FastEmbed/Qdrant с lexical fallback и golden retrieval metrics hit@k/MRR. Ошибка,
найденная live-проверкой (`Conversational Autopilot exhausted without terminal
state`), устранена: persistent workflow использует общий лимит work units, а
достижение потолка даёт контролируемый `work_unit_limit_exhausted`, не assertion.

Live evidence на изолированных workspace/SQLite (существующие ключи, без записи
секретов в Git):

- `doctor --live`: Zhipu `glm-5.3` и резерв OpenAI доступны, memory mode `hybrid`;
- resume/routing: `%TEMP%\dca-resume-routing-live-m9v_6z43\result.json` и
  `%TEMP%\dca-resume-routing-live-nmxyvzho\result.json` — PASS, audit runs 0;
- bounded progress, два независимых прогона `scripts/live_bounded_progress.py
  --repeat 2`: PASS; jobs `7fe11482671abcbaa8e24495` и
  `ccef18df14bde8a594a550e1`, 14 успешных read receipts, пять страниц одного
  файла и ровно одна результирующая запись в каждом прогоне;
- Web browser: версия 0.26.0, sticky chat header, adaptive profiles сохраняются
  после restart, первая индексация `indexed=1`, повторная `unchanged=1`, hybrid
  retrieval показывает `FTS5/BM25 + vector` после ленивой загрузки модели.

Финальная автоматическая регрессия 2026-09-06: `pytest -ra` — **369 passed,
1 skipped** за 41.05 s; skip относится только к недоступному созданию Windows
symlink. Ruff check/format — PASS, 83 Python-файла; mypy — PASS, 27 source
modules; TypeScript noEmit, production bundle и два Web bundle/model tests —
PASS; compileall и pip check — PASS; из чистого sdist успешно собраны
`deep_context_agent-0.26.0.tar.gz` и wheel 0.26.0.

Границы подтверждения: это локальный single-user Web runtime; первый запуск
FastEmbed скачивает модель. В одном раннем диагностическом прогоне реальный GLM
timeout корректно перешёл на OpenAI fallback. Нагрузочный многопользовательский
benchmark и live-сбор данных Ozon в приёмку Deep Context Agent не входят.

## Task resume / model resources 0.25.0 — 2026-09-04

Выполнено по `DEEP_CONTEXT_AGENT_0_25_RESUME_MODEL_ROUTING_PROMPT.md` и
`TASK_RESUME_MODEL_ROUTING_SPEC.md` (R1–R6). Существующие изменения 0.24 и
пользовательские prompts сохранены, Ozon workspace и рабочий сервер не изменялись.

1. **R1:** additive SQLite `task_checkpoints`, исходная цель, план, следующий шаг,
   отдельные tool receipts; task/job identity не зависит от текста продолжения.
   CAS/owner/revision/lease защищают сохранение и terminal от устаревшего worker.
2. **R2:** общий Web/CLI structured intent resolver. Точные команды локальны;
   неоднозначные отсылки используют ограниченный classifier с JSON-schema,
   timeout и журналом. Структурированный intent возвращается в routing metadata.
   Неизвестный ID/низкая confidence/ошибка/несколько задач не создают новый аудит.
3. **R2:** пересечение прежних прав с текущими Ask/Plan/allow-write/read-only;
   актуальная инструкция передаётся отдельно от исходной цели и checkpoint.
   Логи, служебные XML-теги и имена `resume.txt` не создают разрешений.
4. **R3:** project-change/project-test используют execute units без audit manifest.
   Следующий шаг продолжается из checkpoint, пауза сохраняется; успешный worker
   не закрывает общую задачу без оператора. CLI `job` проверен на двух units.
5. **R4:** конфигурируемые fast/standard/reasoning profiles, high-risk-first,
   tools/context/full schemas/checkpoint/output reserve, local-only, optional
   cost/latency ceilings. Ручной выбор сохраняет приоритет. Fallback выбирается
   только среди допустимых кандидатов и не переисполняет tools.
6. **R5:** выбранная модель, оценка размера/стоимости и причина выбора в actual
   model attempts; intent/task ID/next step в Web/SSE. UI запоминает auto/manual,
   heartbeat подписан как время последней активности, partial не скрывается.
7. **R6:** регрессии и повторные live через реальный Web runtime на отдельных
   SQLite. Разработка → лог → restart → точная длинная команда → тот же task/job,
   файл LIVE_A/LIVE_B, затем Ask без записи. Audit runs: **0**.

Live evidence (локальные временные файлы, не включаются в Git):

- `%TEMP%/dca-resume-routing-live-p5g78cew/result.json`: PASS первого полного
  сценария после исправлений.
- `%TEMP%/dca-resume-routing-live-0hshoohy/result.json`: повторный PASS,
  job `352264b87cce756bad6025ca`, 15 actual model attempts. Использованы существующие
  GLM-5.3/OpenAI; реальный GLM timeout 45 s завершился fallback без потери прогресса.
- `%TEMP%/dca-resume-routing-live-5ruqawva/result.json`: отдельный classifier PASS,
  `zhipu/glm-5.3`, effort `low`, request `c2565e00f7134aacbb05da34944eecbe`.
- Исправления, найденные именно live: будущее «продолжение» внутри новой задачи;
  имя файла `resume.txt`; GLM-5.3 не принимает отключённое thinking. Для classifier
  используются enabled/low согласно
  [официальной документации GLM-5.3](https://docs.z.ai/guides/llm/glm-5.3).

Проверки версии 0.25.0:

- pytest: **353 passed, 1 skipped**, 32.14 s; skip — создание Windows symlink.
- Ruff check/format: PASS, 76 Python files; mypy: PASS, 25 source modules.
- TypeScript noEmit, production bundle, model choices tests: PASS;
  JS+CSS — 62 307 bytes. Это проверки сборки, не ручной визуальный UX-аудит.
- compileall и pip check: PASS; wheel и sdist 0.25.0 собраны.
- Изменения runtime покрыты реальными live API-вызовами, переходы прав и лимиты —
  детерминированными регрессиями. Нагрузочный многопользовательский production
  benchmark не проводился; область применения остаётся локальной.

Границы подтверждения:

- Профили в пользовательском `.env.local` не создавались автоматически. Без них
  остаётся configured-chain, а не заявленная экономия от разных моделей.
  В live каждый реальный ID назначался нескольким tier для проверки механизма;
  сравнение качества/стоимости трёх различных моделей не проводилось.
- Оценка complexity/risk объяснимая эвристическая, а не гарантия точной семантики.
  Semantic fallback ограничен интерпретацией продолжения и не выдаёт полномочий.
- Латентность/надёжность process-local, тарифы/качество задаёт оператор;
  оценка стоимости на вызов не равна счёту провайдера или бюджету всей задачи.
- Старые job/history не импортируются как новые полномочия. Если сохранённой
  задачи нет, требуется новая прямая постановка задачи; старый текст лога не годится.
- Работающий сервер не перезапущен: уже импортированный старый runtime заменяется
  только после перезапуска. Git commit/push в этом этапе не выполнялся.

## Task continuity / reliability / model choices 0.24.0 — 2026-09-03

Реализовано по `DEEP_CONTEXT_AGENT_0_24_TASK_CONTINUITY_PROMPT.md`:

1. Существовавшие пользовательские untracked prompts не изменены. Baseline:
   298 passed, 1 Windows symlink skip; исходный commit ad308270.
2. `TaskStateStore` в context SQLite хранит цель прямого запроса, scope/rights,
   workflow, состояние, revision, lease и ссылку на результат хода. История и
   детали persistent job остаются в прежних Context/Autopilot stores.
3. Общий Web/CLI resolver: разработка → боковой лог → продолжение; Ask/Plan/
   allow-write ограничивают возобновлённые права. Текущие права добавляются к
   каждому вызову модели. Данные логов не создают полномочий.
4. Diagnostics schema 3 сохраняет каждую реальную model attempt до и после
   inference, effective temperature/reasoning effort, hash, usage, finish/stop,
   retry и cancellation. Crash-left attempts становятся interrupted, без
   выдуманного количества токенов или длительности.
5. Conservative повтор одинаковых абзацев: off/observe/enforce; code fences,
   JSON, tool calls и таблицы исключаются. No-progress использует новые реальные
   tool evidence, а не количество обещаний в тексте.
6. Output/call/time budgets; transient-only retries; отказ от graph replay;
   проверка отмены/lease/deadline перед model/tool calls. Responses incomplete и
   Chat Completions length не переходят к исполнению неполных tool calls.
7. Saved task отделён от retrieval. Невыполненный одиночный этап остаётся partial;
   оператор явно закрывает задачу. Нельзя молча выбрать одну из нескольких задач.
8. Web/SSE: partial/blocked/cancelled/completed, persistent terminal, JSONL terminal,
   выбор saved task и кнопки закрытия/отмены с optimistic revision check.
9. Регрессии покрывают Web/CLI continuity, revoked write, Ask, другую область,
   занятость/устаревший owner/истечение lease, отмену после записи, повтор текста,
   tool no-progress, budgets, миграцию, crash recovery и сохранность диагностики.
10. Изолированный live через реальный Web runtime с существующим ключом:
    создание файла → анализ лога (0 tools) → новый экземпляр приложения с теми же
    SQLite → read/edit/read → Ask без изменения файла. Два прогона PASS.
11. Обновлены глобальные ТЗ, управляющий и системный prompts, README, CHANGELOG,
    пример конфигурации. Версии Python и Web согласованы: 0.24.0.
12. Каталоги Web/провайдеров: пять выбираемых моделей, даты с provenance, full
    catalog validation, alias/snapshot dedup, сохранение старой активной модели.
    Для OpenAI model switch не переносит несовместимый effort `none` в Pro.

Проверки:

- pytest: **324 passed, 1 skipped** (Windows не разрешает создание symlink).
- Ruff check/format, mypy (22 source files), compileall, pip check — PASS.
- TypeScript noEmit, production bundle, отдельный тест model choices — PASS.
- Wheel и sdist 0.24.0 собираются; отсутствовавший локальный hatchling установлен.
- Live continuity evidence: `%TEMP%/dca-continuity-live-4__0sxqz/data/diagnostics.sqlite3`.
  Запросы: `9016dbc76aa549878800898025b5de6d`, `ee40983d650b4a799f8c82f56d1f7d24`,
  `7d382228c4a54d45bbdf0cf7e8014394`, `b8295d43dc67455f82ea1a74fa3d27d9`.
  Первый прогон: `%TEMP%/dca-continuity-live-uesccal4`; подтверждён также реальный
  failover после timeout GLM к OpenAI. Ключи не выводились и не изменялись.
- Read-only live каталоги: GLM — 10 подходящих ID, OpenAI — 63 до удаления дублей;
  оба возвращают пять choices. Это не live-inference всех десяти моделей.

Границы подтверждения:

- Выполнение предназначено для локального однопользовательского приложения.
  После crash lease становится доступным по истечении task budget + 60 секунд;
  немедленного распределённого определения смерти owner нет.
- Sync HTTP нельзя считать подтверждённо остановленным на сервере после cancel;
  остановка проверяется на границах вызовов. Streaming repetition detector и
  семантический классификатор повторов не реализованы и не заявляются.
- Для GLM-5-Turbo в текущем каталоге дата сортировки — created, не подтверждённая
  дата релиза. Известные API-релизы дополнены официальными источниками:
  https://docs.z.ai/release-notes/new-released,
  https://openai.com/index/gpt-5-6/,
  https://openai.com/index/introducing-gpt-5-5/.
- В этом этапе не выполнялись ручной browser UX-прогон и inference каждой модели.
  Пользовательский Ozon workspace, текущий сервер и его задачи не перезапускались.
  Изменения локальные; публикация Git в текущем этапе не выполнялась.

## Structured data-aware routing 0.23.0 — 2026-09-03

- Добавлен immutable RoutingDecision с независимыми execution/workflow/scope,
  scan authority, mutation intent, confidence и safe reason codes.
- Direct instruction отделяется от fenced, quoted, tagged и узнаваемого
  terminal/traceback payload до классификации; полный query сохраняется для LLM
  как untrusted data.
- Non-project persistent workflow выполняется durable `execute` unit без
  ProjectAuditStore manifest. Autopilot schema мигрирована полем `workflow`.
- Web backend применяет scope к доступным tools, а write — только при прямом
  mutation intent и trusted allow-write. API/SSE/UI/JSONL отражают решение.
- Нормативный контракт: `DEEP_CONTEXT_AGENT_0_23_STRUCTURED_ROUTING_PROMPT.md`,
  TECHNICAL_SPEC 7.18 и Web-ТЗ 13.17.

Финальные evidence 0.23.0:

- Ruff check/format, mypy и compileall — PASS; pytest — 298 passed,
  1 planned Windows symlink skip; TypeScript production build и bundle test —
  PASS; `pip check` — PASS.
- Исходный PowerShell-log route стабилен в пяти повторах: `single-turn`,
  `log-analysis`, `scope=message`, `allow_project_scan=false`,
  `mutation_requested=false`. Tool-policy regression подтверждает отказ
  project-wide `glob` для message scope.
- Live Web task `65aa43be1f31468a89ea38a1010bf9ce` получил такое же routing decision,
  завершился обычным ответом через failover `zhipu/glm-5.3` →
  `openai/gpt-5.6-sol`; в `autopilot.sqlite3` и `project_audit.sqlite3`
  осталось 0 jobs/runs, safe JSONL не содержит body журнала.
- Отдельный live explicit-persistent task
  `f5b7d95ea50d4ff7b2253d7a1da75ece` создал durable job
  `d4319e8d877fc28560b66906` с `workflow=log-analysis`, `phase=execute`,
  `audit_run_id=NULL` и 0 audit runs. Недоступный в момент проверки
  `openai/gpt-5-nano` после четырёх bounded attempts перевёл job в безопасный
  persisted `blocked`, не расширив scope и не запустив аудит.
- Browser acceptance подтвердил версию 0.23.0, пять chat modes, model controls,
  `position: sticky; top: 0px`, независимый `overflow-y: auto` истории и
  отсутствие console errors.
- Собраны sdist и wheel 0.23.0. Wheel установлен в чистый target, импортировал
  `context_agent.routing`; размер 180633 bytes, SHA-256
  `C184A9B5EC5C1DF498141F531DC055063FDC8C5F195ED3EAFD5B80565471930A`.

Этот файл связывает этапы `IMPLEMENTATION_PROMPT.md` с требованиями
`TECHNICAL_SPEC.md`.

## Dynamic models, hybrid retrieval and bounded scans 0.22.0 — 2026-09-03

- Нормативный промпт создан:
  `DEEP_CONTEXT_AGENT_0_22_HYBRID_RETRIEVAL_MODEL_UI_PROMPT.md`.
- Требования внесены в `IMPLEMENTATION_PROMPT.md`, `TECHNICAL_SPEC.md` и
  `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`.
- Реализованы sticky per-thread provider/model selection и пять presets;
  server-validated bounded catalog, immutable task snapshot и фактический
  provider/model/fallback в terminal evidence.
- Реализованы локальные FastEmbed/ONNX CPU + Qdrant, hybrid RRF с FTS5/BM25,
  SHA-256 incremental sync, lazy load, RAM-aware batch и lexical-only fallback.
- Один artifact policy используется context index, audit, glob и grep;
  широкие операции возвращают bounded pages, scope-bound cursor,
  partial/resume и явные indexed/unchanged/skipped counts.
- Terminal Web event сохраняет duration, provider/model/fallback, file counts,
  partial и cursor presence; Windows shutdown закрывает SQLite, workers и
  rotating JSONL handler.

Финальные evidence 0.22.0:

- Ruff check/format, mypy и compileall — PASS; pytest — 281 passed,
  1 planned Windows symlink skip; TypeScript и production bundle — PASS.
- Editable install и `pip check` — PASS; собраны wheel и sdist 0.22.0.
  Wheel: 173851 bytes, SHA-256
  `c2e3bb33765e1be671e96fabb7631efefe1c230ce03d52574ffc8cead7a77f9a`.
- Реальный FastEmbed/Qdrant CPU: русский semantic paraphrase и точный Python
  symbol найдены; forced invalid model дал рабочий `lexical-only`.
- Web live: `zhipu/glm-5.3` корректно перешёл на
  `openai/gpt-5.6-sol` после rate limit; следующий turn выбрал OpenAI напрямую,
  preference сохранён, JSONL handler освободил файл при shutdown.
- Browser live: sticky header `top=0`, три provider, model catalog и пять
  presets видимы, console errors отсутствуют.
- Ozon read-only scan: 112 уникальных файлов за 3 страницы; grep resume по 25
  results без дубликатов. Synthetic 1 000 000 lines/200 docs: 5 pages,
  6.051 s index, 1.285 ms two searches, 1.27 MiB Python peak, anchors PASS.

## Five chat modes 0.21.0 — 2026-09-02

| Этап 0.21.0 | Статус | Реализация/проверка |
|---|---|---|
| Mode contract | Завершён | Только Agent/Ask/Plan/Debug/Multitask в Pydantic, runtime metadata, HTML и TypeScript; 11 legacy values получают 422 |
| Server policy | Завершён | Ask/Plan forced read-only, Ask/Plan/Debug forced single-turn, trusted mutation middleware не зависит от JavaScript |
| Multitask | Завершён | До четырёх concurrent Web tasks, отдельный child thread/SSE/pending bubble, cancel-all и worktree warning |
| Context UX | Завершён | Aggregate SQLite usage API, accessible circular estimate и built-in Deep Agents summarization explanation |
| Browser/live | Завершён | Production bundle открыт на loopback; все mode defaults и meter проверены без console errors; настоящий GLM Ask-turn PASS |
| Quality/package | Завершён | Ruff/format/mypy/pytest/TypeScript/bundle/compile/package/secret contour выполнен перед release |

Финальные evidence 0.21.0:

- pytest — 268 passed, 1 planned Windows symlink skip; Ruff check/format и
  mypy — PASS; production JS+CSS bundle — 45 699 bytes;
- Browser production DOM содержит mode values `agent`, `ask`, `plan`, `debug`,
  `multitask` и ни одной прежней карточки; Ask/Plan disabled+unchecked write,
  Debug/Agent trusted-write UI, Multitask safe write default подтверждены;
- context meter изменился с 0 до 0.5% на 1 600 символах, tooltip сообщает об
  automatic summary и SQLite archive, browser console errors отсутствуют;
- реальный Web task `3ba4697d8ed34a49b9a7d8b751179751` через
  `zhipu/glm-5.3` принял raw `allow_write=true` + `execution_mode=autopilot`, но
  исполнился как Ask `single-turn`, `allow_write=false`, вернул контрольную
  строку и terminal completed; отдельный `doctor --live` — `OK`.
- wheel `deep_context_agent-0.21.0-py3-none-any.whl` содержит 25 файлов,
  запрещённые SQLite/JSONL/env artifacts отсутствуют, изолированный target
  вернул 0.21.0; SHA-256
  `72AD3F5F39947858196121E517A203D73C16F2016F10581A144118BD8C218755`.

## Durable lease orchestration 0.20.0 — 2026-09-02

| Этап 0.20.0 | Статус | Реализация/проверка |
|---|---|---|
| Schema/migration | Завершён | Job/unit generation, heartbeat, deadline и interrupted с idempotent migration существующей SQLite |
| Fencing | Завершён | Все owner transitions проверяют token+generation; mutation guard закрывается при lease loss |
| Long unit | Завершён | Audit/verify/repair heartbeat действует во время model/check call, batch ≤2, recursion 40, soft deadline 900 s |
| Recovery | Завершён | Expired running unit становится interrupted; job paused, resume создаёт новую generation/thread |
| Web progress | Завершён | Persisted heartbeat/deadline/generation/interrupted доступны job DTO и SSE |
| Tests/live | Завершён | Migration, stale owner, expiry, delayed unit, clean Web job и real provider tests PASS |

Финальные evidence 0.20.0:

- production SQLite миграция сохранила две старые jobs как
  paused/interrupted и две work units как interrupted без удаления истории;
- реальная unit дольше 30-second test lease завершилась без premature takeover;
  clean repeat task `1ad47374e6c54c129218dc557cd11bdd` получил 58 heartbeat
  events, terminal complete и generation 1;
- `zhipu/glm-5.3` с fallback `openai/gpt-5.6-sol` прошёл live doctor.

## Chat-integrated Autopilot 0.19.1 — 2026-09-01

| Этап 0.19.1 | Статус | Реализация/проверка |
|---|---|---|
| Root cause | Завершён | Production query не содержал прежний action verb; `/api/chat` выбрал ordinary turn и получил `agent_step_limit` |
| Backend routing | Завершён | explicit auto/autopilot/single-turn, расширенный classifier, automatic step/context fallback |
| Chat UX | Завершён | Отдельная вкладка удалена; execution selector, inline SSE progress и job history находятся в чате |
| Persistence/security | Завершён | Parent-thread archive, user/human roles, trusted allow-write и существующая SQLite job identity сохранены |
| Tests/package/live | Завершён | Полный Python/TypeScript/package-контур и два chat live-прогона PASS |

Финальные evidence 0.19.1:

- диагностика task `ee02592d8eb643a4854ea3bb72ec302f`: kind `chat`, status
  `failed`, error `agent_step_limit`; исходная body-фраза не прошла classifier;
- targeted backend regressions для точной проблемной фразы, explicit short
  Autopilot, automatic step-limit fallback и single-turn negative — PASS;
- TypeScript check, production build и bundle regression без отдельной вкладки
  Autopilot — PASS, bundle 39 223 bytes;
- Ruff check/format, mypy, compileall и `pip check` — PASS; pytest — 245 passed,
  1 planned Windows symlink skip;
- реальный `/api/chat` с точной проблемной фразой получил task
  `695815966ff24695837c9b0026043455`, kind `chat_autopilot`, события
  execution + 4 job_progress, terminal `complete`; job
  `3ab2fa8c8c0360922c856d88` завершена, parent thread содержит user/assistant;
- diagnostics подтверждает 5 успешных model attempts через `zhipu`; отдельный
  `doctor --live` для `glm-5.3` с fallback `gpt-5.6-sol` вернул `OK`;
- повтор той же chat job после завершения вернул execution/job_progress и
  `complete` за 79 ms без нового полного прохода; production HTML показывает
  chat execution selector и не содержит `data-panel="audits"`/`audit-form`;
- wheel 0.19.1 содержит 25 файлов, запрещённые SQLite/JSONL/env artifacts
  отсутствуют, изолированная target-install вернула 0.19.1; SHA-256
  `CC9F5CF0B3942FA9A71DDC3BFCDE7866A6A792AEEBF83E214EA8964E3E95AD15`.

## Persistent Autopilot 0.19.0 — 2026-08-31

| Этап 0.19.0 | Статус | Реализация/проверка |
|---|---|---|
| Root cause/context | Завершён | Graph recursion отделён от million-line retrieval; применены durable execution/context isolation patterns LangGraph/Deep Agents |
| Durable job state | Завершён | `autopilot.sqlite3`, stable identity, lease, work units, pause/cancel/resume, WAL/migration |
| Adaptive execution | Завершён | One batch/unit, new worker thread, split `2 → 1`, transient retry, bounded blocked states |
| Verification/repair | Завершён | Fixed allowlisted checks, safe output, bounded repair/recheck, current PASS gate |
| CLI/Web | Завершён | `job`, `job-status`, `/api/jobs`, SSE progress/replan/verification, no batch field, complex Web-chat routing |
| Security | Завершён | Trusted allow-write identity, workspace confinement, secret-free subprocess, physical path hidden in Web DTO |
| Tests/package/live | Завершён | Полный Python/TypeScript/package-контур, реальный GLM job, restart/resume и Web API smoke PASS |

Финальные evidence 0.19.0:

- Ruff check/format, mypy, compileall и `pip check` — PASS; pytest — 242 passed,
  1 planned Windows symlink skip; проверено 50 Python-файлов и 16 mypy source
  files;
- deterministic regression воспроизводит `GraphRecursionError`: batch
  автоматически уменьшается `2 → 1`, два новых worker threads завершают
  manifest без ручного продолжения;
- TypeScript check, bundle regression и esbuild — PASS; production bundle
  38 449 bytes;
- `doctor --live` на `zhipu/glm-5.3` с резервным
  `openai/gpt-5.6-sol` — `live_response=OK`;
- первый реальный read-only job обработал три независимых файла и обнаружил
  collision job/audit ID и physical path в отчёте; после исправления повторный
  clean-DB job обработал 2/2 файла, получил разные job/audit IDs, завершился в
  новом процессе со status `complete` и не раскрыл physical workspace;
- Web API smoke на `127.0.0.1:8768`: health/runtime/index/jobs/details/report —
  HTTP 200, версия 0.19.0, форма «Автопилот» присутствует, сохранённый job
  доступен после restart, physical path отсутствует в DTO и отчёте;
- wheel 0.19.0 содержит 25 файлов, запрещённые SQLite/JSONL/env artifacts
  отсутствуют, изолированная target-install вернула 0.19.0; SHA-256
  `10689C5EB6E895B2C280E5277ADBF0790749518F3D368701DF5C691C22E9140B`.

## Durable failure journal 0.18.0 — 2026-08-30

| Этап 0.18.0 | Статус | Реализация/проверка |
|---|---|---|
| Durable SQLite | Завершён | `diagnostics.sqlite3`, WAL, schema v2 migration, request/provider/task records |
| Privacy/retention | Завершён | off/metadata/redacted/full, SHA-256, truncation, deterministic redaction, bounded cleanup |
| Runtime rollback | Завершён | Provider/tool evidence, rollback counts, filesystem-side-effect flag, dual-failure chain |
| Web/CLI | Завершён | list/show/export/purge, safe DTO, correlation IDs, terminal SSE replay после restart |
| Process log | Завершён | Rotating JSONL, safe fields, Windows benign reset policy сохранена |
| Physical deletion | Завершён | Checkpoint `secure_delete=ON` и WAL truncate после rollback исключают deleted-prompt residue |
| Финальный контур | Завершён | Python/TS/package, GLM/OpenAI live, failover, controlled failure, restart/redaction PASS |

Финальные evidence 0.18.0:

- Ruff check/format, mypy и compileall — PASS; pytest — 234 passed, 1 planned
  Windows symlink skip до финальной packaging-проверки;
- TypeScript `tsc --noEmit`, bundle regression и esbuild — PASS, bundle 38 012
  bytes;
- `zhipu/glm-5.3` и `openai/gpt-5.6-sol` doctor live — `OK`; реальный
  failover `lmstudio(unavailable) → zhipu` вернул `LIVE_FALLBACK_84217` и
  записал `fallback_triggered/fallback_success`;
- controlled Web failure сохранил request `25f31e45c6ec4f93aff42c0b4ffe5889`,
  `rollback_success=true`; после restart status/SSE replay вернулся за 85 ms;
- fake credential отсутствует в diagnostics/checkpoints/context/JSONL/export,
  failed conversation history пуста, redacted marker и полный SHA-256 сохранены.
- wheel 0.18.0 содержит 24 файла, runtime SQLite/JSONL отсутствуют, target-install
  вернул 0.18.0; SHA-256
  `7AA2F146B68374C84D71DBAD7DDD91875624DD454985955F897C496AC177F319`.

## Active-context recovery 0.17.0 — 2026-08-30

| Этап 0.17.0 | Статус | Реализация/проверка |
|---|---|---|
| Root cause | Завершён | Thread `web`: 174 messages, 452 525 chars, 75 ToolMessage; terminal detail не сохранялся после SSE |
| Runtime compaction | Завершён | Transient copy, threshold 80k tokens, latest 8 tool results, original checkpoint/archive preserved |
| Web recovery | Завершён | Safe error codes, terminal status/replay, client reconnect reconciliation |
| Windows logging | Завершён | Только exact Proactor `_call_connection_lost` + WinError 10054 считается benign |
| Документация/version | Завершён | Release/global/system prompts, main/Web ТЗ, env/README/changelog, 0.17.0 |
| Финальный контур | Завершён | Проверен совместно с полным контуром release 0.18.0 |

Промежуточные evidence 0.17.0:

- реальный checkpoint: 452 525 → 154 874 model-input chars, очищено 67 старых
  tool bodies, original cleared count = 0;
- targeted Python tests и mypy — PASS; TypeScript/build/bundle — PASS.

## Bounded stale-edit recovery 0.16.0 — 2026-08-29

| Этап 0.16.0 | Статус | Реализация/проверка |
|---|---|---|
| Root cause | Завершён | Exact edit conflict был тупиком после two-read limit; recovery state отсутствовал |
| Runtime state machine | Завершён | One path/version recovery read, forced fresh read, one revised edit, second-conflict stop |
| Context-tool hardening | Завершён | `/workspace`/bare source denied, invalid radius safe, hard eight-call budget |
| Security | Завершён | No raw old_string/content/physical path; exact edit and all prior path/write policies preserved |
| Документация/version | Завершён | Release prompt, global prompt/ТЗ, Web-ТЗ, system prompt, README/changelog, 0.16.0 |
| Финальный контур | Завершён | Python/TS/package, LM Studio+GLM doctor, real GLM stale-edit live и Web smoke PASS |

Финальные evidence 0.16.0:

- `ruff check` и `ruff format --check` — PASS, 42 файла; mypy —
  13 source files без ошибок; pytest — 197 passed, 1 planned Windows
  symlink skip; compileall, pip check и `git diff --check` — PASS;
- TypeScript `tsc --noEmit`, esbuild и bundle test — PASS, JS+CSS
  37 370 bytes;
- deterministic external-mutation sequence и negative state-machine tests —
  PASS; LM Studio `qwen2.5-7b-instruct` и Zhipu `glm-5.3` doctor live — `OK`;
- чистый GLM-5.3 live audit: `read success, read success, edit error,
  read success, edit success, read success`; независимое exact-file
  assertion — `RECOVERED_LINE_51602`, PASS;
- Web smoke `127.0.0.1:8766`: `/api/health=ok`, runtime version `0.16.0`,
  provider `zhipu`; TypeScript bundle — PASS;
- wheel 0.16.0 содержит 22 файла, static bundle и system prompt,
  target-install вернул 0.16.0; SHA-256
  `D06294936A5CB11E9CBAD7726AE47A212683387CF27A86CD39A80AFCD5A3D503`.

## Provider/files hardening 0.15.0 — 2026-08-29

| Этап 0.15.0 | Статус | Реализация/проверка |
|---|---|---|
| LM Studio diagnostics | Завершён | Bounded `/models`, safe errors, `local-model` auto-selection, catalog update |
| Local/remote payment UX | Завершён | Loopback без confirm и с явным free label; remote с opt-in warning |
| Custom providers | Завершён | `custom-*` UI/API, immediate activation, HTTPS/loopback validation, server-only key |
| Files navigation | Завершён | Bounded history Back, parent Up, loading/success/error и object count |
| Error UX | Завершён | Expected AgentError в SSE даёт safe provider guidance без raw exception |
| Документация/version | Завершён | Release prompt, global prompt/ТЗ, Web-ТЗ, system prompt, README/env/changelog, 0.15.0 |
| Финальный контур | Завершён | Python/TS/package, real LM Studio/custom live, desktop/mobile browser acceptance PASS |

Финальные evidence 0.15.0:

- `ruff check` и `ruff format --check` — PASS, 41 файл; mypy —
  13 source files без ошибок; pytest — 193 passed, 1 planned Windows
  symlink skip; compileall и pip check — PASS;
- TypeScript `tsc --noEmit`, esbuild и bundle test — PASS, JS+CSS 37 370 bytes;
- реальный LM Studio `/models` вернул 7 models; Web doctor выбрал
  `qwen2.5-7b-instruct` вместо placeholder и получил `OK`;
- custom local provider создан, помещён первым в active chain и
  получил `OK` без сетевой платы;
- browser E2E: LM Studio no payment dialog, custom provider form, file Open/
  Back/Up/editor, mobile 390x844 и zero console warnings/errors — PASS;
- wheel 0.15.0 содержит 22 файла и static bundle, SHA-256
  `55608F53674F7EDC1D213C975911F42E04A66779771240759F5974D73076A2E2`.

## Единый Web API и Codex-like UX 0.14.0 — 2026-08-29

| Этап 0.14.0 | Статус | Реализация/проверка |
|---|---|---|
| Shared API boundary | Завершён | Web использует общие runtime, context/audit SQLite, provider failover и path policy |
| Chat UX | Завершён | Tasks/history, mode selector, bottom composer, stop, optional Enter send, Shift+Enter newline |
| Context/files | Завершён | Исправлен `/workspace`, index lifecycle/counters, directory navigation, bounded editor + SHA |
| Audit/settings labels | Завершён | Bilingual fields и русские help/comments вместо raw JSON |
| Live providers | Завершён | Thread-safe atomic priority, add/remove configured providers, per-provider live check |
| Responsive/accessibility | Завершён | Desktop sidebar, mobile bottom nav, keyboard/focus/aria-live, 390 px browser acceptance |
| Документация/version | Завершён | Web prompt, Web-ТЗ, global prompt/ТЗ, README, changelog, 0.14.0 |
| Финальный контур | Завершён | Ruff/format, mypy, 184 passed + 1 planned skip, compileall, TS build, wheel, pip check и browser live acceptance PASS |

Финальные evidence 0.14.0:

- `ruff check` и `ruff format --check` — PASS, 40 файлов formatted; mypy —
  24 source files без ошибок; pytest — 184 passed, 1 planned Windows symlink skip;
- `compileall` и `pip check` — PASS;
- TypeScript `tsc --noEmit`, esbuild и bundle test — PASS, JS+CSS 35 174 bytes;
- реальный browser E2E: GLM-5.3 chat ответил `ГОТОВ`, Enter/Shift+Enter,
  context index, file preview, provider reorder/restore и mobile 390x844 — PASS;
- console errors/warnings после основных flows — отсутствуют;
- wheel 0.14.0 содержит 22 файла и static bundle, SHA-256
  `F1AF91D1FB0B9CF97964F52D0951CB8278393C1919B5BB1B224E9B71B5F4A4E2`.

## Production audit and Web UI 0.13.0 — 2026-08-27

| Этап 0.13.0 | Статус | Реализация/проверка |
|---|---|---|
| Baseline | Завершён | Ruff lint/format, mypy, 168 passed + 1 planned symlink skip до изменений |
| Explicit audit mode | Завершён | Read-only default; только `--allow-write`; mode в identity/SQLite/status/report; regression против inference по `fix` |
| File selection | Завершён | Built-in generated/dependency/report filters, env include/exclude, selected/excluded/reasons |
| Requirements/findings | Завершён | Устойчивые REQ IDs, bounded batch subset, candidate evidence matrix, validated/deduplicated findings |
| Output/resume | Завершён | Compact console, direct UTF-8 text/JSON, flush progress, model-free status, pause/cancel/resume |
| Web backend/UI | Завершён | FastAPI/Uvicorn extra, local static UI, REST/SSE, shared runtime/SQLite, files/providers/settings |
| Web security | Завершён | Same-origin, CSRF, CSP, remote bearer gate, path/secret guards, optimistic hash, delete off |
| Large corpus | Завершён | Автотест реального файла 1 000 000 строк и 500 отдельных документов |
| Документация/version | Завершён | Global prompt, implementation prompt, ТЗ, Web-ТЗ, README, env, changelog, 0.13.0 |
| Финальный контур | Завершён | Ruff check/format, mypy, compileall, 181 passed + 1 planned symlink skip; wheel/static, CLI/doctor, HTTP Web smoke и GLM-5.3 live audit OK |

Финальные evidence 0.13.0:

- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 40
  файлов уже отформатированы; `mypy src/context_agent` — PASS для 13 modules;
- `pytest -ra` — 181 passed, 1 planned Windows symlink skip;
- `compileall`, `pip check` — PASS; wheel 0.13.0 содержит 22 файла и локальный
  static bundle, SHA-256
  `D0573D286BE9C458B6FF1ACF3CCAC0F6E4C166DA096A507A56F09DC808594B6E`;
- TypeScript `tsc --noEmit`, reproducible esbuild и bundle test — PASS,
  production JS+CSS 11 690 bytes;
- реальный Uvicorn HTTP smoke на чистых workspace/data — health, version,
  static page и CSP PASS;
- `doctor --live` выбрал primary `zhipu/glm-5.3` и получил `OK`;
- clean-DB live audit `aba479946ef46a305c72fa7c` обработал 2/2 файла,
  `file_reads=2`, `partial=0`, `mode=read-only`, сохранил UTF-8 text/JSON и не
  изменил fixture.

## Large-project audit orchestration 0.12.0 — 2026-08-25

- Wide project requests route to a persistent SQLite manifest and independent
  batches instead of one ever-growing graph turn.
- Manifest completion is evidence-backed; unread files remain pending and a
  changed SHA-256 reopens only the changed file.
- Unique reviewed-file counts are separate from actual paginated `file_reads`;
  the final report cannot hide a second page call as one read.
- Per-file page exhaustion is represented by `partial` and
  `complete_with_partial`; uncovered lines are never reported as reviewed.
- Cached summaries and a Python AST index provide a compact project map without
  importing target code or placing the corpus in an LLM prompt.
- `run_project_checks` exposes only a fixed no-shell allowlist with timeout,
  bounded/redacted output and secret-free child environment.
- Operational limits are configurable and validated; `recursion_limit` is no
  longer hardcoded in the graph call.

| Этап 0.12.0 | Статус | Проверка | Результат |
| --- | --- | --- | --- |
| Prompt/ТЗ/документация | Завершён | System prompt, `IMPLEMENTATION_PROMPT.md`, `TECHNICAL_SPEC.md`, README, env, changelog | Two-level large-context model and batch rules synchronized |
| Manifest/SHA/resume | Завершён | Unit: partial batch, restart, changed file | Only verified/current files become reviewed |
| Summary/AST | Завершён | Unit: cache reuse and qualified nested symbol | No import/execution of target code |
| Safe checks | Завершён | Unit: injection denial, `shell=False`, env redaction, repeat after mutation | Fixed allowlist only; bounded cycle |
| Scale | Завершён | Existing 1,000,001-line FTS5 test plus 300-file manifest | Beginning/end searchable; corpus never placed in one prompt |
| Финальный контур | Завершён | Ruff check/format, mypy `src tests`, compileall, wheel, CLI/doctor/live | 168 passed, 1 Windows symlink skip; wheel 0.12.0; GLM-5.3 doctor/agent/batch live OK |

Live evidence на чистых временных workspace/data/thread:

- `doctor --live`: `zhipu/glm-5.3`, `live_response=OK`;
- полный `AgentRuntime.ask`: точный ответ `DEEP_CONTEXT_012_OK`;
- batch audit: 2/2 файла, `partial=0`, `file_reads=4`, README 368/368
  строк, UTF-8 stdout без `charmap` exception;
- первый live-прогон обнаружил Windows stdout codec и page-budget дефекты;
  исправления выполнены и подтверждены повторными clean-DB live-прогонами.

## Default GLM/OpenAI chain 0.11.0 — 2026-08-24

- Основная модель по умолчанию: `zhipu/glm-5.3`.
- Резервная модель по умолчанию: `openai/gpt-5.6-sol`.
- Порядок failover без CLI/env override: `glm,openai`.
- Default Z.AI endpoint: `https://api.z.ai/api/paas/v4`.
- GPT-5.6 Sol Chat Completions tool calls используют `reasoning_effort=none`.
- Локальная `.env.local` синхронизирована без раскрытия ключей.

| Этап | Статус | Проверка | Примечание |
| --- | --- | --- | --- |
| 1. ТЗ и промпты | Завершён | `TECHNICAL_SPEC.md`, `IMPLEMENTATION_PROMPT.md`, `EVIDENCE_INTEGRITY_PROMPT.md` | Глобальный prompt и критерии приёмки синхронизированы с дефектами ручных и live-логов |
| 2. Каркас и провайдеры | Завершён | Unit-тесты конфигурации и фабрики | LM Studio, OpenAI, YandexGPT, DeepSeek и Qwen; provider retry выключен, retry выполняет middleware model call |
| 3. Большой контекст | Завершён | 1 000 001 строка, 200 документов, повторное открытие SQLite | Потоковый FTS5, BM25, соседние чанки и cross-thread archive без загрузки корпуса в окно LLM |
| 4. Deep Agent и CLI | Завершён | Fake-model tool loops, CLI unit/smoke, OpenAI live | `/paste`, `ask --file`, stdin, bounded input, `--no-auto-context` и доверенная runtime identity |
| 5. Безопасность файлов | Завершён | Root-delete, внешний путь, exact read, незавершённая команда | Нет shell/общего `delete`; запрещён root, placeholder и угадывание отсутствующего содержимого |
| 6. Надёжность | Завершён | Retry, точный rollback checkpoints/writes, web timeout | Model-call retry не дублирует tool; failed turn не попадает в следующий запрос; web error структурирован |
| 7. Доказательства | Завершён | Audit runtime/context/files/web и guards | Содержимое/секреты не копируются; текущий веб-факт требует успешного fetch; self-PASS не является приёмкой |
| 8. Финальная проверка 0.2.0 | История | Ruff, 61 passed, 1 planned skip, CLI help, `doctor --live` | Результат предыдущего production-hardening сохранён для трассировки |
| 9. Acceptance reliability 0.3.0 | Завершён | Последовательность, duplicate guard, redaction, PyPI, recursive cleanup | Каждый дефект последнего ручного лога закреплён кодом и регрессионным тестом |
| 10. Финальная проверка 0.3.0 | Завершён | Ruff, 72 passed, 1 planned skip, CLI help, `doctor --live` | Изолированный OpenAI `gpt-5-nano` smoke: один `runtime_info`, точные provider/model, ключ не выведен |
| 11. Acceptance completion 0.4.0 | Завершён | Repeat policy, forbidden read, manifest parser/evaluator | Exact counts, ordered/forbidden events и negative statuses проверяются независимо от LLM |
| 12. Финальная проверка 0.4.0 | Завершён | Ruff, 81 passed, 1 planned skip, package/CLI/doctor/live | OpenAI `gpt-5-nano`: один `runtime_info`, runtime manifest — 2 PASS, 0 FAIL |
| 13. Acceptance correctness 0.5.0 | Завершён | Sentence scope, allowed-unlisted, BLOCKED, completion gate | Runtime завершает только dependency-ready cleanup/postconditions и не расширяет filesystem scope |
| 14. Финальная проверка 0.5.0 | Завершён | Ruff, 91 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: полный manifest — 32 PASS; новый процесс/thread — 2 PASS и 4 context results |
| 15. Evidence integrity 0.6.0 | Завершён | Structured audit, manifest v2, cardinality/exact-once guards | Hash/count predicates независимы от LLM; устаревший exact-once повтор не исполняется |
| 16. Финальная проверка 0.6.0 | Завершён | Ruff, 97 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: полный v2 manifest — 32 PASS; restart — ровно один call, 4 results, 2 PASS |
| 17. Ozon hardening 0.7.0 | Завершён | Root-scope, index-filter и listing regression tests | Общий root не блокирует child-read; generated/browser/cache пути отсечены; listing ограничен 20/50 |
| 18. Финальная проверка 0.7.0 | Завершён | Ruff, 100 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: root-scope read успешен; полный manifest — 32 PASS; restart — 4 results и 2 PASS |
| 19. BOM text patch 0.7.1 | Завершён | Ruff, 102 passed, package/pip/doctor live, UTF-16/UTF-32 regression | Обнаруженный Ozon UTF-16LE отчёт теперь индексируется; clean-DB повтор выполняется после публикации |
| 20. Explicit tool budgets 0.8.0 | Завершён | Ruff, 105 passed, package/pip/doctor live, parser/tool-loop | Per-tool и total budget скрывают exhausted tools; stale provider-call не исполняется и не аудируется |
| 21. Empty toolset patch 0.8.1 | Завершён | OpenAI 400 regression, Ruff, 106 passed, package/pip/doctor live | Пустой toolset не отправляет tool-only model settings; повтор Ozon выполняется после публикации |
| 22. Middleware order patch 0.8.2 | Завершён | Ruff, 107 passed, package/pip/doctor live, композиционный regression | Sequential normalizer видит окончательный toolset; чистый Ozon-run выполняется после публикации |
| 23. Multiline budget patch 0.8.3 | Завершён | Ozon prompt regression, Ruff, 107 passed, package/pip/doctor live | Общий limit 15 доказан live; per-tool 2 распознаётся через перенос строки |

Финальная проверка выполнялась командами из README. Конфликт ACL между обычным
Windows-пользователем и Codex sandbox устранён отказом от общих pytest/Ruff-
кэшей. Платные провайдеры без настроенных ключей не вызывались; их конфигурация
и маршрутизация покрыты автоматическими тестами без отправки секретов в вывод.

Production-hardening от 2026-08-22 проверен в изолированных
`AGENT_WORKSPACE`/`AGENT_DATA_DIR`:

- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 22 файла отформатированы;
- `pytest -ra` — 61 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- OpenAI doctor — `model=gpt-5-nano`, `live_response=OK`;
- один многострочный stdin-запрос дошёл до live runtime одной транзакцией;
  `runtime_info` вернул `provider=openai`, `model=gpt-5-nano`, а audit подтвердил
  один успешный tool call без вывода ключа;
- незавершённая команда была отклонена до LLM/tool, файл не создан;
- fake model упал один раз и повторился на model-call уровне; файловый tool
  выполнился ровно один раз;
- после окончательной model error checkpoints/writes совпали с точным снимком
  до хода, а следующий запрос не содержал failed marker;
- если tool успел изменить файл до более поздней model error, исключение содержит
  безопасный audit фактического изменения и предупреждает, что side effect не
  был отменён;
- timeout web-search дал структурированный `error`, и runtime пометил старое
  утверждение об актуальной версии как `FAIL` без успешного page fetch;
- запрос удаления `/workspace/` не выполнил mutating tool, контрольный файл
  `ROOT_DELETE_MUST_KEEP_58317` остался на месте;
- точное чтение вернуло только `EXACT_REQUESTED_CONTENT_27419`;
- запрос `C:\outside-live-48271.txt` был отклонён до LLM, substitute отсутствует;
- фраза `СЕВЕРНЫЙ КВАРЦ 73184` найдена после нового запуска в другом thread ID,
  ответ правильно описал постоянный SQLite-архив.

Результат 0.2.0 выше сохранён как история версии. Проверка 0.3.0 от 2026-08-22
выполнена после устранения недостатков последнего acceptance-лога:

- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 25 файлов отформатированы;
- `pytest -ra` — 72 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- CLI `--help` и безопасный OpenAI `doctor` — успешно, ключ показан только как
  `configured`;
- реальный официальный PyPI JSON probe вернул `langchain=1.3.16` с
  `https://pypi.org/pypi/langchain/json`;
- изолированный live-run использовал
  `v030-844fe480ca63498ba3dbfafd17d1c40d`, новый workspace/data/thread,
  `gpt-5-nano`; `doctor --live` вернул `OK`, а agent audit — ровно один
  `runtime_info: success`;
- fake-model integration доказал, что из двух параллельных calls исполняется
  только первый, а на каждый bind передаётся `parallel_tool_calls=false`;
- повторная идентичная мутация в одном ходе получает `denied`, но ledger
  сбрасывается перед следующим пользовательским ходом;
- `DECOY_SECRET_DO_NOT_SHOW_71935` остаётся точным содержимым тестового файла,
  но заменяется на `[REDACTED]` в ответе и audit;
- private URL `http://127.0.0.1:8000/private` даёт фактический текущий
  `fetch_web_page: error` без сетевого доступа к локальному сервису;
- явно названный непустой подкаталог удаляется одним
  `remove_path [recursive=true]`, а root-delete по-прежнему сохраняет sentinel;
- `CANDIDATE_RESULT` и общий PASS/FAIL получают программный внешний вердикт
  `FAIL` либо `NOT_VERIFIED` и не используются как источник готовности.

Версия 0.3.0 считается production-ready для заявленного локального
однопользовательского CLI. Многопользовательский сетевой сервис требует
отдельной процессной песочницы, аутентификации и координации одновременных
записей; это не входит в область данного ТЗ.

Результат 0.3.0 выше сохранён как история. Проверка 0.4.0 от 2026-08-23
выполнена после анализа полного ручного audit из 26 tool calls:

- повторные идентичные runtime/context/listing/web-вызовы блокируются до
  повторного исполнения и получают `denied` в текущем audit;
- третье чтение неизменённого пути блокируется; успешная мутация самого пути
  или recursive removal родителя открывает новую проверяемую версию;
- basename из явного «не читай/не открывай/не показывай» сопоставляется только с
  явно указанным полным workspace-путём и запрещает `read_file` для decoy;
- строгий bounded manifest parser отклоняет неизвестные поля/tools/status,
  неверные зависимости и превышение размера;
- manifest evaluator сверяет все attempts, точные количества, ordered required
  events, forbidden calls и ожидаемые `denied/error/not_found`; один audit event
  не используется дважды, а необъявленный tool является FAIL;
- канонический `acceptance-prompt.txt` содержит 21 ordered event, один forbidden
  decoy read, точные количества для 11 tool types и одну restart-проверку;
- contract-тест канонического manifest подтверждает предусмотренную полную
  последовательность cleanup и post-delete reads без выполнения платного API;
- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 27 файлов отформатированы;
- `pytest -ra` — 81 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable wheel `deep-context-agent==0.4.0` собран и установлен, `pip check` не
  обнаружил конфликтов, CLI `--help` и безопасный OpenAI doctor прошли;
- изолированный live-smoke использовал
  `v040-ff3825cf02f8410bb7c7530eeb087bf2`, новый workspace/data/thread и
  `gpt-5-nano`; audit зафиксировал ровно `runtime_info: success`, а runtime
  manifest — `2 PASS, 0 FAIL, 0 PENDING` без вывода API-ключа.

Версия 0.4.0 считается production-ready для заявленного локального
однопользовательского CLI. Полный канонический live acceptance остаётся
отдельным операторским прогоном из-за числа платных model calls; его лог теперь
оценивается runtime автоматически. Многопользовательский сетевой сервис по-
прежнему требует отдельной процессной изоляции, аутентификации и координации
записей и не входит в область данного ТЗ.

Результат 0.4.0 выше сохранён как история. Проверка 0.5.0 от 2026-08-23
выполнена после итеративных диагностических live-прогонов и финального чистого
прогона:

- отрицательная инструкция ограничена своим предложением: `decoy.txt` остаётся
  запрещённым, а положительно указанный `result.txt` читается;
- `write_todos` разрешается только как явно объявленный unlisted planning-tool,
  а функциональные tools сохраняют exact counts;
- первичный missing event получает FAIL, зависимые события — BLOCKED;
- bounded completion middleware продолжает только доказанную ordered
  cleanup/postcondition цепочку, не начинает root event, не читает запрещённый
  путь, не выходит из `/workspace/` и не превышает exact count;
- после root event provider получает имя следующего prose-разрешённого tool, а
  dependency-ready `read_file`/`remove_path` — точный ordered target; JSON
  manifest без независимого prose-разрешения не инициирует действие;
- машинный manifest исключён из классификации текущих web-фактов, поэтому его
  поле `version` не создаёт ложный web FAIL для локального context search;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 91 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.5.0`, `pip check`, CLI `--help` и
  безопасный OpenAI doctor — успешно;
- полный изолированный live acceptance использовал run
  `20260823-125545`, `gpt-5-nano`, `AGENT_MODEL_TEMPERATURE=0` и новые
  workspace/data/thread; runtime manifest дал `32 PASS, 0 FAIL, 0 BLOCKED,
  1 PENDING`, а exact counts совпали для всех десяти функциональных tool types;
- физическая проверка после live-run подтвердила отсутствие тестового каталога,
  root sentinel и outside placeholder при сохранённом prompt-файле;
- новый процесс с тем же `AGENT_DATA_DIR` и thread
  `restart-v050-release-20260823-125545` вызвал `search_context` ровно один раз,
  получил `4 result(s)` и runtime verdict `2 PASS, 0 FAIL, 0 BLOCKED`; restart
  PENDING тем самым закрыт внешней проверкой.

Версия 0.5.0 считается production-ready в границах локального
однопользовательского CLI. Многопользовательский сетевой сервис требует
отдельной процессной изоляции, аутентификации и координации одновременных
записей и не входит в область данного ТЗ.

Результат 0.5.0 выше сохранён как история. Проверка 0.6.0 от 2026-08-23
выполнена после устранения трёх дефектов restart-аудита:

- `ToolAuditEntry` содержит безопасные `result_count` и `content_sha256`;
  SHA-256 вычисляется из фактических байтов workspace-файла и не раскрывает
  его тело;
- manifest v2 строго валидирует `min_results` и `content_sha256`, сохраняет
  совместимость v1 и запрещает evidence-предикаты в forbidden events;
- прямой вопрос о количестве получает детерминированный ответ из ToolMessage;
  unit tool-loop заменил ошибочный текст модели `0` фактическим числом `1`;
- exact-once middleware удаляет уже вызванный tool из следующего model request;
  regression с повторным provider call дал одну audit-запись без `denied`;
- canonical `acceptance-prompt.txt` v2 проверяет SHA-256 начального/изменённого
  result и root sentinel, а `restart-acceptance-prompt.txt` требует
  `search_context: 1` и `min_results=1`;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 97 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.6.0`, `pip check`, CLI `--help`,
  безопасный OpenAI doctor и `doctor --live` — успешно;
- полный изолированный live acceptance использовал run `20260823-221609`,
  `gpt-5-nano`, новые workspace/data/thread и manifest v2; runtime дал
  `32 PASS, 0 FAIL, 0 BLOCKED, 1 PENDING`, все exact counts и hash-предикаты
  совпали;
- PowerShell-проверка после live-run подтвердила отсутствие тестового каталога,
  root sentinel и outside placeholder;
- restart с тем же `AGENT_DATA_DIR` и новым thread вызвал `search_context`
  ровно один раз, вернул `4 result(s)` и manifest verdict
  `2 PASS, 0 FAIL, 0 BLOCKED, 0 PENDING`; повторный denied отсутствует.

Версия 0.6.0 считается production-ready в границах локального
однопользовательского CLI. Многопользовательский сетевой сервис по-прежнему
требует отдельной процессной изоляции, аутентификации, централизованного аудита
и координации одновременных записей и не входит в область данного ТЗ.

Результат 0.6.0 выше сохранён как история. Проверка 0.7.0 от 2026-08-23
выполнена после устранения дефектов первого Ozon-эксперимента:

- общий `/workspace/` исключён из exact-file allowlist, при этом точный
  дочерний путь по-прежнему ограничивает чтение;
- индексатор до обхода отсекает generated/cache/coverage, Playwright и browser-
  profile пути с регистронезависимым сопоставлением;
- `list_context_sources` ограничен 20 источниками по умолчанию и 50 максимум;
- Ozon-промпт сокращён до одного доказанного дефекта, узких retrieval-запросов
  и не более 15 функциональных tool calls;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 100 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.7.0`, `pip check`, CLI `--help`,
  безопасный OpenAI doctor и `doctor --live` — успешно;
- отдельный live root-scope ход нашёл и прочитал `/workspace/pyproject.toml`
  после общего упоминания `/workspace/`, вернул `0.7.0` без denied events;
- полный изолированный live acceptance использовал run
  `20260823-231533`, `gpt-5-nano` и новые workspace/data/thread; runtime дал
  `32 PASS, 0 FAIL, 0 BLOCKED, 1 PENDING`, а cleanup завершился;
- новый процесс/thread на той же SQLite-БД вызвал `search_context` ровно один
  раз, получил `4 result(s)` и закрыл restart-аудит с
  `2 PASS, 0 FAIL, 0 BLOCKED, 0 PENDING`.

Версия 0.7.0 считается production-ready в границах локального
однопользовательского CLI. Проверка Ozon выполняется отдельно на временной
копии и чистой БД; её изменения не переносятся в исходный проект автоматически.

Patch 0.7.1 добавлен после первого clean-DB индексирования Ozon: файл
`analysis_30_days.txt` имеет корректный UTF-16LE BOM, но 0.7.0 принимал его
NUL-байты за binary. После переноса BOM-detection перед binary guard выполнены
Ruff и format-check, `pytest -ra` дал 102 passed и 1 planned symlink skip,
editable package сообщает 0.7.1, `pip check` и OpenAI `doctor --live` успешны.

Hardening 0.8.0 добавлен после первого LLM-хода на чистой Ozon-БД. Модель
выполнила четыре `search_context` при prose-максимуме два и превысила общий
максимум 15 попыток. Новая model middleware парсит русские/английские числовые
ограничения, считает фактический current-turn audit, удаляет exhausted tools из
следующего model request и подавляет stale provider-call. Regression охватывает
парсер, per-tool и total budget; полный прогон дал Ruff PASS, 105 passed,
1 planned symlink skip, editable 0.8.0, чистый `pip check` и успешный OpenAI
`doctor --live`.

Первый Ozon live-run 0.8.0 остановился после ровно 15 audit events, доказав
работу total budget, но OpenAI вернул 400: `parallel_tool_calls` запрещён без
`tools`. Patch 0.8.1 удаляет этот model setting и сбрасывает `tool_choice` при
пустом toolset; regression проверяет итоговый request непосредственно.

Повтор 0.8.1 показал, что sequential normalizer был внешним middleware и видел
старый непустой toolset до budget narrowing. В 0.8.2 порядок изменён так, чтобы
sequential normalizer выполнялся после toolset policies; композиционный тест
проверяет именно этот полный путь до model handler.

Ozon live-run 0.8.2 успешно завершился без 400 и остановился ровно на 15 audit
events, однако сделал три `search_context`: строка prompt была перенесена между
«не более двух узких» и именем tool. Patch 0.8.3 допускает такой перенос только
внутри того же предложения и закрепляет точную формулировку regression-тестом.

Финальный clean-DB Ozon-run 0.8.3 описан в `OZON_EXPERIMENT_REPORT.md`:
индексация 230 файлов/582 чанков с exit 0, live exit 0, ровно 15 audit events и
2 `search_context`, temp worktree чист. Внешние проверки Ozon: Ruff lint PASS,
57 pytest PASS; один `ruff format --check` drift в exporter существовал уже в
baseline. Исходный Ozon-проект не изменялся.

## GLM-5.2 / Zhipu integration 0.9.0 — 2026-08-24

- Добавлены канонический provider `zhipu` и CLI-алиас `glm`; оба разрешаются в
  runtime identity как `zhipu` с моделью `glm-5.2`.
- Поддержаны официальные `ZAI_API_KEY`, `ZAI_MODEL`, `ZAI_BASE_URL` и
  совместимые `ZHIPU_*` aliases. Стандартный endpoint и Coding Plan endpoint
  выбираются конфигурацией, секрет не хранится в репозитории.
- Thinking включён через `extra_body`, а недопустимая для Zhipu температура
  отклоняется до сетевого запроса.
- Недокументированный Zhipu-параметр `parallel_tool_calls` не отправляется;
  defensive response normalization по-прежнему оставляет один tool call.
- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 31
  файл отформатирован; `pytest -ra` — 122 passed, 1 planned Windows symlink
  skip.
- CLI `doctor` для `--provider glm` и `--provider zhipu` — PASS с тестовым
  несекретным ключом; оба показали модель `glm-5.2` и официальный base URL.
- Изолированно собран `deep_context_agent-0.9.0-py3-none-any.whl`, SHA-256
  `52C2986B90E7400DB8F9D6E6641241FC81344280B200AD6F6872E437CEFAFDB8`.
- Реальный `doctor --live` не выполнялся: `ZAI_API_KEY`/`ZHIPU_API_KEY` не
  настроен ни в процессе, ни в `.env.local`. Это явное внешнее условие, а не
  дефект кода; live-проверка должна быть выполнена после локальной установки
  ключа без его вывода.

## Provider priority and failover 0.10.0 — 2026-08-24

- Добавлены взаимоисключающие `--provider` и `--providers`, а также
  `AGENT_PROVIDER_PRIORITY`; порядок списка задаёт порядок model calls.
- Конфигурация отклоняет пустые элементы, неизвестные имена и канонические
  дубликаты aliases. Пустая env-строка означает отключённую цепочку и сохраняет
  одиночный `AGENT_PROVIDER`.
- `ProviderFailoverMiddleware` даёт каждому провайдеру настроенные model-call
  retries, затем безопасно переключается на следующий. Успешный fallback
  закрепляется на текущий ход и сбрасывается к primary перед следующим ходом.
- Failover расположен только в model-call middleware: уже выполненные tools не
  переигрываются. Runtime identity и `runtime_info` отражают активный provider,
  полный приоритет, число failover и неуспешные имена без API-ответов/ключей.
- Одиночный provider сохраняет прежнее исключение для обратной совместимости;
  полная ошибка много-провайдерной цепочки содержит только provider/model и
  безопасный тип исключения.
- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 31
  файл отформатирован; `pytest -ra` — 135 passed, 1 planned Windows symlink
  skip.
- CLI doctor с тестовыми ключами корректно показал приоритет
  `openai,zhipu,deepseek`. Реальный `doctor --live` на существующем локальном
  OpenAI key и цепочке `openai,lmstudio` вернул `live_provider=openai` и `OK`.
- Изолированный runtime live-test с приоритетом `lmstudio,openai`, новой БД и
  timeout 15 секунд подтвердил настоящий failover:
  `answer=FAILOVER_RUNTIME_OK`, `active_provider=openai`, `failover_count=1`,
  `failed_providers=lmstudio`.
- Собран `deep_context_agent-0.10.0-py3-none-any.whl`, SHA-256
  `35A0766EF1CFE06D3599649CD1C9F646C077749EFDF8BFEE85731D2D627AA5E8`.
