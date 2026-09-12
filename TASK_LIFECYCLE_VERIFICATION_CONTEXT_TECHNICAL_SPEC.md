# ТЗ 0.32: жизненный цикл задачи и окружение верификации

## Дополнение к завершению 0.32 — запланировано

Нормативное дополнение: [ТЗ R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
и [порядок реализации](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).
Оно уточняет сохраняемый verification context, полный check plan, устойчивое
владение/reconciliation, сквозной REPAIR, Web/API и live/release gates.
Исходные A01–A34 и ограничения прав остаются обязательными. Опубликованная ветка
и 449 passed не означают закрытие production-приёмки. В этом этапе изменены
только документы; код новых требований ещё предстоит реализовать.

Дата: 2026-09-09. Статус: **запланировано, код ещё не исправлен**.
Текущая версия пакета: 0.31.0. Подготовка этого ТЗ не меняет версию,
состояние пользовательских задач или результат их проверок.

Порядок реализации: [основной промпт 0.32](DEEP_CONTEXT_AGENT_0_32_TASK_LIFECYCLE_PROMPT.md).
Web-часть: [Web-промпт 0.32](DEEP_CONTEXT_AGENT_0_32_WEB_TASK_STATE_PROMPT.md).
Документ уточняет гарантии этапов 0.25–0.31 для всех путей исполнения.

## 1. Подтверждённый incident и границы выводов

Источник — локальные SQLite, проверенные 2026-09-09; время ниже — Москва, UTC+03.

| Идентификатор | Значение |
| --- | --- |
| Autopilot job | `9134483d5b0edd7fe90e758e` |
| Saved task | `d97b9babc8b740e4b7ff5427770d0c87` |
| Web task / parent diagnostic | `a3a399f949704266abfefbbef5fb497d` |
| Последний failed request | `b573a536fca944cc90607627bf7a5e7b` |
| Выполнявшаяся версия | `0.31.0` |
| Начало job | 2026-09-08 18:21:56 |
| Истечение saved-task lease | 2026-09-08 18:37:56 |
| Последний job heartbeat | 2026-09-08 18:39:07 |
| Остановка job | 2026-09-08 18:39:11 |

Job завершился `blocked`, `verification=failed`,
`error_code=task_authority_lost`; saved task осталась `running`, revision=2.
Зарегистрированы две успешные правки `providers/html_card.py`, три VERIFY unit,
два yielded и один failed REPAIR. Последний запрос к `zhipu/glm-5.3-flash`
дважды получил `OpenAITimeoutError` примерно через 120 секунд; последний tool
audit пуст. Настройка резервного OpenAI не доказывает его успешное использование.

Сохранённые проверки запущены с cwd `/workspace` и
`C:\script\20260820-langchain\.venv\Scripts\python.exe`, тогда как проект
находится в `/workspace/ozon_market_analytics`. Результат pytest — 11 passed;
Ruff check — UP035 в `src/ozon_analytics/services/core.py`; format-check отметил
`services/core.py` и `web/app.py`. Это исторические результаты в неверном
контексте проверки, а не актуальный итоговый PASS проекта Ozon. Повтор из
правильного root может дать другой набор замечаний, включая форматирование.

Подтверждение по коду 0.31.0:

- Web/CLI claim использует фиксированный `task_timeout_seconds + 60`;
  `AutopilotHeartbeat` обслуживает другой lease в `autopilot.sqlite3`.
- `TaskStateStore.owns()` проверяет срок saved-task lease, а `finish()` требует
  его действительности. Web вызывает `finish()` без обработки `False`.
- Ветка обычного project-change VERIFY не передаёт root в общий verifier.
- `ProjectCheckRunner._project_python()` возвращает `sys.executable`, если
  не нашёл `.venv` относительно execution root.

Тайм-ауты увеличили длительность исполнения. Непосредственная причина
`task_authority_lost` подтверждается истёкшим saved-task lease при живом job
heartbeat; причина тайм-аутов внешнего провайдера отдельно не установлена.

### 1.1. Контекст источников и применение к этому incident

Источники повторно проверены 2026-09-09. Ниже — применимые идеи, затем собственные
проектные решения DCA; ссылки не являются доказательством их реализации.

- [Авторский кейс Habr: DCS/ICS](https://habr.com/ru/articles/1064052/)
  описывает механические gates вместо одних инструкций, сохранённую передачу
  работы, отдельную проверку результата, ограничение попыток и разделение
  независимых причин. В DCA применяем эти принципы через L01–L05/L09/L13:
  ownership проверяет controller, передача подтверждается durable записью,
  readiness устанавливает verifier по receipts. Это адаптация, не установка DCS
  и не перенос всех его ролей или модельной иерархии. Не добавлять ручное
  подтверждение каждого heartbeat; согласованный scope остаётся автономным.
- [Репозиторий LangChain](https://github.com/langchain-ai/langchain)
  разделяет высокоуровневые возможности Deep Agents и управляемую оркестрацию
  LangGraph. Сохраняем используемые интеграции, а lease/finalization реализуем
  в существующем controller DCA, не в отдельном обходном агенте.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
  различает checkpoints одного thread и межпоточное хранилище данных.
  Отделяем graph-state от saved-task authority и terminal journal. Ни checkpoint,
  ни RAG-память не становятся источником разрешений или статуса выполнения.
- [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)
  сохраняют состояние по шагам; replay после выбранного checkpoint повторно
  исполняет последующие узлы, включая внешние вызовы. Поэтому recovery должен
  учитывать receipts побочных эффектов, а не безусловно повторять весь worker.
- [LangChain runtime](https://docs.langchain.com/oss/python/langchain/runtime)
  предоставляет dependency injection через runtime context. Для DCA это место
  передачи server-owned ExecutionOwnership/VerificationContext, а не полномочий
  из модельных tool arguments. Конкретные доступные API сверяются с установленной
  и закреплённой версией зависимостей до реализации.

Собственные решения этого ТЗ — dual lease renewal, CAS fencing, terminal outbox,
идемпотентные проекции и project-environment preflight. Источники не обещают
автоматическое продление наших аренд, выбор Ozon venv, exactly-once файловых
изменений или атомарность нескольких SQLite. Не объявлять эти гарантии готовыми
после подключения checkpointer; не вводить обязательный LangSmith/облачный
backend, отправку документов наружу или обновление зависимостей без необходимости.

Разделить реализацию на связанные root-cause work items:
`saved_task_lease_not_renewed`, `terminal_projection_missing`,
`verification_context_not_propagated`. Для каждого сохранять конкретный scope,
regression, diff и итог проверки. Общая приёмка 0.32 проверяет также их совместную
работу; успех одного item не закрывает остальные. Это рабочая декомпозиция
исправления DCA, а не предложение снова поручать пользователю делить задачи.

## 2. L01 — единый контракт владения

Сохранить различие: разрешения задачи, аренда выполнения, checkpoint revision и
временные бюджеты. Продление аренды не расширяет scope/allow_write и не сбрасывает
model/unit/active/wall-clock limits, provider retries и repair counters.

Controller связывает task_id, job_id, Web execution/request ID, owner,
task fencing revision/generation и job lease generation в `ExecutionOwnership`.
Generation обозначает исполнителя; checkpoint revision — версию данных.
Обычный heartbeat не увеличивает fencing revision и не делает собственный
SavedTask устаревшим. Смена владельца/cancel/revoke меняет fencing token.

Политика едина для Web chat, CLI chat/ask/job, scheduler recovery,
project-change/project-test/verification-only и подтверждённого repair 0.31.
Для задачи без saved_task отсутствие второй аренды задаётся явно в контракте,
а не маскируется catch-all исключением.

Передача управления фиксирует previous/new generation, checkpoint/unit cursor,
scope/approval hash, verification context reference, receipts, remaining work
и бюджеты. Новый worker принимает запись через CAS; старый после release не
продолжает работу. Model/thread/run IDs коррелируют с ownership, но не заменяют
task/job IDs. Смена модели не создаёт новые полномочия.

## 3. L02 — heartbeat двух аренд

Добавить CAS renewal saved-task lease и controller heartbeat, обслуживающий
task и job. Heartbeat работает независимо от LLM, tools, subprocess stdout и SSE
от claim до подтверждённой финализации, включая переходы между units и VERIFY.
Queued/waiting_user/paused не держат активного владельца без необходимости.

Renewal допускается только для текущих owner/generation/revision, состояния
running, живой аренды и неотозванных полномочий. Просроченный worker не вправе
сам себя «оживить»; новое владение выдаёт scheduler через recovery CAS.
Частичное обновление двух SQLite не считается подтверждённым владением обеими.
До следующего side effect controller должен подтвердить обе аренды.

Предлагаемые настройки (пока не добавлять как работающие в `.env.example`):

| Поле / environment | Default | Правило |
| --- | --- | --- |
| task_lease_seconds / AGENT_TASK_LEASE_SECONDS | 900 | Положительное конечное число |
| task_heartbeat_seconds / AGENT_TASK_HEARTBEAT_SECONDS | 30 | Не больше task_lease_seconds / 3 |
| task_reconcile_interval_seconds / AGENT_TASK_RECONCILE_INTERVAL_SECONDS | 5 | Интервал ограниченного reconciliation |
| task_finalize_timeout_seconds / AGENT_TASK_FINALIZE_TIMEOUT_SECONDS | 15 | Конечный бюджет retries финализации |

`AGENT_TASK_TIMEOUT_SECONDS` сохраняет смысл одноходового timeout и не является
общим сроком persistent-задачи. Interval рассчитывается и для job lease;
ошибочные NaN/inf/нулевые комбинации отклоняются конфигурацией.
Метрики длительности используют monotonic clock; durable expiry — UTC clock
controller с ограничением влияния скачков времени. Не использовать данные
времени из ответа модели. DB busy retries ограничены запасом до expiry.

## 4. L03 — fencing и прекращение side effects

Перед записью, checkpoint, запуском проверки и применением результата проверять
актуального владельца и requested cancel/pause. После takeover, revoke или loss
запрещены новая мутация, запуск REPAIR, публикация PASS и продление старого lease.
Поздний ответ модели может быть сохранён как диагностика, но не выполнен.

Существующие gates 0.31 для approval seal, path allowlist, source evidence и drift
обязательны. Нет fallback «продолжить запись, если heartbeat сломался».
На loss остановить/ограниченно завершить запущенный subprocess и записать его
реальный исход; частичный stdout не превращать в PASS.

## 5. L04 — согласованная terminal-финализация

Ввести общий `finalize_execution` для normal return, exceptions, cancel,
provider timeout, budget exhaustion и process recovery. Он сохраняет terminal
outcome execution, failed checks, side effects, error code и blocker, затем
применяет проекции к job, saved task, web task, diagnostics и repair relation.

Разные SQLite нельзя объявлять одной атомарной транзакцией. Выбрать durable
terminal record/outbox в одной БД и идемпотентные projection updates с ACK.
Событие содержит event_id, execution/owner generation, job_id, task_id,
source revision, outcome, receipt references и timestamps. Дубликаты не меняют
результат. Результат `finish=False` обрабатывается явно и журналируется.

Worker не получает право на файловую запись после expiry. Финализировать
просроченное владение может только controller/reconciler, если owner/generation
не сменился, совпадают task↔job↔execution и имеется durable terminal evidence.
Проверить CAS непосредственно при записи; не убирать безусловно lease-предикат
из `finish()`. Новый владелец, cancel или операторское решение имеет приоритет.

| Исполнение job | Проекция saved task при том же владении | Web execution |
| --- | --- | --- |
| blocked | blocked, owner освобождён | blocked, error_code/blocker |
| failed | blocked с исходным failure code, если схема не имеет failed | failed |
| cancelled | cancelled | cancelled |
| paused/interrupted | interrupted либо существующий resumable эквивалент | partial/interrupted |
| complete + проверенный конечный scope | completed | completed |
| complete одной unit, цель шире | partial, next step сохранён | partial |

Нельзя оставлять saved task running после подтверждённого terminal execution.
Нельзя закрывать общую задачу по завершению одного worker. Если проекции ещё не
сошлись, API показывает `finalization_pending`, а не ложный working/complete.
При недоступности durable storage выдавать явный bounded diagnostic fallback;
ошибку записи terminal event нельзя молча проглотить.

После durable terminal commit новые side effects запрещены, даже если остальные
проекции ожидают ACK. Release каждой аренды учитывается отдельно: успешно
освобождённый lease не нужно повторно renew-ить и считать новым ownership loss.
Projection retries живут в пределах finalization budget, затем переходят к
reconciler; ожидание ACK не создаёт бесконечно работающий model worker.

## 6. L05 — startup и периодический reconciliation

В bounded pages обрабатывать pending terminal projections и orphan running
tasks. Сначала replay существующего terminal record, затем проверка job/lease.
Если job terminal и связь/generation подтверждена — применить его проекцию.
Если terminal evidence нет, worker отсутствует и аренда просрочена — interrupted
с `worker_lost`, без PASS и без автоматического replay мутаций. Живую аренду
чужого worker не отнимать. `recover_claim` требует доказательства истечения или
завершённого согласованного handoff, а не только task ID.

Уже неконсистентные legacy записи (в том числе task из раздела 1) обрабатывать
после read-only inventory; нет связи — `reconciliation_required`, не догадка.
Действительные queued/recoverable jobs возвращаются в существующий scheduler;
явные blocked/paused/cancelled не запускаются автоматически.
Возобновление сохраняет бюджеты, scope, approval и receipts.

Восстановление LangGraph-checkpoint и DCA scheduler сверять по execution identity.
Для переходов с побочными эффектами подтвердить сохранение checkpoint/receipts
до handoff поддерживаемым публичным механизмом закреплённой версии; если это
невозможно, остановиться с точной причиной. Crash после мутации до записи receipt
означает неопределённый исход: сначала bounded сверка фактического target/hash,
затем подтверждение результата либо blocker, но не слепой повтор edit/command.
Outbox не даёт exactly-once внешних операций. Retention не удаляет pending
terminal events и evidence, необходимые для незавершённого recovery.

## 7. L06 — сохранённый VerificationContext

Реализация 2026-09-12: schema 2 immutable/context-addressed payload, отдельно
compact receipts; runtime разрешения никогда не восстанавливаются из payload.
Task/job/run/attempt и parent context обеспечивают attribution при repair/restart.
Подробная схема и ограничения сканирования уточнены в дополнении R1.

Каждая проверка получает schema-versioned immutable execution context:

- context_id, task_id/job_id, verification run/attempt ID;
- virtual project_root, происхождение root и trusted target;
- manifest/config/lock fingerprints, revision проверяемого кода;
- Python launch path/reference, environment identity, Python version,
  sys.prefix/sys.base_prefix, dependency fingerprint;
- required check plan, effective config paths, allowlisted command/cwd;
- capability snapshot, capture time и canonical SHA-256.

Физические пути и owner tokens не публиковать в UI; локальный runner получает
разрешённые реальные пути через сервер. Public DTO содержит virtual root,
безопасную environment label и fingerprints. Хранить общий контекст один раз,
receipts ссылаются на него; не раздувать prompts дублирующимся metadata.

Обязателен один resolver/contract для model tool `run_project_checks`,
verification-only, обычного development VERIFY, repair/recheck,
CLI/Web и scheduler recovery. Все вызовы ProjectCheckRunner передают явный
контекст/root. Тихий `run()` с подменой вложенного проекта `/workspace` запрещён.

## 8. L07 — ближайший корень и bounded discovery

Приоритет: валидный ранее сохранённый root; явный trusted выбор пользователя для
новой verification; ближайший `pyproject.toml` от согласованного конкретного
target; единственный bounded candidate. Новое указание, конфликтующее с
сохранённым контекстом, требует новой verification revision и проверки scope.
Target внутри вложенного manifest-проекта выбирает ближайший manifest, даже если
`pyproject.toml` присутствует и в workspace root.

Небезопасный explicit root отклоняется, не игнорируется с fallback на другой.
Несколько равноправных roots — structured ambiguity с bounded candidates.
Monorepo-задача использует отдельные contexts/check sets по проектам, не общий
случайный cwd. Для неподдерживаемого проекта без manifest — точный blocker.

Обход через общий scanner с pruning исключений до рекурсии и paging cursor;
лимит числа найденных manifests не заменяет лимит visited entries/time.
Исключать `.git`, `.venv`, node_modules, build/dist, cache и базы агента.
Resolve junction/symlink и confinement проверять до работы с root и targets.

## 9. L08 — Python проекта без скрытой подмены

Выбирать валидированный явно настроенный environment проекта, затем его
`.venv/Scripts/python.exe` или `.venv/bin/python`. Отсутствие/повреждение venv
не является разрешением использовать `sys.executable` агента или PATH Python.
Исключение — root самого DCA либо явно настроенное operator-managed окружение
с совпадающим context и preflight; причина выбора записывается.

Launch path venv сохраняется: раскрытие symlink `.venv/bin/python` до base Python
не должно обходить `pyvenv.cfg` и менять sys.prefix. Trusted environment может
иметь base interpreter вне workspace; это не разрешает чтение/правку файлов
там. Проверять invocation path, identity окружения и capabilities раздельно.

Preflight фиксированными командами с timeout проверяет существование/запуск
Python, sys.prefix, Python version, доступность pytest/Ruff и mypy при наличии
его в плане; подтверждает origin установленного проектного пакета относительно
выбранной копии. Sanitized env удаляет секреты/PYTHONPATH/PYTHONHOME и задаёт
контролируемые cwd/PATH/VIRTUAL_ENV; не наследует чужое окружение молча.

Missing dependency/runtime error отделяется от source defect. `uv`/Poetry/другой
manager допускается только явно поддерживаемым adapter, а не arbitrary shell.
При разрешённой подготовке окружения использовать manifest/lock/dev extras
выбранного проекта в изолированной среде. Без такого разрешения вернуть
`verification_dependency_missing` с конкретным setup action. Не удалять venv,
не устанавливать глобально и не менять source/lock ради скрытия environment FAIL.

## 10. L09 — корректные проверки, drift и PASS

План обязательных проверок: Ruff check → Ruff format --check → pytest → mypy,
если настроен/явно обязателен → compileall. Учитывать явный пользовательский scope
и конфигурацию проекта. Skipped/unavailable/error/timeout не равны passed;
отсутствие ненастроенного optional mypy обозначается not_applicable с причиной.
Обязательный mypy нельзя тихо пропускать. Обрабатывать фактически завершённые
checks отдельно от не начатых после отмены или environment blocker.

Команды остаются фиксированными, shell=False. pytest/plugins могут исполнять код:
checks capability разрешает только контролируемый запуск и временные артефакты,
но не форматирование с записью или произвольный shell. Писать кэши в temp,
проверять отсутствие непредусмотренных source/config mutations.

До запуска и перед принятием PASS сверять root/manifest/environment/code revision.
Изменение контекста делает старый evidence stale и требует recheck. Авторизованная
правка создаёт новую code revision; не объединять PASS разных ревизий/окружений.
Корректный repair reuses project context, обновляя проверяемую ревизию; расширение
scope/approval проходит gates 0.31. Исторические root-less checks не импортировать
как итоговый PASS и не использовать вслепую для форматирования.

Receipt: schema/check/run ID, context hash, effective cwd/config, Python/environment
identity, code revision, exit status/code, duration, bounded redacted stdout/stderr,
truncation и хэш полного захваченного вывода до усечения. Сравнение повторных
ошибок исключает duration/timestamp/random run ID из semantic fingerprint.

## 11. L10 — provider retries и конечные бюджеты

Heartbeat продолжается во время timeout/backoff/fallback; он подтверждает
владение, но не является полезным прогрессом. Проверять cancel/lease/budgets между
attempts и до исполнения tool calls из позднего ответа. Существующий circuit
breaker применяется раньше повторного полного timeout при открытом circuit.
Retry SDK и runtime имеют общий конечный ceiling. Fallback соблюдает ручной выбор,
privacy/local-only и согласованную стоимость; доступность резервного provider
сама по себе не означает разрешение на его использование.

Доступность provider/quota — наблюдение с timestamp/TTL, не постоянное свойство
из старого журнала. Replan, checkpoint, новый model thread и renewal не сбрасывают
счётчики фактических attempts. Независимый verifier применяет функциональные
критерии приёмки; косметические замечания вне scope не создают бесконечный repair.

`provider_timeout`, `task_lease_expired`, `task_owner_replaced`, `task_revoked`,
`job_lease_expired`, `verification_environment_unavailable`,
`verification_context_stale`, `state_reconciliation_required` различаются.
Старый `task_authority_lost` сохраняется как совместимая category с новым reason.
Loss аренды нельзя автоматически лечить бесконечным ростом timeout.

## 12. L11 — статус и диагностика для человека

Обычный UI показывает «Остановлена / Blocked», конкретный check/root/environment
и действие восстановления. Task/job/request IDs имеют раздельные подписи.
Heartbeat, два lease_remaining и generation доступны в раскрываемой диагностике;
они не заменяют текущую фазу и результат проверок. Загрузка страницы не продлевает
lease. В отсутствие работающего сервера UI не изображает активный worker.

API/SSE/terminal journal используют один execution outcome + projection status.
События lease_renewed/lost, terminal_recorded/projected, reconciliation и
verification_context_resolved/invalidation ограничены по частоте и размеру.
Не показывать raw owner tokens, ключи, prompts, абсолютные host paths.
Parent наследует child error code и structured blocker. При DB lag не публиковать
успешное завершение; после reload восстановить согласованный статус.

## 13. L12 — миграция и эксплуатация

Миграции аддитивны, идемпотентны и versioned; сохранить историю 0.31, checkpoints,
diagnostics и evidence. Новые lease/context/outbox fields имеют безопасные
legacy defaults. Перед upgrade снять schema/version inventory и безопасный
backup через SQLite backup API, а не копирование live WAL без согласования.

Reconciliation выполняется после миграции до автоматического resume. В smoke
использовать отдельные data/workspace/port; реальный Ozon incident воспроизвести
фикстурой, не переписывая пользовательскую БД ради зелёного теста. Server
shutdown bounded; завершить/отменить собственные live processes с проверкой PID.
Upgrade установленного runtime и публикация — после соответствующей авторизации.

## 14. L13 — критерий выпуска

Готовность требует фактических evidence по матрице ниже. HTTP 202, doctor OK,
наличие UI-кнопки и зелёный unit suite не заменяют end-to-end FAIL→REPAIR→PASS.
До реализации и live-приёмки 0.32 остаётся планом; не повышать runtime version и
не называть новый incident устранённым. Каждый непроверенный пункт имеет статус
not_run/blocked с причиной, а не подразумеваемый PASS.

## 15. Приёмка A01–A34

| ID | Сценарий | Обязательное доказательство |
| --- | --- | --- |
| A01 | Model turn дольше исходного task lease | Task/job heartbeats сохраняют обоих owners |
| A02 | Длительный check без stdout | Saved-task lease продлевается во время subprocess |
| A03 | Yield/переходы между units/repair | Нет промежутка с истёкшим владением; counters сохранены |
| A04 | Heartbeat с CAS | Renewal не меняет generation/revision исполнителя |
| A05 | Takeover worker B | Поздний worker A не renew/write/finalize B |
| A06 | Cancel/revoke во время model timeout | Новые side effects заблокированы, cancelled сохранён |
| A07 | Expired lease без takeover | Controller завершает status по terminal evidence, не даёт запись |
| A08 | `finish()` вернул False | Ошибка видна, durable projection/reconciliation достигает согласованности |
| A09 | Crash между commits и после side effect до receipt | Outbox сходится; неопределённая мутация сверяется, не replay-ится вслепую |
| A10 | Legacy job blocked, saved task running | Контролируемая projection без повторного запуска задачи |
| A11 | Живая задача при startup scanner | Reconciliation не захватывает чужой owner |
| A12 | Две вкладки, repeated terminal/resume | Одна актуальная generation, без двойных мутаций |
| A13 | DB busy/частичный heartbeat | Bounded retry; при loss gate закрыт и причина сохранена |
| A14 | Timeout/backoff/fallback | Lease жив, budget конечен, TTL availability и provider policy соблюдаются |
| A15 | Два manifest на пути к target | Выбран ближайший вложенный pyproject |
| A16 | Several roots/no root/unsafe explicit path | Точный blocker, без случайного workspace fallback |
| A17 | Огромное дерево с cache/.venv | Pruning/paging и visited/time ceilings подтверждены |
| A18 | Обычный project-change после мутации | VERIFY получает явный вложенный root/context |
| A19 | verification-only, tool checks, CLI/Web | Один resolver и одинаковые root/environment receipts |
| A20 | Repair 0.31 и restart/handoff | Durable transfer принят новым owner; context/approval сохранены, source права не меняются |
| A21 | Различные Python у DCA и проекта | Используется venv проекта, правильный sys.prefix/package origin |
| A22 | Venv отсутствует/сломана | Environment blocker, sys.executable fallback отсутствует |
| A23 | Symlink venv Python и Windows spaces/Cyrillic | Launch path и venv identity сохранены |
| A24 | Missing pytest/mypy/dependency | Это environment failure; source не правится без evidence |
| A25 | Manifest/lock/venv drift | Stale context требует новой проверки, не старого PASS |
| A26 | Ruff, format, pytest, optional mypy, compileall | Полный required plan учтён, skipped не объявлен PASS |
| A27 | Одинаковый failure с другими временем/ID | Устойчивый fingerprint останавливает бессмысленный retry |
| A28 | check stdout injection/секрет/большой output | Не исполняется, redacted, bounded и hashed |
| A29 | Repair/check изменили неразрешённый файл | Gate/safety veto блокирует COMPLETE |
| A30 | API/SSE/reload после BLOCKED | Task/job/Web/diagnostics согласованы или явно finalization_pending |
| A31 | Миграция и повтор restart | История сохранена, без duplicate terminal events и side effects |
| A32 | Exact incident fixture с lease+timeout | Исправление одного Ozon-like target, правильный VERIFY, нет stale running |
| A33 | Изолированный live end-to-end и повтор | FAIL→разрешённый REPAIR→PASS + forced timeout/cancel/restart negative runs |
| A34 | Release quality gate | Ruff/format/pytest/mypy/compileall/frontend/build/install; совместимость checkpoint/runtime APIs закреплённых версий |

Для A01–A14 использовать injectable clock и controlled slow provider/subprocess,
не нестабильные большие sleeps. A33 дополнительно содержит реальный доступный
LLM и отдельный контролируемый HTTP timeout transport с маленькими тестовыми
lease/interval; отличать simulated failure от реального ответа provider.
На Python/Ozon-like fixture обеспечить разные venv и конфигурацию Ruff так,
чтобы случайный cwd/Python не дал незаметный PASS. Прогоны выполнять дважды на
чистых БД, с сохранением redacted отчётов и проверкой состояния после restart.

## 16. Карта изменения модулей и документов

`task_state.py`: CAS renewal, fencing, finalization/reconciliation contracts.
`autopilot.py`: ownership link, terminal outbox, replay и heartbeat integration.
`runtime.py`: общий lifecycle, все VERIFY call sites, context/receipt propagation.
`project_checks.py`: explicit context, root/environment preflight, typed results.
`web.py`: нормальная/recovery submit и terminal projections, additive API/SSE.
`repair.py`: context/hash inheritance и incident terminal projection.
`diagnostics.py`, `config.py`, CLI: безопасные reason codes, defaults и telemetry.
`webui/src/app.ts`, static bundle: согласованный status, root/environment/checks.

Обновить [глобальное ТЗ](TECHNICAL_SPEC.md),
[глобальный промпт](IMPLEMENTATION_PROMPT.md),
[Web-ТЗ](WEB_INTERFACE_TECHNICAL_SPECIFICATION.md), system prompt,
ТЗ scheduler/continuity/orchestration/verification/repair, README, CHANGELOG и
IMPLEMENTATION_STATUS. В финальной реализации включить `.env.example` только для
действительно добавленных параметров; не редактировать пользовательский `.env`.
