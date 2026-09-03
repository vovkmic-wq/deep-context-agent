# Deep Context Agent 0.22.0: dynamic models, hybrid retrieval and bounded scans

## Статус документа

Это нормативный production-промпт следующего этапа. Выполняй его совместно с
`TECHNICAL_SPEC.md`, `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md` и всеми ранее
принятыми security/evidence-инвариантами. Не объявляй этап завершённым, пока
каждый критерий приёмки не подтверждён свежим автоматическим или live-логом.

## Цель

Сделать выбор LLM-модели управляемым прямо из закреплённого заголовка чата,
добавить полностью локальную гибридную память `FTS5/BM25 + FastEmbed/Qdrant`,
исключить необ bounded обходы больших деревьев и сделать частичный результат,
прогресс, продолжение по курсору и итог каждой задачи наблюдаемыми.

Приоритет реализации неизменяем:

1. динамический выбор модели в закреплённом заголовке чата;
2. гибридная векторная память на FastEmbed/ONNX и Qdrant;
3. единая политика исключений и bounded `glob`/`grep`/index/audit;
4. понятная пользователю диагностика и полный безопасный журнал операций.

## Неприкосновенные инварианты

1. `/workspace/` остаётся единственной файловой областью агента. Новая модель,
   retrieval backend или Web API не расширяют filesystem/MCP/shell/destructive
   права.
2. FTS5 остаётся доступным и авторитетным лексическим индексом. Отказ FastEmbed
   или Qdrant переводит retrieval в явно обозначенный `lexical-only`, но не
   ломает чат, аудит и чтение файлов.
3. Документы не отправляются во внешний embedding API. Скрытый cloud fallback
   запрещён. Любая будущая внешняя передача требует отдельного opt-in и нового
   требования безопасности.
4. API-ключи, заголовки авторизации, raw provider payload, физические пути,
   тела секретных файлов и полные запросы вне установленного logging mode не
   попадают в browser, SSE, context index и обычный process log.
5. Провайдер и модель фиксируются неизменяемым snapshot в начале задачи.
   Переключение UI влияет только на следующий turn или безопасную границу
   Autopilot work unit и никогда не меняет уже исполняющийся model call.
6. Один и тот же центральный exclusion policy используется всеми широкими
   обходами. Локальная копия списка исключений в отдельном tool запрещена.
7. `partial`, `cancelled`, `timed_out` и `failed` не маскируются под `complete`.
   Текст LLM не может повысить terminal status или полноту покрытия.

## Фаза 0. Базовая фиксация и проектирование миграции

1. Сохрани текущие версии Python/Web bundle, schema versions, число тестов,
   Ruff/mypy/pytest/TypeScript/package результаты и один воспроизводимый лог с
   timeout широкого `glob` либо `grep`.
2. Найди существующие реализации provider registry, Chat DTO, context FTS5,
   project audit ledger, diagnostics/task events и artifact filtering. Расширяй
   их; не создавай параллельные обходные реестры или вторую Web-бизнес-логику.
3. Определи версионируемые DTO, схему Qdrant collection, формат opaque cursor и
   обратимо мигрируемые SQLite-поля до изменения runtime.
4. Составь краткую матрицу «требование → код → тест → live evidence» и обновляй
   её после каждой фазы.

## Фаза 1. Динамический каталог и выбор модели

1. Добавь серверный каталог моделей для каждого настроенного OpenAI-compatible
   провайдера. Каталог получает доступные ID через bounded `/models`, имеет
   timeout, размерный лимит, TTL-cache, ручной refresh и безопасные error codes.
2. Не показывай как chat-модели embedding, rerank, moderation, image, audio,
   transcription и заведомо deprecated entries. Недостаточные метаданные
   `/models` дополняй версионируемым compatibility manifest, а неизвестную
   модель помечай `unverified`, не выдавая за tool-compatible.
3. API должен поддерживать:
   - получение paginated каталога конкретного провайдера;
   - `auto` либо явный `provider + model` для следующего chat turn;
   - сохранение предпочтения на уровне thread;
   - фактические provider/model/fallback в terminal result и истории;
   - атомарную замену приоритета без воздействия на активные задачи.
4. Backend валидирует доступность модели и совместимость с chat/tool calling.
   Произвольный model ID из browser не передаётся провайдеру без allowlist или
   успешной server-side проверки.
5. Закрепи заголовок внутри панели чата под общей topbar. В нём размести thread,
   режим, execution, провайдер, модель, состояние подключения и индикатор
   контекста. История прокручивается независимо; мобильный вид сворачивает
   вторичные настройки без горизонтального переполнения.
6. Дай понятные presets `Авто`, `Качество`, `Баланс`, `Экономия`, `Локально` и
   раскрываемый поиск по всем совместимым моделям. Не называй каталог API
   «всеми моделями ChatGPT».
7. При fallback рядом с ответом покажи фактическую цепочку, например
   `zhipu/glm-5.3 → openai/gpt-5.6-terra`, без raw exception и API payload.
8. Не спрашивай подтверждение оплаты для loopback/local provider. Для удалённой
   live-проверки сохрани явное предупреждение, но обычный уже настроенный chat
   turn не должен получать повторяющееся модальное окно.

## Фаза 2. Гибридная память FastEmbed/Qdrant

1. Добавь abstraction `EmbeddingProvider` и `VectorStore`; первая production-
   реализация — FastEmbed/ONNX на CPU, первая vector storage — локальный Qdrant
   под `AGENT_DATA_DIR`. Чат-модель и embedding-модель конфигурируются отдельно.
2. Выбери и закрепи конкретную FastEmbed-compatible многоязычную модель только
   после локального benchmark на русском тексте, Python/TypeScript-коде,
   именах/артикулах и перефразированных запросах. Зафиксируй ID, revision,
   лицензию, размерность, distance и обязательные query/document prefixes.
3. Модель загружай лениво при первой vector index/search/doctor операции.
   Запуск CLI/Web, FTS5-поиск и provider doctor не должны ждать её загрузки.
4. Реализуй adaptive CPU batching. Начинай с bounded configured maximum,
   измеряй фактический результат; при `MemoryError`/OOM уменьши пачку вдвое до
   единицы. Не повторяй уже committed batch и не объявляй OOM обычным skip.
5. Используй существующие chunk/source IDs и SHA-256. Vector record содержит
   source, chunk index, content hash, embedding signature и безопасные metadata.
   Не дублируй полные тексты в нескольких БД без документированной причины.
6. Инкрементальная индексация создаёт vectors только для новых/изменённых
   chunks, удаляет vectors исчезнувших sources и не трогает неизменённые.
7. Смена модели, revision, размерности, normalization, prefix или distance
   меняет embedding signature и запускает управляемую resumable reindex.
   Строй новую collection отдельно и переключай alias только после полного
   завершения; прерванный индекс не становится активным.
8. Hybrid search параллельно получает bounded lexical и vector candidates,
   дедуплицирует `source + chunk_index`, объединяет ранги через детерминированный
   RRF либо равноценный алгоритм и возвращает top-k с типом score и источником.
   Rerank допускается отдельной опцией, но не является скрытым LLM-вызовом.
9. Добавь filters по source/kind/path/thread/project и diversity по источникам,
   чтобы один длинный файл не вытеснял все остальные документы.
10. При недоступности FastEmbed/Qdrant верни `retrieval_mode=lexical-only`,
    safe reason и метрику degradation. Ни один документ не отправляй наружу.
11. Минимальная конфигурация:

    ```env
    AGENT_RETRIEVAL_MODE=hybrid
    AGENT_EMBEDDING_PROVIDER=fastembed
    AGENT_EMBEDDING_DEVICE=cpu
    AGENT_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
    AGENT_EMBEDDING_BATCH_SIZE=0
    AGENT_VECTOR_STORE=qdrant
    AGENT_EXTERNAL_EMBEDDING_FALLBACK=false
    ```

    Все значения валидируются до работы соответствующего компонента; secrets
    отсутствуют, физический vector path не возвращается browser.

## Фаза 3. Единая политика исключений и bounded traversal

1. Создай один центральный path/artifact policy для `glob`, `grep`, context
   indexing, project audit, symbol indexing и Web file discovery. Все клиенты
   вызывают один API и получают одинаковую причину исключения.
2. Защищённый минимум применяется регистронезависимо к path segments и включает:
   `.git`, `.venv`, `venv`, `node_modules`, `dist`, `build`, `__pycache__`,
   `.cache`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, coverage/browser-
   artifacts, временные каталоги/файлы, логи, backup-копии, базы/WAL/SHM и
   exports самого агента. Существующие более строгие secret/profile правила
   сохраняются.
3. Дополнительные пользовательские include/exclude накладываются поверх общего
   policy. Include не может вернуть secret, traversal, agent database или
   путь за workspace. Точное явно разрешённое `read_file` остаётся отдельной
   операцией и не блокируется только потому, что файл исключён из широкого
   discovery, если иной security rule его не запрещает.
4. `glob`, `grep`, source listing, indexing и audit discovery возвращают
   bounded page, opaque `next_cursor`, `complete`, `partial`, `reason`, а также
   `matched/scanned/excluded/error` counts. Max page size действует независимо
   от аргумента модели.
5. Cursor связывается с root, нормализованными filters, sort order и snapshot
   identity. Повтор страницы не даёт дубликатов; несовместимый/stale cursor
   отклоняется стабильным кодом либо явно перезапускает snapshot по контракту.
6. Timeout возвращает уже committed page как `partial=true` и продолжимый
   cursor. Следующий вызов продолжает после последней подтверждённой позиции,
   а не повторяет полный обход.
7. Когда пользователь назвал точный известный файл и просит прочитать его,
   используй `read_file` с pagination. Не запускай широкий `grep` по дереву.
   `grep` допустим для поиска pattern/неизвестного расположения либо явно
   ограниченного набора путей. Закрепи это tool descriptions, system prompt и
   runtime-policy тестами, не полагаясь только на послушание модели.
8. Не материализуй тысячи путей в RAM, ToolMessage, prompt или SSE. Discovery
   является потоковым/итеративным и проверяет cancellation/deadline между
   страницами.

## Фаза 4. Прогресс, частичный результат и продолжение

1. Все длительные scans публикуют throttled SSE progress с полями `discovered`,
   `scanned`, `matched`, `indexed`, `unchanged`, `skipped`, `excluded`,
   `chunks`, `errors`, `elapsed_ms`, `cursor_available` и phase.
2. Контекстная индексация показывает итоговые `indexed / unchanged / skipped`
   при каждом повторном запуске. Нулевые значения отображаются явно.
3. UI визуально различает `complete`, `partial`, `paused`, `cancelled`,
   `timed_out`, `failed` и `degraded`. Частичный список получает заметную метку,
   причину, counts и кнопку `Продолжить`, если cursor доступен.
4. Resume использует persisted server cursor/job state. Browser localStorage не
   является источником истины. Reload/reconnect получает тот же status и не
   начинает новую операцию автоматически.
5. Отмена останавливает дальнейший обход, сохраняет подтверждённые counts и
   cursor; она не удаляет уже корректно проиндексированные chunks/vectors.

## Фаза 5. Журнал и пользовательская диагностика

1. Для каждой scan/index/audit/provider/chat task сохрани terminal event:
   `task/request/thread/job ID`, operation, terminal status, provider/model
   snapshot, fallback chain, start/end/duration, counts найденных/просмотренных/
   исключённых файлов, partial reason, cursor presence и safe error code.
2. Terminal event записывается и при exception, timeout, cancellation, process
   recovery и lexical-only degradation. Он переживает restart и одинаково
   читается task status, SSE replay и operator diagnostics.
3. Structured JSONL/SQLite используют UTC и correlation IDs. Секреты и тела
   документов проходят существующую redaction/retention policy.
4. На «Обзоре» замени raw JSON понятным `Система готова / Ограниченный режим /
   Требуется внимание` и короткими действиями. Полный блок перенеси в
   `Настройки → Дополнительно → Диагностика`, сохрани API и кнопку копирования
   безопасного отчёта.
5. HTTP `200`/`202` и открытый SSE не считаются успешным terminal result.
   Browser сообщает успех только после авторитетного `completed` с counts.

## Фаза 6. Миграции, совместимость и документация

1. Добавь идемпотентные schema migrations и crash recovery. Повторный запуск на
   БД 0.21.0 не теряет FTS5, checkpoints, diagnostics, audit или autopilot jobs.
2. Сохрани совместимость существующих CLI и Web endpoints. Новые поля должны
   иметь безопасные defaults; удаление/переименование публичного поля требует
   версионированного API или явной миграции.
3. Обнови system/global prompts, основное и Web-ТЗ, README, `.env.example`,
   OpenAPI/CLI help, changelog, status, версии Python/Web и production bundle.
4. Документируй резервное копирование FTS5/Qdrant, rebuild vectors, смену
   embedding-модели, offline-only гарантию и восстановление после partial run.

## Фаза 7. Обязательные тесты

1. Unit: единый exclusion policy возвращает одинаковые решения и причины для
   glob/grep/index/audit; Windows case/Unicode/symlink/traversal regressions.
2. Pagination: стабильный порядок, bounded page, отсутствие дубликатов и
   пропусков, stale/tampered cursor, timeout → partial → resume.
3. Tool routing: точный файл читается paginated `read_file`; широкий `grep` не
   вызывается. Неизвестный путь и явный pattern сохраняют допустимый grep flow.
4. Model catalog: timeout, oversized/invalid payload, cache/refresh, filtering,
   unavailable model, tool compatibility, per-thread preference, in-flight
   snapshot, fallback и отсутствие секретов.
5. Vector: русский paraphrase, точный code symbol, hybrid fusion, metadata
   filters, source diversity, SHA incremental update/delete, dimension/model
   mismatch и atomic reindex switch.
6. Degradation: FastEmbed load error, Qdrant corruption/lock, MemoryError batch
   reduction и гарантированный FTS5 fallback без внешней сети.
7. Progress/diagnostics: terminal event и authoritative counts для complete,
   partial, failed, cancelled, restart/replay; HTTP/SSE transport success не
   повышает task status.
8. Frontend/E2E: sticky header, keyboard/mobile behavior, live model change,
   partial badge/continue, repeated indexing counts и simplified diagnostics.
9. Large corpus: детерминированный synthetic fixture не менее 1 000 000 строк,
   сотни документов, excluded artifact tree и semantic needle/paraphrase.
   Зафиксируй peak RAM, index duration, search latency и отсутствие передачи
   всего корпуса в один prompt/browser response.
10. Quality gates:

    ```powershell
    .\.venv\Scripts\python.exe -m ruff check --no-cache .
    .\.venv\Scripts\python.exe -m ruff format --check --no-cache .
    .\.venv\Scripts\python.exe -m mypy src
    .\.venv\Scripts\python.exe -m pytest -ra -p no:cacheprovider
    pnpm --dir webui exec tsc --noEmit
    pnpm --dir webui test
    ```

11. Live: на чистых `AGENT_DATA_DIR`, workspace и thread выполнить CPU
    FastEmbed/Qdrant index/search, повторную индексацию, semantic paraphrase,
    forced embedding failure, timeout/resume scan, model switch между двумя
    доступными chat-моделями и fallback. Платный provider live-test запускается
    только при существующем opt-in; external embedding request запрещён.

## Критерии production-приёмки

- модель следующего turn меняется из sticky chat header и backend показывает
  фактически использованные provider/model/fallback;
- активная задача сохраняет исходный immutable model snapshot;
- русский semantic query находит релевантный chunk без общих ключевых слов, а
  точный symbol/артикул остаётся находимым через FTS5;
- отключённый FastEmbed/Qdrant даёт рабочий и явно обозначенный lexical-only;
- все широкие обходы используют один exclusion policy и bounded cursor pages;
- прежний Ozon-сценарий с 4558 matches возвращает page/partial/resume, а не
  немой timeout или огромный ToolMessage;
- точный известный файл читается `read_file`, не широким tree grep;
- UI показывает partial и authoritative `indexed/unchanged/skipped`;
- terminal journal содержит provider/model, duration и file counts при любом
  завершении, но не содержит ключей или тел защищённых документов;
- миллионный корпус проходит offline/performance acceptance;
- все quality, package, browser и live проверки имеют свежие успешные логи;
- только после этого обновляется release version и выполняется Git publication.
