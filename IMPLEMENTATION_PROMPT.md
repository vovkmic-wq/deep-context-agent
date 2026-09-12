# Управляющий промпт реализации

## Дополнение к завершению 0.32 — реализация и приёмка

Нормативное дополнение: [ТЗ R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
и [порядок реализации](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).
Оно уточняет сохраняемый verification context, полный check plan, устойчивое
владение/reconciliation, сквозной REPAIR, Web/API и live/release gates.
Исходные A01–A34 и ограничения прав остаются обязательными. На 2026-09-12
реализованы bounded renewal/reconciliation, отмена работающих checks/preflight,
исправлен подтверждённый REPAIR и общий Web execution status. Два реальных
LLM FAIL→REPAIR→PASS, браузерная проверка и clean-install smoke выполнены.
Точные доказательства и незакрытые gates: [матрица приёмки](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Версия пакета пока 0.31.0: частичная приёмка не означает production-релиз 0.32.

Реализован контракт R1 schema 2: immutable VerificationContext хранит task/job/run/
attempt IDs, snapshot разрешений проверяющего runner, root provenance, code/config/
lock/dependency fingerprints, версии и origins, launch path/prefix. В публичном
результате — только компактные ссылки; legacy schema 1 не принимается как новый
PASS. При resume сохранённый root проверяется до discovery. Discovery использует
общие исключения и SQLite frontier/cursor; partial не считается уникальным root.
Scanner не держит writer lock во время обхода; отмена сохраняет последний commit.
Фактическая Windows-приёмка и незакрытый Unix/symlink gate перечислены в матрице.

## Целевой этап 0.32: lease, terminal state и VerificationContext

Выполняй [промпт 0.32](DEEP_CONTEXT_AGENT_0_32_TASK_LIFECYCLE_PROMPT.md)
по [ТЗ 0.32](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md), L01–L13,
A01–A34; Web — по
[Web-промпту 0.32](DEEP_CONTEXT_AGENT_0_32_WEB_TASK_STATE_PROMPT.md).
Это план, а не отчёт о готовом коде. Версия пакета пока 0.31.0.

Начни с воспроизводимых failing regressions: длительный ход переживает срок
saved-task lease, `finish(False)` оставляет running, обычный project-change
проверяет вложенный Ozon из workspace агентским Python. Затем последовательно
реализуй dual renewal/CAS fencing, terminal outbox/reconciliation и обязательный
контекст root/environment во всех точках вызова ProjectCheckRunner.

Опирайся на механические gates, persisted handoff и независимую верификацию;
применение Habr/LangChain описано в разделе 1.1 ТЗ. Checkpoints не заменяют
полномочия runtime и не делают несколько SQLite одной транзакцией. Не устанавливай
DCS или новый облачный backend и не обновляй зависимости только ради аналогии.

Не лечи expiry бесконечным lease, снятием CAS или повторными просьбами
«продолжи». Не используй sys.executable как скрытый fallback проверяемого проекта.
Повтори incident в изолированной fixture, длительный live-сценарий и restart;
сопоставь task/job/diagnostics/API/UI, root и Python receipts. Заполни матрицу
приёмки фактическими результатами; непройденные пункты оставь явно открытыми.
Подготовка документов не разрешает объявлять этап выполненным.

## Целевой этап 0.31: подтверждаемая repair-задача

Выполняй `DEEP_CONTEXT_AGENT_0_31_VERIFICATION_REPAIR_PROMPT.md` строго по
`VERIFICATION_REPAIR_INCIDENT_TECHNICAL_SPEC.md` (R01–R16, A01–A30). Failed
read-only verification является source evidence, а не разрешением записи.

Создавай новый allow-write repair incident только отдельным typed API после
явного подтверждения пользователя. Seal canonical plan/evidence SHA-256 и source
revision; применяй централизованный write-gate перед каждой мутацией. Continuation,
semantic classifier, prompt text, смена модели и env не повышают полномочия.

Один incident исправляет одну root cause. Work units получают конкретные targets
и path territories. После изменений свежий независимый read-only verifier
проверяет mutation receipts и запускает ProjectCheckRunner. Версия
0.31.0 прошла зафиксированные в IMPLEMENTATION_STATUS.md regression/quality
checks и ограниченный live Web API. Эти результаты сохраняются как история,
но не доказывают прохождение всех A01–A30 или длительного полного repair-цикла:
последующий incident выявил открытые дефекты, описанные в этапе 0.32.

## Целевой этап 0.30: verification-only execution

Выполняй `DEEP_CONTEXT_AGENT_0_30_VERIFICATION_ONLY_PROMPT.md` строго по
`VERIFICATION_ONLY_EXECUTION_TECHNICAL_SPEC.md` (V01–V12, A01–A22). Exact
regression — job `b6b00104265fddec6ab4caf6`, diagnostic
`b77bc36c73c04006b549842456e6b5be`.

Запрос только на pytest/Ruff/mypy/compileall маршрутизируется как отдельный
`verification-only`, начинает deterministic VERIFY и запускает checks из
валидированного вложенного project root. Не запускай DISCOVER/PLAN/IMPLEMENT и не
выбирай последний прочитанный manifest как edit target. REPAIR допустим только
из persisted failed-check evidence и всегда возвращается в VERIFY.

Каждый terminal BLOCKED имеет непустой structured blocker; parent diagnostics
наследует job error code/category. VERIFY не расходует model calls. Repair prompts,
повтор provider/tool failures и input context ограничены. До A01–A22, полного
quality gate и двух live-прогонов не повышай версию и не заявляй production PASS.

## Целевой этап 0.29: executable handoff recovery

Выполняй `DEEP_CONTEXT_AGENT_0_29_EXECUTABLE_HANDOFF_RECOVERY_PROMPT.md` строго по
`EXECUTABLE_HANDOFF_RECOVERY_TECHNICAL_SPEC.md` (E01–E12, A01–A18). Сначала
воспроизведи incident `103bdbab6c558da6761380dc`, затем исправь runtime contract,
decomposition, preflight, bounded recovery, blocker persistence, verification и
Web observability.

Нельзя запускать IMPLEMENT с пустым target, увеличивать implementation attempts
на preflight failure или просить модель угадать файл. Широкая цель должна стать
durable dependency graph с конкретными leaf operations. `missing_information`
разрешён только при действительно сохранённом prerequisite; planner defect имеет
отдельный код и не перекладывается на пользователя.

Подготовка документов не является исправлением. До A01–A18, полного regression
suite и двух изолированных live-прогонов не повышай версию и не заявляй production
PASS. Не затрагивай пользовательский сервер 8765 и не публикуй Git без отдельной
актуальной команды пользователя.

## Целевой этап 0.28: production orchestration

Выполняй `DEEP_CONTEXT_AGENT_0_28_PRODUCTION_ORCHESTRATION_PROMPT.md` строго по
`PRODUCTION_ORCHESTRATION_TECHNICAL_SPEC.md` (R01–R07, A01–A14). Маршрутизация
учитывает межмодульный footprint до точных путей; quoted logs остаются данными.
Targeted read, project discovery, write и project checks являются независимыми
capabilities. `run_project_checks` не является discovery.

VERIFY принадлежит runtime: mutation receipt запускает allowlisted checks, а
модель не устанавливает PASS/COMPLETE. Каждая unit получает новый worker thread,
одну операцию, objective/contract по 12 000 символов и не replay-ит полную историю.
Перед IMPLEMENT runtime строит schema-first snapshot names/signatures/fields.
Транспортный сбой открывает быстрый provider circuit и немедленно допускает
разрешённый fallback. Terminal parent агрегирует durable child diagnostics.

Нельзя лечить эти дефекты увеличением timeout или текста системного промпта.
До A01–A14 и записи реальных результатов этап не считается production PASS.

## Целевой этап 0.27: durable scheduler и evidence-driven execution

Выполняй `DEEP_CONTEXT_AGENT_0_27_DURABLE_SCHEDULER_PROMPT.md` строго по
`DURABLE_EXECUTION_SCHEDULER_TECHNICAL_SPEC.md` (S01–S12, A01–A20), а Web-часть —
по `DEEP_CONTEXT_AGENT_0_27_WEB_DURABLE_SCHEDULER_PROMPT.md`. Это целевой этап:
до изменения кода и повторной приёмки не называй его реализованным.

Корневая проблема подтверждена diagnostic
`83bbc5d39bf7410b87462378c5259487`: persistent project-change после семи
успешных soft yields остановился общим 900-second `task_deadline`, имея 960
прочитанных строк, но 0 changed files и 0 checks. Нельзя исправлять это только
увеличением timeout.

Production-основа: отдельный durable scheduler, фазовый автомат
DISCOVER→PLAN→IMPLEMENT→VERIFY→REPAIR, evidence-driven renewal и четыре независимых
временных бюджета. Handoff выдаёт новый unit deadline только при новом durable
evidence, но не сбрасывает active/wall/token/cost/tool budgets или права. Discovery
ограничен; после ceiling обязателен executable transition, допустимая reasoning
escalation либо точный structured blocker. В allow-write IMPLEMENT требуется
реальная mutation receipt или blocker, а не фиктивная запись. Ask/Plan/read-only
никогда не принуждаются к мутации.

Web/CLI используют один runtime. HTTP/SSE disconnect не владеет job; restart
восстанавливает queue/lease/phase без replay side effects. UI показывает
completed/yielded/failed отдельно, четыре таймера, discovery counters, last verified
progress и next executable operation. Не проси пользователя выбирать batch size.

Перед повышением версии обязательны A01–A20, полный regression suite и минимум два
изолированных live-прогона исходного service-layer сценария с disconnect/reconnect,
restart, mutation, check и terminal. Подготовка этих документов не является PASS.

## Этап 0.26: bounded progress, adaptive routing и observable memory

При реализации этого этапа выполняй
`DEEP_CONTEXT_AGENT_0_26_BOUNDED_PROGRESS_RECOVERY_PROMPT.md` по
`AUTOPILOT_PROGRESS_RECOVERY_TECHNICAL_SPEC.md` (P01–P15, A01–A26).
Наличие нового промпта не означает исправление инцидента
`5dc5c86b57fd25f873da7acd`. Сначала воспроизведи дефекты, затем исправляй runtime
и подтверждай каждый пункт автоматическими и повторными изолированными live-тестами.

Приоритетные изменения: runtime-owned ledger до лимита без обязательного LLM
checkpoint; soft yield с конкретным next operation; range-aware read/coverage;
раздельные information/implementation/verification progress; ограниченная execution
escalation без обхода manual/local-only/budgets; существующая parent/child диагностика;
единый token estimator; Web-настройка adaptive profiles; actual memory indicator;
golden retrieval metrics hit@k/MRR для semantic и lexical запросов.
Нельзя ограничиться увеличением recursion или изменением текста промпта.

Для этого этапа отменяется blanket rule «два чтения неизменённого пути»:
проверяй полезность фактически возвращённых диапазонов и ограничивай их budgets.
Third-read regression должен отличать повтор той же страницы от третьей новой
страницы. Старые exact-once, path/security и bounded stale-edit recovery сохраняются.
Новая unit не начинает исследование с нуля и не replay успешные мутации. Task/job
identity и общий бюджет сохраняются, current права имеют приоритет над старой историей.

Обновлённые документы — не разрешение перезапустить сервер, менять Ozon или
публиковать Git. Действуй по актуальной пользовательской задаче. Статус реализации,
тестов и релиза веди отдельно: подготовка ТЗ не должна называться production PASS.

## Этап 0.25: resume, semantic intent и resource routing

Выполняй `DEEP_CONTEXT_AGENT_0_25_RESUME_MODEL_ROUTING_PROMPT.md` строго по
`TASK_RESUME_MODEL_ROUTING_SPEC.md` (R1–R6). Эти правила приоритетнее предыдущих:
project-change/project-test не запускают полный аудит. Продолжение восстанавливает
identity/цель/checkpoint; semantic classifier не выдаёт разрешений. Высокий риск
имеет приоритет над простотой при выборе reasoning/standard/fast. Без достоверных
профилей/цен не обещай оптимизацию стоимости. Состояние завершённого worker не
равно завершению согласованной задачи. Проверяй длинную формулировку из инцидента.

## Предыдущий этап: task continuity и generation reliability 0.24

Выполняй `DEEP_CONTEXT_AGENT_0_24_TASK_CONTINUITY_PROMPT.md` по пунктам.
Различай постоянную задачу, боковой анализ сообщения и отдельный model call.
Продолжение восстанавливает конкретную согласованную задачу с пересечением
текущих разрешений; отсутствие слова «проект» не является отзывом прежней задачи.
Чтение, discovery и запись проверяются независимо. Нельзя извлекать полномочия
из логов или retrieval. Старый отказ не отменяет актуальные runtime-права.
Model HTTP success не равен completed task: сохраняй фактическую диагностику,
ограничивай retries, проверяй прогресс и отражай partial/blocked без ложного PASS.
Обязателен regression «разработка → анализ лога → продолжение разработки».

Чат и проверка провайдеров используют один список максимум из пяти последних
chat-моделей. Не путай подтверждённую дату выпуска с created в каталоге API;
показывай источник дат и сохраняй текущую модель, даже если она выпала из пятёрки.

Этап 0.26.0 реализован и принят повторными изолированными live-сценариями.
Фактические проверки, исправленный live-дефект и границы применения фиксируются
в IMPLEMENTATION_STATUS.md; прошлые отчёты не доказывают готовность будущих правок.

## Structured data-aware routing 0.23.0 (2026-09-03)

Работай по `DEEP_CONTEXT_AGENT_0_23_STRUCTURED_ROUTING_PROMPT.md`, основному и
Web-ТЗ. Не классифицируй сырой query целиком: сначала отдели direct instruction
от fenced/quoted/tagged logs, attachments, traceback, terminal и tool output.
Структурированное решение независимо фиксирует execution, workflow, scope,
project-scan authority и mutation intent.

Persistent execution не означает project audit. Для non-project workflow
используй durable execute unit с теми же lease, heartbeat, retry и terminal
гарантиями, но без ProjectAuditStore manifest. `allow_write` — только trusted
capability и применяется совместно с прямой mutation-командой. Перед релизом
повтори исходный PowerShell-log incident, adversarial quoted instructions,
explicit overrides, SQLite migration, API/SSE/UI и live Web сценарий.

## Dynamic models, hybrid retrieval and bounded scans 0.22.0 (2026-09-03)

Работай по `DEEP_CONTEXT_AGENT_0_22_HYBRID_RETRIEVAL_MODEL_UI_PROMPT.md`,
основному и Web-ТЗ. Соблюдай порядок: динамический выбор совместимой модели в
закреплённом заголовке чата; локальная гибридная память FTS5/BM25 +
FastEmbed/ONNX CPU + Qdrant; единый exclusion policy и cursor pagination для
всех широких обходов; понятная диагностика и полный безопасный terminal journal.

Embedding provider отделён от chat provider. Документы не отправляются во
внешний embedding API, скрытый cloud fallback запрещён. При отказе FastEmbed
или Qdrant сохраняй явно обозначенный lexical-only поиск. Смена embedding
signature создаёт отдельный resumable индекс и переключает его атомарно только
после завершения.

Все `glob`, `grep`, indexing, audit и discovery используют один центральный
artifact policy, bounded pages и opaque cursor. Точный известный файл читается
через paginated `read_file`, а не широким tree grep. Timeout обязан вернуть
partial status, подтверждённые counts и cursor для продолжения. UI показывает
`indexed/unchanged/skipped`; diagnostics хранит terminal status,
provider/model, duration и file counts без секретов. Повтори исходный Ozon
timeout, миллионный offline corpus и live CPU/vector/model-switch сценарии до
изменения версии и публикации.

## Five chat modes 0.21.0 (2026-09-02)

Работай по `DEEP_CONTEXT_AGENT_0_21_CODEX_CHAT_MODES_PROMPT.md`, основному и
Web-ТЗ. В Web chat допустимы только `Agent`, `Ask`, `Plan`, `Debug`,
`Multitask`; прежние mode values удалены из HTML/TypeScript/API и отклоняются
как 422. Политика режима исполняется backend-ом: Ask/Plan всегда read-only,
Debug single-turn с опциональной trusted диагностикой, Agent до результата,
Multitask — отдельные child threads в bounded pool. Ни один режим не расширяет
workspace, provider, MCP, shell или destructive policy.

Добавь круговой bounded estimate активного контекста. Deep Agents
SummarizationMiddleware автоматически компактирует раннюю историю для модели,
но SQLite archive/retrieval не удаляется. Проверь API policy, реальный запрет
write, два конкурентных workers, отсутствие legacy values, production bundle,
браузерный UX и live provider turn до публикации.

## Durable lease orchestration 0.20.0 (2026-09-02)

Работай по `DEEP_CONTEXT_AGENT_0_20_DURABLE_LEASE_ORCHESTRATION_PROMPT.md`.
Каждый controller lease содержит token и monotonic generation; все owner
transitions и filesystem mutation gate проверяют оба. Heartbeat действует во
время audit/repair/verification unit, expired running unit сохраняется как
`interrupted`, а recovery создаёт новую generation и повторно сверяет manifest.
Ограничивай unit batch/recursion/soft deadline программно и показывай persisted
heartbeat/deadline через тот же SSE. Повтори expired-lease, stale-owner и live
долгую unit; не объявляй production до полного package acceptance.

## Durable failure journal 0.18.0 (2026-08-30)

Работай по `DEEP_CONTEXT_AGENT_0_18_DURABLE_FAILURE_JOURNAL_PROMPT.md`,
основному `TECHNICAL_SPEC.md` и Web-ТЗ. Не ослабляй транзакционный rollback:
diagnostics хранится в отдельной SQLite вне checkpoint и FTS5. До model call
создай correlation record, после ошибки зафиксируй safe provider attempts,
tool audit, checkpoint baseline, rollback outcome и filesystem side effects.

Текст failed request регулируется `off|metadata|redacted|full`; safe default —
`redacted`, `full` требует явной локальной настройки. Terminal Web task обязан
переживать restart. Browser получает только safe code/request ID; подробности
остаются в ограниченном локальном журнале. Добавь retention, migration,
structured rotating log, CLI/Web operator access, security regressions и live
повтор ошибки до объявления production.

## Active-context recovery 0.17.0 (2026-08-30)

Работай по `DEEP_CONTEXT_AGENT_0_17_ACTIVE_CONTEXT_RECOVERY_PROMPT.md`,
`TECHNICAL_SPEC.md` и Web-ТЗ. Полный миллионный корпус и история остаются в
SQLite/FTS5, но model call не должен повторно получать все старые тела tools.
Применяй transient context editing к копии сообщений до failover, сохраняй
текущую пачку и не разрушай checkpoint/search/archive.

Web task обязан сохранять safe terminal event, отдавать его через status и
повторный SSE, а ошибка — иметь стабильный операторский код без raw SDK detail.
На Windows фильтруй только точный Proactor callback reset 10054. Закрепи
регрессиями, повтори на реальном длинном checkpoint и на чистом live Web
runtime; production объявляй лишь после полного контура.

## Bounded stale-edit recovery 0.16.0 (2026-08-29)

Работай по `DEEP_CONTEXT_AGENT_0_16_STALE_EDIT_RECOVERY_PROMPT.md` и
`TECHNICAL_SPEC.md`. Не ослабляй exact `edit_file`: первый match-conflict
даёт только один целевой recovery-read того же path/version и один
revised edit по свежему content. Второй conflict закрывает recovery и
требует stop/report. Сырой failed `old_string` не возвращай.
Не разрешай `read_context_window` подменять `read_file` для
`/workspace`: invalid source/radius должен давать safe ToolMessage, а
hard per-turn budget обязан остановить runaway context-window loop.

Закрепи external-mutation integration, bounded negative и third-read regression
с учётом более нового P03: повтор старого диапазона и новая страница различаются.
Повтори исходную ситуацию в live runtime и на
реальном LLM provider. После full offline/live/package contour обнови
документы/version/status и только затем публикуй release.

## Provider and files UX hardening 0.15.0 (2026-08-29)

Работай по `DEEP_CONTEXT_AGENT_0_15_PROVIDER_FILES_PRODUCTION_PROMPT.md`,
основному `TECHNICAL_SPEC.md` и Web-ТЗ. Диагностируй LM Studio через
ограниченный `/models`: если Web получил default placeholder
`local-model`, выбери первую загруженную не-embedding модель и
примени её к новым Web-вызовам. Показывай safe причины:
сервер недоступен, модель не загружена, неверный каталог или
Chat Completions не поддержан.

Локальный loopback provider не требует предупреждения об оплате
API. Для remote provider сохрани opt-in confirmation. Добавь
создание process-local OpenAI-compatible profiles `custom-*`: HTTP только
для loopback, HTTPS для remote, ключ только из server environment,
никогда из browser payload/response.

В Files раздели «Назад» (история) и «Выше» (родитель),
выключай их только когда действие невозможно. Кнопка «Открыть»
обязана показать loading, success/error, точный virtual path и
число объектов.

Закрепи всё API/unit/bundle/browser regression-тестами, выполни
реальную бесплатную LM Studio live-проверку, полный Python/TS/
package контур, secret scan и browser desktop/mobile acceptance. Публикуй
только после фактического PASS.

## Codex-like local Web UI 0.14.0 (2026-08-29)

Работай по `DEEP_CONTEXT_AGENT_0_14_WEB_PRODUCTION_PROMPT.md`, основному
`TECHNICAL_SPEC.md` и нормативному `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`.
Не создавай Web-only runtime, SQLite, файловую логику или provider chain: Web
является клиентом тех же сервисов, данных и security policies, что CLI.

Сделай чат ориентированным на задачи: thread list/history из SQLite, новый
thread, нижний auto-grow composer, stop/error states, выбор инженерной роли,
optional local Enter-to-send и неизменный Shift+Enter newline. Роль является
ограниченной инструкцией и никогда не даёт право записи.

Исправь virtual root `/workspace`, добавь явные состояния и counters индексации,
кликабельный файловый browser/editor с bounded preview и optimistic SHA-256.
Подпиши audit include/exclude/batch size и safe settings по-русски / по-английски
с понятными комментариями.

Используй thread-safe live provider registry: все новые chat/audit/doctor берут
один snapshot текущего порядка; настроенные providers можно добавить, убрать
или переставить без рестарта, а каждый — проверить отдельным opt-in live call.
Ключи и raw exceptions браузеру не передавать.

Проверь API/SSE, TypeScript bundle, desktop/mobile UX, Enter/Shift+Enter,
индексацию, file open, provider reorder/restore и один primary live chat.
Обнови system prompt, оба ТЗ, README, changelog, status/version. После полного
Ruff/mypy/pytest/compileall/build/pip-check и browser acceptance публикуй только
проверенные release-файлы без секретов.

## Production audit and Web UI 0.13.0 (2026-08-27)

Работай по `DEEP_CONTEXT_AGENT_0_13_PRODUCTION_PROMPT.md`, основному
`TECHNICAL_SPEC.md` и нормативному `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`.
Сначала зафиксируй baseline Ruff/mypy/pytest, затем выполняй изменения малыми
проверяемыми шагами и не объявляй production без фактических логов.

Удали любое определение write-authority по словам цели. Audit read-only по
умолчанию; мутации разрешены только доверенным `--allow-write` или эквивалентным
подтверждённым Web-полем. Сохраняй mode в identity/manifest/status/report и
блокируй все mutating tools в read-only независимо от текста модели.

До пачек создай точный file ledger с обязательным исключением dependency,
pytest/browser/report/cache/build/egg-info artifacts и поддержкой env include /
exclude. Сохраняй selected/excluded/reasons. До аудита извлеки устойчивые
requirement IDs из релевантного ТЗ, передавай пачке только подходящее
подмножество, сохраняй evidence matrix. Принимай findings только в ограниченной
структуре, проверяй пути и дедуплицируй.

Ограничь console summary 20 000 символами. Полный UTF-8 text/JSON report пиши
напрямую Python. После каждой пачки печатай flush progress; добавь model-free
`audit-status`, устойчивые pause/resume/cancel и сохранение pending после сбоя.

Реализуй optional FastAPI/Uvicorn Web UI с локальным TypeScript/static bundle,
REST/SSE для chat/context/audits/files/providers/settings. Используй те же
runtime и SQLite. Обязательны same-origin/CSRF/CSP, безопасные error DTO,
отсутствие ключей в клиенте, workspace path boundary, secret filtering,
optimistic hash concurrency и disabled-by-default delete. Remote bind допускай
только при explicit flag и auth token.

Обнови system prompt, ТЗ, README, env, changelog, version/status. Добавь offline
регрессии для 1 000 000 строк + 500 документов, режима записи, file selection,
requirements/findings/reports и Web security/API. Выполни Ruff check/format,
mypy, pytest, compileall, wheel/install/pip check, CLI/doctor, локальный Web
smoke и opt-in live provider smoke при настроенном ключе. После успешного
контура опубликуй только проверенные release-файлы без секретов.

## Production large-project audit 0.12.0 (2026-08-25)

Устрани переполнение agent graph при аудите проектов с 1 000 000+ строк и
сотнями документов. Не увеличивай prompt до размера корпуса: полный текст
остаётся в SQLite FTS5, а model call получает только найденные фрагменты либо
ограниченную файловую пачку. Замени hardcoded recursion limit валидируемой
настройкой. Создай отдельный SQLite audit manifest со стабильным run ID,
pending/in-progress/reviewed статусами, SHA-256 ledger и crash-safe resume.
Обрабатывай по 5–10 файлов в независимых graph invocations; отмечай файл
проверенным только по успешному текущему ToolMessage. При изменении SHA открывай
повторно только изменённый файл.
Считай фактические страницы отдельно от уникальных файлов. Ограничивай страницы
на файл; при исчерпании budget сохраняй `partial` и точное покрытие вместо
ложного полного review.

Создай кеш кратких SHA-bound summaries и безопасный Python AST-индекс имён,
qualified names, сигнатур, строк и docstrings без импорта кода. Добавь tools для
статуса manifest, summaries и symbol search. Доверенный batch manifest обязан
программно ограничивать filesystem exact paths и запрещать discovery/выход из
пачки. Добавь явную CLI-команду `audit` и автоматическую маршрутизацию широкого
`ask` в bounded batches.

Добавь `run_project_checks` без произвольного shell: только фиксированные Ruff,
pytest, mypy и compileall команды, argv-list с `shell=False`, timeout, output
limit, удаление ключей из child environment и redaction. Разрешай повтор одной
проверки только после подтверждённой мутации, ограничивай число циклов и
выполняй analyze → fix → test → repeat до успеха либо доказанного bounded stop.
Не меняй файлы вне workspace и не считай текст LLM доказательством чтения,
изменения или успешного теста.

Обнови global system prompt, ТЗ, README, env-пример, статус, changelog и версию.
Добавь регрессии на сотни файлов, существующий million-line corpus, resume,
SHA invalidation, AST, batch confinement, recursion setting, отсутствие shell и
утечки секретов. Выполни Ruff check/format, полный pytest, package/CLI/doctor,
live provider smoke и только после успешного контура публикуй commit/tag/main.

## Provider failover 0.11.0 (история)

Реализуй одновременную доступность нескольких LLM через строгую приоритетную failover-
цепочку. Сохрани `--provider`/`AGENT_PROVIDER` и добавь взаимоисключающий
`--providers`/`AGENT_PROVIDER_PRIORITY`. Канонизируй aliases, запрещай пустые
элементы и дубликаты, валидируй все ключи до запуска. Сначала повторяй только
текущий model call, затем переключайся на следующий provider; закрепляй
успешный fallback на текущий ход и восстанавливай приоритет на новом. Никогда
не повторяй уже завершённый tool из-за failover. Динамически отражай активную
identity в model prompt и `runtime_info`; ошибки всей цепочки санитизируй.
Сохрани одиночную обратную совместимость, добавь unit/tool-loop/CLI regression,
обнови ТЗ, README, env-пример, версию и историю, затем выполни полный контур.

Задай production-цепочку по умолчанию `glm,openai`. Основной провайдер должен
использовать `glm-5.3` через `https://api.z.ai/api/paas/v4`, резервный —
`gpt-5.6-sol` через официальный OpenAI API. Явные CLI/env-настройки обязаны
переопределять эти defaults без изменения механики failover.
В Chat Completions tool-calling контуре передавай для GPT-5.6 Sol
`reasoning_effort=none`; сохрани явное переопределение через
`OPENAI_REASONING_EFFORT` и закрепи совместимость live/regression-тестом.

Промпт интеграции версии 0.9.0 требовал подключить
Zhipu AI GLM-5.2 через существующую фабрику `ChatOpenAI`: добавь канонический
provider `zhipu`, CLI-алиас `glm`, безопасные `ZAI_*` настройки с `ZHIPU_*`
aliases, стандартный и переопределяемый Coding Plan endpoint, thinking и
проверку допустимой температуры. Не записывай API-ключ в код, логи или Git.
Обнови ТЗ, README, env-пример, версию и историю; выполни Ruff, pytest,
package/CLI doctor и разрешённый live-test только при наличии ключа.

Строгий обязательный Ozon-промпт версии 0.8.4 находится в
`ozon-strict-compliance-prompt.txt`. Его требования имеют приоритет для
доказательного read-only аудита Ozon с runtime-enforced tool contract.
`EVIDENCE_INTEGRITY_PROMPT.md`, `ACCEPTANCE_CORRECTNESS_PROMPT.md`,
`ACCEPTANCE_COMPLETION_PROMPT.md`, `ACCEPTANCE_RELIABILITY_PROMPT.md` и
`PRODUCTION_HARDENING_PROMPT.md` сохраняются как история версий 0.6.0–0.2.0.

## Краткий промпт Ozon hardening 0.7.0 (2026-08-23)

Не трактуй общий `/workspace/` как exact-file allowlist; сохрани строгую
изоляцию для явно названных файлов. До чтения исключай из индекса pytest,
coverage, browser-profile и generated/cache артефакты. Ограничь listing
источников 20 записями по умолчанию и 50 максимум. Сократи Ozon-промпт до
одного доказанного дефекта и 15 tool calls. Обнови global prompt, ТЗ, версию и
историю; выполни Ruff, pytest, package/doctor/live, опубликуй и только затем
повтори Ozon на чистых workspace/data/thread.

Patch 0.7.1: распознавай BOM до NUL/binary-проверки и потоково индексируй
UTF-16/UTF-32 документы. Закрепи regression-тестами и повтори весь локальный
контур, package/doctor live, публикацию и Ozon index на новой пустой БД.

Hardening 0.8.0: считай явные total/per-tool maximums жёсткой runtime-
политикой. После достижения лимита удаляй exhausted tools из model request и
подавляй stale provider-calls без лишнего audit event. Проверь русские и
английские формулировки unit/tool-loop тестами и повтори Ozon на новом thread.

Patch 0.8.1: при исчерпанном общем budget передавай model request без tools,
`tool_choice` и `parallel_tool_calls`. Закрепи OpenAI-совместимый empty-toolset
regression, повтори весь контур и Ozon на новой пустой БД/thread.

Patch 0.8.2: budget/exact middleware должны оборачивать sequential normalizer,
чтобы `parallel_tool_calls` вычислялся по окончательному toolset. Добавь
композиционный regression, повтори полный контур, публикацию и чистый Ozon-run.

Patch 0.8.3: разреши безопасный перенос строки между maximum-фразой и именем
tool в пределах одного предложения. Добавь regression из реального Ozon-
промпта, выполни контур, публикацию и финальный clean-DB run.

Patch 0.8.4: не используй evaluator-пути manifest как exact-read allowlist;
нулевыми per-tool budgets исключай web/planning/mutating tools до первого
model call; распознавай актуальный web-факт только по близким точным терминам.
Закрепи строгий Ozon prompt manifest-тестом, выполни Ruff/pytest/doctor live,
изолированный clean-DB аудит, проверку неизменности workspace и внешние тесты
Ozon.

## Краткий промпт evidence integrity 0.6.0 (2026-08-23)

Исправь три дефекта Deep Context Agent: финальная cardinality обязана дословно
совпадать со структурированным ToolMessage; manifest v2 обязан проверять
`min_results` и SHA-256 точного содержимого без утечки тела; явный exact-once
tool после первой попытки должен быть исключён из model request, а устаревший
повтор provider — не исполнен. Обнови глобальный prompt, ТЗ, canonical/restart
acceptance, версию и историю. Выполни Ruff, полный pytest, package/CLI/doctor,
live acceptance и новый-process restart test. Исправляй сбои и повторяй весь
контур до PASS, затем опубликуй проверенный commit.

## Исторический краткий промпт 0.5.0 (2026-08-23)

Исправь Deep Context Agent по runtime audit: ограничивай запрет чтения текущим
отрицательным предложением, не запрещай путь следующей положительной
инструкции; исключи недетерминированный `write_todos` из exact counts через
строгое `allowed_unlisted_tools`. Разделяй первичный FAIL и зависимые BLOCKED,
требуй последний post-delete read sentinel до финального ответа. Обнови
глобальный prompt, ТЗ, канонический manifest и историю. Выполни Ruff, полный
pytest, package/CLI/doctor и полный изолированный OpenAI acceptance; при сбое
исправляй и повторяй до runtime PASS, затем публикуй проверенный commit.

Ты — ведущий Python-инженер. Реализуй проект из `TECHNICAL_SPEC.md` полностью,
последовательно и без пропуска проверок.

Перед каждым этапом:

1. перечитай относящиеся к этапу разделы `TECHNICAL_SPEC.md` и этого файла;
2. сформулируй проверяемый результат этапа;
3. не расширяй права агента за пределы `AGENT_WORKSPACE`;
4. не читай и не выводи значения секретов.

После каждого этапа:

1. сопоставь сделанное с требованиями и критериями приёмки;
2. запиши статус, проверку и отклонения в `IMPLEMENTATION_STATUS.md`;
3. исправь обнаруженные несоответствия до перехода дальше.

Порядок выполнения:

1. Создай `pyproject.toml`, пакет `src/context_agent`, тесты и пример env-файла.
2. Реализуй типизированную конфигурацию и фабрику `ChatOpenAI` для `lmstudio`,
   `openai`, `yandex`, `deepseek`, `qwen`, `zhipu` и алиаса `glm`; реализуй
   общую приоритетную цепочку без дублирования клиентского кода.
3. Реализуй безопасное разрешение путей и SQLite FTS5-хранилище с потоковым
   чанкингом, пакетной записью, повторным индексированием, BM25-поиском, фильтром
   по источнику, чтением соседних чанков и архивом диалогов. Индекс должен
   масштабироваться до 1 000 000+ строк и сотен документов без загрузки корпуса
   в память.
4. Реализуй `search_context`, `web_search`, безопасное ограниченное чтение
   выбранной публичной веб-страницы, `make_directory` и безопасное удаление
   каталога/файла. Сетевой текст всегда помечай как недоверенный.
5. Создай Deep Agent с `CompositeBackend`, `FilesystemBackend(...,
   virtual_mode=True)`, checkpointer и системным промптом. Перед вызовом модели
   автоматически добавляй найденный контекст; после ответа архивируй диалог.
6. Добавь CLI-команды `chat`, `ask`, `audit`, `index`, `search`, `doctor` и понятные
   сообщения об ошибках конфигурации.
7. Напиши README с настройкой всех провайдеров, LM Studio tool calling,
   командами запуска, моделью безопасности и ограничениями FTS5.
8. Напиши тесты без реальных API, затем выполни Ruff, pytest, CLI smoke-test и
   один разрешённый live smoke-test при наличии ключа.
9. После ручного тестирования устрани регрессии безопасности и достоверности:
   убери общий `delete`, заблокируй root-delete и подмену внешнего пути, добавь
   точную runtime identity и проверяемый отчёт файловых операций.
10. После полного acceptance 0.3.0 введи единую per-turn политику повторов,
    абсолютный запрет чтения явно запрещённого файла и детерминированный
    manifest-аудитор exact counts/ordered evidence для версии 0.4.0.
11. После runtime acceptance 0.4.0 ограничь отрицательную инструкцию её
    предложением, разреши явно перечисленные недетерминированные planning-tools,
    отдели BLOCKED от первичных FAIL и гарантируй финальный post-delete read.
    Гарантия должна включать ограниченный runtime completion gate: после
    доказанной dependency он может продолжить только явно запрошенную safe
    cleanup/postcondition цепочку, но не root event. Исключай JSON manifest из
    классификации запросов актуальных web-фактов. После начатого моделью root
    event ограничивай provider именем следующего prose-разрешённого ordered
    tool, не синтезируя write/edit-содержимое.
12. После restart-аудита 0.5.0 обеспечь evidence integrity 0.6.0: сохраняй
    безопасные `result_count` и `content_sha256`, поддерживай manifest v2 с
    предикатами `min_results`/`content_sha256`, детерминированно исправляй
    cardinality финального ответа и не исполняй повторный call после явного
    контракта «ровно один раз» / `exactly once`.
13. После Ozon-аудита 0.6.0 обеспечь hardening 0.7.0: отличай общий корень от
    exact-file scope, отсекай generated/browser/cache пути до индексирования,
    ограничивай source listing и используй узкие ограниченные project-аудиты.
14. После clean Ozon-аудита 0.7.1 обеспечь hardening 0.8.0: исполняй явные
    total/per-tool tool-call budgets программно и не полагайся на дисциплину
    LLM при ограничении токенов и числа инструментов.
15. После live-аудита 0.8.3 обеспечь patch 0.8.4: отделяй evaluator JSON от
    prose filesystem scope, поддерживай запрет tool через нулевой budget и не
    включай web verification guard для локального аудита кода.
16. После Ozon `GraphRecursionError` реализуй 0.12.0: manifest-backed batches,
    SHA resume, summaries/AST, безопасные project checks, настраиваемую recursion
    depth и отдельные короткие graph turns; повтори corpus и live-регрессии.
17. После durable diagnostics 0.18.0 реализуй 0.19.0 по
    `DEEP_CONTEXT_AGENT_0_19_AUTOPILOT_ORCHESTRATOR_PROMPT.md`: одна широкая
    пользовательская задача становится persistent job; внутренний step limit
    вызывает автоматическое сохранение, уменьшение batch и новый worker thread,
    а не просьбу вручную делить задачу. Добавь CLI/Web lifecycle, crash resume,
    проверку/repair loop и повтор прежнего live-сценария.
18. После Web-регрессии 0.19.0 реализуй patch 0.19.1: Autopilot работает внутри
    основного чата и не имеет отдельной вкладки; explicit execution mode имеет
    приоритет над эвристикой, auto mode программно переключается в persistent
    job после `agent_step_limit`, SSE показывает job progress, а завершённый
    результат остаётся в истории исходного thread. Повтори точную проблемную
    русскую формулировку через настоящий `/api/chat`.
19. После production lease-инцидента 0.19.1 реализуй 0.20.0 по
    `DEEP_CONTEXT_AGENT_0_20_DURABLE_LEASE_ORCHESTRATION_PROMPT.md`: введи
    generation fencing, периодический heartbeat внутри долгой unit, явное
    состояние `interrupted`, bounded unit batch/recursion/deadline и раннюю
    маршрутизацию инженерных задач из auto chat. Мигрируй существующую SQLite,
    повтори прежний expired-lease сценарий и запрети stale worker коммитить или
    продолжать filesystem mutations.
20. После durable lease 0.20.0 реализуй Web chat 0.21.0 по
    `DEEP_CONTEXT_AGENT_0_21_CODEX_CHAT_MODES_PROMPT.md`: полностью замени старые
    роли на Agent/Ask/Plan/Debug/Multitask, обеспечь server-side read/write и
    execution policies, параллельные изолированные tasks и круговой estimate
    активного контекста с автоматической summarization Deep Agents.

Инженерные ограничения:

- Python 3.11+, PEP 8, Ruff line length 88;
- зависимости закрепляй совместимыми диапазонами, Deep Agents — ветка 0.7;
- секреты — только окружение/`.env.local`, пример содержит лишь пустые значения;
- не добавляй произвольный shell execution; project checks разрешены только
  через фиксированный runtime allowlist без пользовательских argv;
- внешние вызовы должны иметь timeout и понятные исключения;
- нельзя имитировать «миллион строк в окне модели»: полный корпус хранится в
  индексе, а агент обязан выполнять retrieval и при необходимости расширять
  найденный фрагмент соседними чанками;
- тесты должны проверять не только happy path, но и выход за корень, пустой
  запрос, повторное индексирование, отсутствующий ключ и повреждённый файл;
- защита root-delete должна проверяться через настоящий Deep Agent tool loop с
  контрольным файлом; одного unit-теста функции разрешения пути недостаточно;
- текст LLM не считается доказательством изменения файлов: источником истины
  служит фактический `ToolMessage`, отражённый в итоговом отчёте;
- ожидаемый отрицательный результат (`denied`, `error`, `not_found`) считается
  успешным acceptance-доказательством только при явном требовании manifest;
- один audit event не может подтверждать несколько ordered-требований, а
  forbidden event считается нарушением даже при заблокированном вызове;
- отрицательный глагол не распространяется на путь следующего положительного
  предложения, даже если оба предложения находятся на одной строке;
- planning-tool без функционального exact count допустим только при явном
  перечислении в `allowed_unlisted_tools` валидного manifest;
- текст LLM о числе результатов не может переопределить структурированный
  `result_count`; для прямого вопроса о количестве ответ формирует runtime;
- точное содержимое acceptance-файла доказывается SHA-256 фактических байтов
  внутри workspace без копирования содержимого в audit;
- явный exact-once контракт означает одну фактическую попытку: завершённый tool
  исключается из следующего model request, а устаревший повтор не исполняется;
- не объявляй этап завершённым без команды или теста, подтверждающего результат.
- не перекладывай на пользователя выбор batch size/max batches; это внутренняя
  адаптивная политика persistent autopilot job;
- `agent_step_limit` одной work unit не является терминальным результатом job:
  сначала должны быть исчерпаны безопасные split/retry стратегии;
- каждый повтор work unit использует новый namespaced thread ID, а завершённый
  прогресс и ToolMessage evidence коммитятся атомарно до следующего шага;
- allow-write, pause, resume и cancel берутся только из доверенных CLI/API полей,
  не из текста задачи.
- lease продлевается всё время исполнения audit/repair/verification unit;
  обновление только перед model call недостаточно;
- token без monotonically increasing generation не является достаточным fencing:
  каждая owner mutation проверяет оба значения;
- аварийно оставшаяся `running` unit сохраняется как `interrupted`, а не
  маскируется под `pending`; повтор использует новый sequence и worker thread;
- потерявший lease worker не выполняет новые mutating tools и не записывает
  terminal state; manifest/hash после recovery проверяется заново;
- heartbeat/deadline/progress являются server-side persisted execution state и
  передаются тем же SSE, а не хранятся только в браузере.
- chat mode policy исполняется backend-ом: Ask/Plan всегда read-only, Plan/Debug
  являются последовательными interactive turns, а Multitask использует разные
  child thread IDs и bounded server pool;
- никакое название режима не обходит workspace/path/destructive/provider/MCP
  policy; `Agent` означает полный доступ только к реально настроенным tools;
- круг заполнения контекста показывает bounded estimate, а не точный provider
  billing; автоматический summary не удаляет SQLite archive/retrieval memory.

Финальный результат: устанавливаемый Python-проект, рабочий CLI Deep Agent,
пройденные тесты и заполненный `IMPLEMENTATION_STATUS.md` с трассировкой всех
критериев `TECHNICAL_SPEC.md`.
