# Управляющий промпт реализации

Последний production-этап описан промптом 0.21.0. Версия кода изменяется только
после полного offline/live/package acceptance.

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

Закрепи external-mutation integration, bounded negative и обычный
third-read regression. Повтори исходную ситуацию в live runtime и на
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
