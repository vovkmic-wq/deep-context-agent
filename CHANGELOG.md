# История версий

Формат основан на Keep a Changelog. Проект использует семантическое
версионирование.

## [0.16.0] — 2026-08-29

### Добавлено

- Bounded stale-edit state machine: первый exact-match conflict
  открывает один свежий read того же path/version и один revised edit.
- Production-промпт 0.16.0, нормативные разделы в глобальном
  промпте, основном ТЗ и Web-ТЗ.

### Исправлено

- `String not found in file` больше не заходит в тупик из-за
  исчерпанного read budget: runtime принудительно выстраивает
  `read_file → edit_file` и останавливает второй conflict.
- Invalid `read_context_window` source/chunk/radius возвращает safe
  ToolMessage вместо необработанного `ValueError`; workspace path направляется
  к `read_file`.
- Hard budget в восемь context-window calls и скрытие `edit_file`
  после exhausted retry исключают live runaway/recursion loops.

### Безопасность

- Failed `old_string` не возвращается модели/Web; conflict evidence
  ограничено virtual path, error type и recovery action.
- Recovery не расширяет workspace, write/read prohibition, audit,
  duplicate mutation и secret policies; fuzzy/silent replacement запрещён.

### Проверено

- External-mutation integration, premature retry, second-conflict stop,
  normal third-read и runaway context-window regressions — PASS.
- Реальный GLM-5.3 на чистых workspace/SQLite/thread воспроизвёл
  expected stale conflict, recovery-read, revised edit и контрольное чтение — PASS.
- Финальные Ruff/mypy/pytest/TypeScript/package/live evidence указаны
  в `IMPLEMENTATION_STATUS.md`.

## [0.15.0] — 2026-08-29

### Добавлено

- Создание и немедленное включение process-local OpenAI-compatible
  профилей `custom-*` во вкладке провайдеров.
- Отдельные Files-действия `Назад / Back` по history и `Выше / Up`
  к parent, а также видимый loading/success/error для `Открыть`.

### Исправлено

- LM Studio Web doctor получает bounded каталог моделей и заменяет
  placeholder `local-model` на реальную загруженную чат-модель.
- Для loopback убрано ложное payment warning; теперь UI явно
  показывает `локально, без платы API`, а remote сохраняет
  opt-in предупреждение.
- Expected `AgentError` получил safe operator guidance в SSE вместо
  неинформативного «проверьте журнал».

### Безопасность

- Custom provider API запрещает browser secrets, URL credentials/query/
  fragment и remote HTTP; remote credential читается только из server env.
- `/models` ограничен 1 MiB, 100 записями и коротким timeout;
  API keys и raw SDK exceptions не сериализуются.

### Проверено

- Финальные Ruff/mypy/pytest/TypeScript/package/live/browser evidence указаны
  в `IMPLEMENTATION_STATUS.md`.

## [0.14.0] — 2026-08-29

### Добавлено

- Codex-подобный чат: список задач, persistent history, нижний composer,
  автоувеличение поля, остановка, выбор инженерной роли и сохраняемая локальная
  опция «Enter отправляет»; `Shift+Enter` всегда создаёт новую строку.
- На обзоре — режимы аудитора, кодера, тестировщика, reviewer, отладчика,
  рефакторинга, безопасности, архитектора и документации.
- Live-реестр провайдеров: атомарное изменение приоритета, добавление и удаление
  настроенных провайдеров и отдельная платная проверка доступности каждого.
- Полноценный файловый браузер и UTF-8 editor с SHA-256 optimistic concurrency;
  большие файлы открываются ограниченно и только для чтения.
- Русские/английские названия и пояснения audit glob, batch size и всех
  безопасных Web-настроек.

### Исправлено

- Virtual path `/workspace` больше не удваивается до
  `/workspace/workspace`; корень и вложенные каталоги открываются одинаково.
- Контекстная индексация показывает итоговые счётчики и безопасное объяснение
  ошибки вместо молчаливого завершения фоновой задачи.
- Кнопка «Открыть» и элементы файлов теперь выполняют реальную навигацию и
  открывают содержимое, а не выводят некликабельные карточки.

### Безопасность и совместимость

- Web UI по-прежнему использует те же `AgentRuntime`, provider failover,
  `ContextStore`, `ProjectAuditStore`, SQLite и workspace policy, что CLI.
- Инженерная роль является только ограниченной инструкцией и не предоставляет
  право записи. Секреты, raw paths вне workspace и ключи в браузер не передаются.
- Сохранены same-origin, CSRF, CSP, remote bearer gate, secret/symlink guards и
  disabled-by-default delete.

### Проверено

- Ruff lint/format, mypy 24 source files, compileall и pip check: PASS;
  pytest — 184 passed, 1 planned Windows symlink skip.
- TypeScript check/build/bundle, wheel static inspection, local Uvicorn и
  browser E2E desktop/mobile: PASS.
- Primary `zhipu/glm-5.3` через новый Codex-like chat ответил `ГОТОВ`;
  index, file preview и provider reorder/restore подтверждены в браузере.

## [0.13.0] — 2026-08-27

### Добавлено

- Явный безопасный режим пакетного аудита: read-only по умолчанию,
  `--allow-write` как единственная CLI-авторизация, persisted mode в identity,
  status, prompt и report.
- Детерминированный file selection с фильтрацией dependency,
  pytest/browser/report/cache/build/coverage и `*.egg-info`, env include/exclude
  и статистикой причин исключения.
- Межпакетный registry требований с устойчивыми `REQ-*`, source hash,
  релевантной batch-выборкой и evidence matrix; структурированные,
  валидируемые и дедуплицированные findings.
- Прямой UTF-8 export полного text/JSON report, компактный console summary,
  flush `AUDIT_PROGRESS` и model-free `audit-status --json`.
- Optional FastAPI/Uvicorn Web UI: overview, chat/SSE, context, audits,
  workspace files, providers и safe settings поверх общих runtime/SQLite.
- Web security boundary: same-origin, CSRF, CSP, bearer gate для remote,
  secret path filtering, optimistic SHA-256 writes и disabled-by-default delete.
- Offline regressions корпуса 1 000 000 строк + 500 документов, режима записи,
  отбора artifacts, requirements/findings/reports и Web API/security.

### Исправлено

- Слова `fix`, `исправь`, `update` в цели больше не повышают права аудита.
- Большие LLM batch-ответы больше не создают мегабайтный PowerShell output;
  полный результат сохраняется в файл без перекодирования через pipeline.
- Interrupted/paused/cancelled run сохраняет manifest и pending-файлы для
  безопасного resume.

### Документация

- Синхронизированы глобальный system prompt, управляющий prompt, основное ТЗ,
  Web-ТЗ, README, env-пример, статус реализации и версия пакета.

### Проверено

- Ruff lint/format, mypy, compileall, `pip check`: PASS; pytest — 181 passed,
  1 planned Windows symlink skip.
- TypeScript check/build/bundle test, wheel static inspection и реальный local
  Uvicorn HTTP smoke: PASS.
- Primary `zhipu/glm-5.3` `doctor --live`: OK; clean-DB read-only live audit
  проверил 2/2 файла, сохранил text/JSON report и не изменил workspace.

## [0.12.0] — 2026-08-25

### Добавлено

- Постоянный SQLite-манифест широкого аудита со стабильным run ID,
  crash-safe resume, bounded batches и статусами каждого файла.
- SHA-256 file ledger, кеш кратких сводок и Python AST-индекс определений без
  импорта или исполнения кода проекта.
- Автоматическая маршрутизация полного project audit в независимые graph turns
  и CLI-команда `audit` с жёстким `--max-batches`.
- Tools `project_audit_status`, `get_project_file_summary`,
  `search_python_symbols` и фиксированный `run_project_checks`.
- Настройки recursion limit, размера/числа пачек, timeout и output limit
  проверок с программной валидацией границ.

### Изменено

- Hardcoded `recursion_limit=100` заменён `AGENT_RECURSION_LIMIT`.
- Широкий аудит читает только 1–25 manifest-файлов (по умолчанию 8) за graph invocation и
  фиксирует completion исключительно по успешным file ToolMessages.
- Повтор project checks разрешён после подтверждённой filesystem mutation;
  идентичный повтор без изменения и шестой цикл в одном ходе блокируются.
- Manifest раздельно считает уникальные reviewed-файлы и все успешные
  `file_reads`, включая разные страницы длинного файла.
- Bounded page budget на файл (по умолчанию 4) позволяет пагинацию внутри
  batch; исчерпание даёт `partial`/`complete_with_partial`, а не ложный review.
- Идентичный audit page-read блокируется по точным args; новый offset/limit
  остаётся доступен и отдельно учитывается в `file_reads`.
- Global prompt, управляющий prompt, ТЗ, README, env-пример и runtime metadata
  синхронизированы с двухуровневой моделью большого контекста.

### Безопасность

- Batch middleware запрещает доступ за пределы выделенных manifest paths,
  discovery tools и изменение набора путей во время пачки.
- Project checks не принимают shell/argv, используют `shell=False`, очищают
  child environment от ключей/токенов/паролей, редактируют и ограничивают вывод.
- CLI принудительно использует UTF-8 для stdout/stderr, поэтому Unicode-ответы
  провайдера не падают с Windows `charmap` при pipe/redirect.
- Audit index пропускает secret env-файлы, binary/NUL-файлы, symlinks,
  виртуальные окружения, generated и cache directories.

### Тесты

- Добавлены регрессии manifest resume, SHA invalidation, summary cache,
  AST-qualified symbols, 300 документов, fixed check allowlist, redaction,
  batch routing и повтор проверки только после мутации.
- Сохранены регрессии поиска начала и конца файла из 1 000 001 строк и
  пагинации сотен документов в FTS5.
- Финальный контур: Ruff check/format, mypy по 22 source/test files, compileall,
  168 passed и 1 ожидаемый Windows symlink skip; wheel 0.12.0 собран.
- GLM-5.3 live: doctor, полный agent turn и clean-DB batch audit 2/2 прошли;
  `file_reads=4`, `partial=0`, Unicode stdout подтверждён.

## [0.11.0] — 2026-08-24

### Изменено

- Production-цепочка по умолчанию теперь `glm,openai`: основная модель
  `glm-5.3`, резервная — `gpt-5.6-sol`.
- Международный endpoint Z.AI по умолчанию установлен в
  `https://api.z.ai/api/paas/v4`.
- Для совместимости GPT-5.6 Sol с function tools через Chat Completions
  установлен `OPENAI_REASONING_EFFORT=none` с возможностью переопределения.
- CLI help, `.env.example`, README, техническое задание и управляющий промпт
  синхронизированы с новой цепочкой.

### Тесты

- Добавлена регрессия точного порядка и моделей default failover chain.
- Добавлены регрессии default/override настройки OpenAI reasoning effort.

## [0.10.0] — 2026-08-24

### Добавлено

- Одновременная конфигурация нескольких LLM через `--providers` или
  `AGENT_PROVIDER_PRIORITY` с приоритетом слева направо.
- Автоматический failover model calls с per-turn stickiness и восстановлением
  исходного приоритета на следующем пользовательском ходе.
- Динамическая активная identity и безопасная диагностика всей цепочки через
  `runtime_info` и `doctor --live`.
- Регрессии конфигурации, CLI, успешного fallback, отсутствия tool replay и
  санитизации полной ошибки цепочки.

### Безопасность

- Уже выполненные tools не повторяются при смене LLM; наружу не передаются
  ответы API и иные потенциально секретные детали исключений провайдеров.

## [0.9.0] — 2026-08-24

### Добавлено

- Провайдер Zhipu AI для `glm-5.2` через OpenAI-compatible API с CLI-именами
  `zhipu` и `glm`.
- Настройки `ZAI_API_KEY`, `ZAI_MODEL`, `ZAI_BASE_URL` и совместимые aliases
  `ZHIPU_*`; endpoint GLM Coding Plan можно задать без изменения кода.
- Явное включение thinking и ранняя проверка допустимой температуры GLM.
- Provider-aware исключение недокументированного `parallel_tool_calls` для
  Zhipu с сохранением программной последовательности tools.
- Unit-тесты конфигурации, алиасов, фабрики модели и ошибок настройки GLM.

## [0.8.4] — 2026-08-24

### Добавлено

- Строгий read-only Ozon compliance prompt с 20-событийным runtime manifest,
  точными source/test-чтениями и нулевыми бюджетами запрещённых tools.
- Регрессии для нулевого tool budget, evaluator-only manifest paths и ложного
  current-web guard при локальном аудите проекта.

### Исправлено

- Пути из JSON manifest больше не блокируют чтение релевантных файлов широкого
  project-аудита; prose exact-file scope по-прежнему защищён.
- Русские подстроки в словах `совпадать` и `полноценный` больше не принимаются
  за запрос текущей даты или цены.
- Описание `read_file` различает точный файловый запрос и явно запрошенный
  project discovery/audit.

## [0.8.3] — 2026-08-23

### Исправлено

- Per-tool budget parser сохраняет связь между maximum-фразой и tool через
  перенос строки внутри одного предложения, как в Ozon prompt.
- Regression использует точную двухстрочную формулировку «не более двух узких
  `search_context`».

## [0.8.2] — 2026-08-23

### Исправлено

- Budget/exact middleware теперь выполняются перед sequential normalizer, и
  `parallel_tool_calls` вычисляется уже после окончательного сужения toolset.
- Композиционный regression воспроизводит исчерпанный total budget и проверяет
  отсутствие `tools`, `tool_choice` и `parallel_tool_calls` у model handler.

## [0.8.1] — 2026-08-23

### Исправлено

- Empty-toolset model request после исчерпания явного budget больше не содержит
  `parallel_tool_calls` и `tool_choice`, которые OpenAI запрещает без `tools`.
- Добавлен regression-тест model settings для финального budget-exhausted шага.

## [0.8.0] — 2026-08-23

### Добавлено

- Парсер явных русских и английских total/per-tool ограничений tool calls,
  включая числительные словами.
- Model middleware, скрывающий exhausted tools и подавляющий устаревшие вызовы
  provider без создания лишнего audit event.
- Tool-loop regression-тесты для per-tool и общего бюджетов.

### Исправлено

- Ozon-аудит больше не может выполнить четыре `search_context` при явном
  максимуме два или превысить общий максимум функциональных tool calls.

## [0.7.1] — 2026-08-23

### Исправлено

- UTF-16LE/BE и UTF-32LE/BE текстовые документы с BOM больше не определяются
  как binary из-за NUL-байтов и индексируются потоково.
- Добавлены regression-тесты полнотекстового поиска по UTF-16 и UTF-32 файлам.

## [0.7.0] — 2026-08-23

### Добавлено

- Регрессионный tool-loop для project-root scope: общее упоминание
  `/workspace/` не блокирует чтение найденного `pyproject.toml`.
- Фильтрация generated/cache/coverage и browser-profile путей до рекурсивного
  обхода индексатора с регистронезависимым сопоставлением.
- Ограниченный Ozon improvement prompt: один дефект, не более 15 tool calls,
  без широкого glob и с небольшими retrieval-выборками.

### Исправлено

- Общий путь `/workspace/` больше не превращается в exact-file allowlist,
  из-за которого чтение любого дочернего файла получало `denied`.
- В FTS5 больше не попадают `.pytest-*`, `.coverage*`, Playwright-отчёты и
  профили браузеров, и они не расходуют retrieval/token budget.
- `list_context_sources` возвращает 20 записей по умолчанию и программно
  ограничен 50 записями, даже если модель запросила больше.

## [0.6.0] — 2026-08-23

### Добавлено

- Структурированные безопасные audit-поля `result_count` и
  `content_sha256`; тела файлов и найденные фрагменты в audit не копируются.
- Acceptance manifest v2 с required-предикатами `min_results` и
  `content_sha256` при сохранении совместимости manifest v1.
- Детерминированный cardinality guard: прямой ответ о числе результатов
  формируется из ToolMessage evidence, а не из текста LLM.
- Exact-once middleware для явных инструкций `ровно один раз`/`exactly once`:
  завершённый tool исключается из следующего model request, устаревший повтор
  provider не исполняется.
- Отдельный `restart-acceptance-prompt.txt` с exact count 1 и
  `min_results=1` для межпроцессной проверки SQLite-памяти.

### Исправлено

- Финальный ответ больше не может сообщить `0` при фактическом ненулевом
  результате `search_context`: runtime заменяет count авторитетным значением.
- Запрос exact-once больше не создаёт второй policy-denied audit event после
  уже полученного ToolMessage.
- Canonical acceptance доказывает точные байты result/sentinel через SHA-256,
  а не только успешный status чтения или записи.

## [0.5.0] — 2026-08-23

### Добавлено

- Sentence-scoped разбор запретов чтения: путь следующего положительного
  предложения не наследует отрицательную инструкцию.
- Manifest-поле `allowed_unlisted_tools` для недетерминированных служебных
  planning-вызовов с жёсткой валидацией tools, дубликатов и пересечений.
- Отдельный статус BLOCKED для required events, зависимых от первичного FAIL.
- Ограниченный runtime completion gate: после доказанной зависимости он
  преобразует преждевременный или расходящийся ответ в один безопасный cleanup
  или post-delete tool call, не начиная root event и не расширяя filesystem
  scope. Путь должен быть явно разрешён prose вне JSON manifest.
- После начатого моделью manifest-сценария provider получает имя следующего
  dependency-ready tool через `tool_choice`; это предотвращает перестановки и
  лишние calls без синтеза write/edit-содержимого. Естественное файловое
  намерение авторизуется только вместе с точным prose-путём.
- Dependency-ready `read_file` получает точный prose-разрешённый event target;
  это устраняет перестановку одинаковых read tools без чтения decoy/unrelated.
- `remove_path` получает точный ordered target и явный recursive-флаг; root
  остаётся допустим только как гарантированно отклоняемая negative-проверка.
- Sentence-scoped запрет filesystem mutation имеет приоритет над manifest;
  положительное разрешение требует mutation intent рядом с точным prose-путём.

### Исправлено

- `result.txt` больше не блокируется из-за упоминания после запрещённого
  `decoy.txt` в следующем предложении той же строки.
- Повторные `write_todos` отображаются в counts, но не искажают functional
  verdict канонического manifest.
- Один ранний missing event больше не раздувается в цепочку независимых FAIL.
- Глобальный и канонический prompts запрещают заявлять найденный context result
  при фактическом `0 result(s)` и завершать cleanup без последнего ToolMessage.
- Acceptance manifest исключён из классификации актуальных web-фактов, поэтому
  его поле `version` не создаёт ложный web FAIL для проверки локальной памяти.

## [0.4.0] — 2026-08-23

### Добавлено

- Единая per-turn политика повторов для mutating, runtime, context, listing и
  web-tools, а также ограничение чтений по версии состояния пути.
- Абсолютный запрет `read_file` для путей, явно помеченных пользователем «не
  читай/не открывай/не показывай» или английским эквивалентом.
- Ограниченный JSON acceptance manifest с exact counts, ordered/forbidden
  events, ожидаемыми negative statuses и внешними pending-проверками.
- Программный отчёт acceptance с per-tool/per-status counts и причинами FAIL.

### Исправлено

- Повторные PyPI, private URL и context-list вызовы больше не исполняются в
  одном ходе; лишние попытки получают `denied` в audit.
- `LLM_OBSERVATION_ONLY` больше не может скрыть пропущенный cleanup,
  post-delete read, удаление sentinel или запрещённое чтение decoy.
- `denied`, `error` и `not_found` не считаются безусловным провалом: manifest
  оценивает их относительно ожидаемого результата конкретного шага.

## [0.3.0] — 2026-08-22

### Добавлено

- Последовательное исполнение зависимых tool calls с принудительным
  `parallel_tool_calls=false` и runtime-ограничением одного call на model step.
- Защита от повторной идентичной файловой мутации в одном пользовательском ходе.
- Редактирование пользовательских маркеров `DO_NOT_SHOW` в ответе и audit.
- Детерминированное получение текущей версии пакета из официального PyPI JSON.
- Acceptance-регрессии для последовательности, redaction, recursive cleanup и
  достоверности итогового статуса.

### Изменено

- Удаление явно названного подкаталога целиком выполняется одним рекурсивным
  вызовом, корень workspace остаётся безусловно защищён.
- LLM-самооценка теста всегда сопровождается внешним runtime-вердиктом.
- `/paste ТЕКСТ` сохраняет текст после команды как первую строку одного turn.

## [0.2.0] — 2026-08-22

### Добавлено

- Многострочный ввод `/paste`, `ask --file` и stdin.
- Model-call retry без повтора graph, rollback неуспешного checkpoint turn.
- Расширенный безопасный audit filesystem, context, runtime и web tools.
- Проверка актуальных веб-фактов через успешный page fetch текущего хода.

### Исправлено

- Удалён общий filesystem `delete`; заблокированы root-delete, внешние пути и
  создание placeholder-файлов.
- Точные чтения ограничены явно указанными файлами.

## [0.1.0] — 2026-08-20

### Добавлено

- Первый CLI Deep Context Agent на Python с LM Studio, OpenAI, YandexGPT,
  DeepSeek и Qwen.
- Постоянный SQLite FTS5-контекст, потоковое индексирование больших корпусов,
  поиск соседних чанков и LangGraph checkpointer.
- Ограниченная виртуальная файловая система и безопасный публичный web search.
