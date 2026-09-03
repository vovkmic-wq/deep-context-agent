# Deep Context Agent

CLI-агент на Python и Deep Agents с долговременным SQLite-контекстом,
поиском и безопасным чтением публичных веб-страниц, а также ограниченной
файловой системой. Поддерживаются LM Studio, OpenAI, YandexGPT, DeepSeek,
Qwen и Zhipu AI GLM через OpenAI-compatible API.

## Как устроен большой контекст

Контекст размером в миллион строк нельзя целиком и надёжно разместить в окне
любой LLM. Проект хранит полный корпус в SQLite FTS5 и локальном Qdrant, а
модели передаёт только релевантные фрагменты:

1. Файлы читаются потоково, разбиваются на перекрывающиеся чанки и записываются
   пакетами. Объём RAM зависит от размера чанка, а не корпуса.
2. Все чанки, включая начало и конец каждого документа, сохраняются между
   запусками.
3. Перед обычным вопросом выполняется гибридный retrieval: SQLite FTS5/BM25
   объединяется с локальным vector search через детерминированный rank fusion.
   Для точных файловых операций, acceptance/self-test и длинных
   самодостаточных запросов auto-retrieval не добавляется, чтобы старая история
   не смешивалась с текущей операцией.
4. Агент может повторно вызвать `search_context`, ограничить поиск одним
   источником, перечислить сотни источников и раскрыть соседние чанки через
   `read_context_window`.
5. Полные пользовательские запросы и ответы также индексируются как
   долговременная память.

Для аудита исходного кода используется второй уровень, а не попытка отправить
миллион строк модели. Runtime создаёт SQLite-манифест всех текстовых файлов,
SHA-256-reестр, краткие сводки и Python AST-индекс. Затем он выдаёт LLM пачки по
8 файлов в независимых graph turns. Успешно прочитанные файлы фиксируются по
`ToolMessage`, непрочитанные остаются в очереди, а изменившийся SHA-256 повторно
открывает только соответствующий файл. Поэтому размер проекта влияет на число
пачек и объём БД, но не раздувает один prompt или recursion depth.

FastEmbed/ONNX вычисляет embeddings локально на CPU; документы не отправляются
во внешний embedding API. Векторы хранятся в локальном Qdrant под
`AGENT_DATA_DIR`. Модель загружается лениво, batch автоматически ограничивается
доступной памятью, а смена ID модели создаёт отдельную collection. Если vector
слой недоступен, FTS5/BM25 продолжает работать в наблюдаемом lexical-only
режиме. Повторная индексация использует SHA-256 и явно сообщает
`indexed / unchanged / skipped`.

Production-default —
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` через
`fastembed==0.7.4`: Apache-2.0, 384 измерения, cosine distance, специальные
query/document prefixes не требуются. В embedding signature входят ID модели,
версия FastEmbed, distance и prefixes, поэтому несовместимые векторы не
смешиваются.

## Установка

Требуется Python 3.11 или новее.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
Copy-Item .env.example .env.local
```

Не помещайте реальные ключи в `.env.example`. Локальный `.env.local` исключён
из Git.

## Настройка провайдеров

Общий выбор:

```dotenv
# Цепочка по умолчанию: GLM-5.3, затем GPT-5.6 Sol:
AGENT_PROVIDER_PRIORITY=glm,openai
AGENT_WORKSPACE=./agent_workspace
AGENT_CONTEXT_ROOT=./agent_workspace
AGENT_DATA_DIR=./.agent_data
AGENT_CONTEXT_TOP_K=8
AGENT_AUTO_CONTEXT_MAX_CHARS=12000
AGENT_AUTO_CONTEXT_QUERY_MAX_CHARS=2000
AGENT_ACTIVE_CONTEXT_MAX_TOKENS=80000
AGENT_RETRIEVAL_MODE=hybrid
AGENT_EMBEDDING_ENABLED=true
AGENT_EMBEDDING_PROVIDER=fastembed
AGENT_EMBEDDING_DEVICE=cpu
AGENT_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
AGENT_EMBEDDING_BATCH_SIZE=0
AGENT_VECTOR_STORE=qdrant
AGENT_EXTERNAL_EMBEDDING_FALLBACK=false
AGENT_MODEL_CALL_RETRIES=3
AGENT_MODEL_RETRY_INITIAL_DELAY=1
AGENT_MODEL_RETRY_MAX_DELAY=15
AGENT_WEB_RETRY_ATTEMPTS=3
AGENT_RECURSION_LIMIT=100
AGENT_AUDIT_BATCH_SIZE=8
AGENT_AUDIT_MAX_BATCHES_PER_REQUEST=4
AGENT_AUDIT_MAX_READS_PER_FILE=4
# Необязательные comma-separated glob-фильтры:
AGENT_AUDIT_INCLUDE=
AGENT_AUDIT_EXCLUDE=
AGENT_AUDIT_MAX_READS_PER_FILE=4
# Внутренние safety ceilings Autopilot; пользователю не нужно подбирать их:
AGENT_AUTOPILOT_MAX_WORK_UNITS=200
AGENT_AUTOPILOT_MAX_REPLANS=12
AGENT_AUTOPILOT_RETRY_ATTEMPTS=3
AGENT_AUTOPILOT_REPAIR_CYCLES=3
AGENT_AUTOPILOT_LEASE_SECONDS=900
AGENT_AUTOPILOT_HEARTBEAT_SECONDS=30
AGENT_AUTOPILOT_UNIT_TIMEOUT_SECONDS=900
AGENT_AUTOPILOT_UNIT_BATCH_SIZE=2
AGENT_AUTOPILOT_RECURSION_LIMIT=40
AGENT_PROJECT_CHECK_TIMEOUT_SECONDS=300
AGENT_PROJECT_CHECK_OUTPUT_MAX_CHARS=20000
AGENT_FAILURE_LOG_MODE=redacted
AGENT_FAILURE_LOG_RETENTION_DAYS=30
AGENT_FAILURE_LOG_MAX_ROWS=10000
AGENT_FAILURE_LOG_QUERY_MAX_BYTES=65536
```

Для совместимости также принимается старое имя `AGENT_RETRIEVAL_LIMIT`, но при
одновременном задании приоритет имеет `AGENT_CONTEXT_TOP_K`.

`AGENT_ACTIVE_CONTEXT_MAX_TOKENS` ограничивает только transient-ввод модели:
после порога старые большие результаты инструментов заменяются компактными
маркерами. Полная SQLite-история, документы и FTS5-поиск не удаляются, поэтому
корпус в миллион строк остаётся доступен через retrieval.

Неудачные запросы сохраняются отдельно от checkpoint rollback в
`AGENT_DATA_DIR/diagnostics.sqlite3`. Безопасный default `redacted` хранит
ограниченный очищенный текст, SHA-256 исходного запроса и операторскую
диагностику. Режим `metadata` не хранит текст, `off` отключает записи, а `full`
является явным локальным opt-in и может содержать персональные данные. Просмотр
не требует LLM: `context-agent diagnostics list`, `show`, `export` и `purge`.

Без явных настроек используется цепочка `glm,openai`: основная модель
`glm-5.3`, резервная — `gpt-5.6-sol`. `AGENT_PROVIDER` выбирает один провайдер.
`AGENT_PROVIDER_PRIORITY` включает
автоматический failover и имеет приоритет над `AGENT_PROVIDER`. Все удалённые
провайдеры из цепочки должны иметь настроенные ключи. Повторы одного model call
сначала исчерпываются у текущего провайдера; затем используется следующий.
Успешный fallback остаётся активным до конца текущего пользовательского хода,
а новый ход снова начинается с первого провайдера. Выполненные tools при
переключении модели не запускаются повторно.

### LM Studio

Запустите Local Server в LM Studio и загрузите модель с поддержкой tool calling.

```dotenv
LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_MODEL=<идентификатор загруженной модели>
LM_STUDIO_API_KEY=
```

Если локальная модель не умеет корректно возвращать OpenAI tool calls, обычный
чат может работать, а файловые операции и retrieval tools — нет.

### OpenAI

```dotenv
OPENAI_API_KEY=<секрет>
OPENAI_MODEL=gpt-5.6-sol
OPENAI_REASONING_EFFORT=none
OPENAI_BASE_URL=https://api.openai.com/v1
```

Для `gpt-5.6-sol` используется `reasoning_effort=none`, потому что резервный
маршрут работает через Chat Completions с function tools. Значение можно
переопределить, но ненулевой effort несовместим с этой комбинацией endpoint и
tools; для reasoning с tools требуется отдельная миграция на Responses API.

### Yandex AI Studio / YandexGPT

```dotenv
YANDEX_API_KEY=<секрет>
YANDEX_FOLDER_ID=<folder_id>
# Либо задайте полный URI:
YANDEX_MODEL_URI=gpt://<folder_id>/yandexgpt/latest
YANDEX_BASE_URL=https://ai.api.cloud.yandex.net/v1
```

API-ключу нужна область `yc.ai.languageModels.execute`. Модель должна
поддерживать function calling.

### DeepSeek

```dotenv
DEEPSEEK_API_KEY=<секрет>
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

По умолчанию thinking отключён, чтобы сделать агентный tool-calling цикл более
предсказуемым.

### Alibaba Model Studio / Qwen

```dotenv
DASHSCOPE_API_KEY=<секрет>
QWEN_MODEL=qwen3.7-plus
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

Для новых workspace-specific endpoint замените `QWEN_BASE_URL` значением из
консоли Model Studio. Thinking по умолчанию отключён для tool calling.

### Zhipu AI / GLM-5.3

Провайдер можно выбрать как `zhipu` или коротким алиасом `glm`. В runtime
используется каноническое имя `zhipu`.

```dotenv
ZAI_API_KEY=<секрет>
ZAI_MODEL=glm-5.3
ZAI_BASE_URL=https://api.z.ai/api/paas/v4
```

Поддерживаются также прежние имена `ZHIPU_API_KEY`, `ZHIPU_MODEL` и
`ZHIPU_BASE_URL`, но приоритет имеют `ZAI_*`. Для ключа GLM Coding Plan задайте
`ZAI_BASE_URL=https://api.z.ai/api/coding/paas/v4`. Thinking включён;
`AGENT_MODEL_TEMPERATURE` должен быть больше `0` и не больше `1`.

## Использование

```powershell
# Проверка конфигурации без вывода ключа
.\.venv\Scripts\context-agent.exe --provider openai doctor

# Небольшой реальный запрос к модели
.\.venv\Scripts\context-agent.exe --provider openai doctor --live

# Цепочка по умолчанию: GLM-5.3 -> GPT-5.6 Sol
.\.venv\Scripts\context-agent.exe `
  --providers "glm,openai" `
  doctor --live
.\.venv\Scripts\context-agent.exe `
  --providers "glm,openai" `
  --thread priority-main `
  chat

# Проверка GLM-5.3 и запуск интерактивного чата
.\.venv\Scripts\context-agent.exe --provider glm doctor --live
.\.venv\Scripts\context-agent.exe --provider glm --thread glm-main chat

# Индексировать всю разрешённую директорию или отдельный файл
.\.venv\Scripts\context-agent.exe index .
.\.venv\Scripts\context-agent.exe index docs\manual.txt

# Проверить retrieval без LLM
.\.venv\Scripts\context-agent.exe search "условия договора"

# Одиночный запрос и интерактивный режим
.\.venv\Scripts\context-agent.exe --provider openai ask "Найди условия расторжения"
.\.venv\Scripts\context-agent.exe --provider lmstudio --thread work chat

# Один многострочный запрос из UTF-8 файла или stdin
.\.venv\Scripts\context-agent.exe --provider openai ask --file .\prompt.txt
Get-Content .\prompt.txt -Raw | .\.venv\Scripts\context-agent.exe --provider openai ask -

# Не добавлять автоматический retrieval (search_context остаётся доступен)
.\.venv\Scripts\context-agent.exe --provider openai --no-auto-context ask "..."

# Полный read-only пакетный аудит с SQLite-resume и прямым UTF-8 отчётом
.\.venv\Scripts\context-agent.exe --thread ozon-audit audit `
  --file .\ozon-project-improvement-prompt.txt `
  --max-batches 100 `
  --report-file .\reports\ozon-audit.txt `
  --report-format both

# Только явный флаг разрешает исправления существующих выделенных файлов
.\.venv\Scripts\context-agent.exe --thread ozon-fix audit `
  --file .\ozon-project-improvement-prompt.txt `
  --allow-write --max-batches 100

# Проверка persisted-прогресса не вызывает LLM
.\.venv\Scripts\context-agent.exe audit-status --run-id RUN_ID --json

# Рекомендуемый режим большой задачи: автоматическое дробление, resume,
# проверки и итоговый отчёт. Batch size/max batches вводить не нужно.
.\.venv\Scripts\context-agent.exe --thread ozon-production job `
  --file .\ozon-project-improvement-prompt.txt `
  --allow-write `
  --report-file .\reports\ozon-production.txt

# Проверка сохранённой job без вызова LLM
.\.venv\Scripts\context-agent.exe job-status --job-id JOB_ID --json

# Локальный Web UI (браузер автоматически не открывается)
.\.venv\Scripts\context-agent.exe web --host 127.0.0.1 --port 8765
```

Путь команды `index` обязан находиться внутри `AGENT_CONTEXT_ROOT`.
В интерактивном `chat` введите `/paste`, вставьте весь prompt и завершите
отдельной строкой `/end`; `/cancel` отменяет вставку. Можно сразу передать
первую строку как `/paste ПЕРВАЯ_СТРОКА`. Обычная многострочная вставка без
`/paste` по-прежнему является несколькими интерактивными turns. Файл, stdin и
`/paste` ограничены 2 МиБ.

Обычный широкий запрос `ask` автоматически переводится в пакетный режим и
обрабатывает не более `AGENT_AUDIT_MAX_BATCHES_PER_REQUEST` за процесс. Команда
`audit` сохранена как низкоуровневый операторский режим; повтор с тем же
`thread ID`, workspace и неизменной целью продолжает сохранённый manifest.
`--max-batches 100` — жёсткий предел, а не обещание модели. Текущий прогресс
доступен LLM через `project_audit_status` и физически хранится в
`AGENT_DATA_DIR/project_audit.sqlite3`.

Для обычной большой задачи используйте `job`. Контроллер хранит job/work units
в `AGENT_DATA_DIR/autopilot.sqlite3`, обрабатывает по одной manifest batch,
фиксирует ToolMessage evidence и при `agent_step_limit`/переполнении контекста
автоматически уменьшает внутреннюю пачку и продолжает в новом worker thread.
При явном `--allow-write` после файловых изменений выполняются фиксированные
Ruff/format/pytest-проверки и ограниченный repair/recheck. Поэтому безопасный
лимит одного graph остаётся защитой, но больше не требует вручную делить задачу.
Во время model/tool/check исполнения controller продлевает lease heartbeat-ом.
Каждое возобновление увеличивает fencing generation; прежний worker больше не
может менять файлы или коммитить unit. После crash незавершённая unit остаётся
видимой как `interrupted`, а новый worker заново сверяет manifest и SHA-256.
Batch cap, recursion budget и soft deadline являются внутренними настройками:
обычному пользователю не нужно рассчитывать размер этапов.
Слова «исправь», `fix` или `update` внутри цели никогда не дают право записи:
без `--allow-write` middleware блокирует все mutating tools. Режим входит в
identity запуска, поэтому read-only и allow-write используют разные manifests.
Manifest отдельно показывает число уникальных `reviewed` файлов и фактических
успешных страниц `file_reads`; многостраничное чтение не маскируется как один
tool call. Для слишком большого отдельного файла агент обязан указать покрытие
и использовать точечный FTS/AST-поиск, а не заявлять построчное чтение целиком.
После исчерпания `AGENT_AUDIT_MAX_READS_PER_FILE` такой файл получает отдельный
статус `partial`, а итог всего запуска — `complete_with_partial`.
До пачек создаются точный file ledger и registry требований `REQ-*`; generated,
dependency, report, cache, pytest/browser и `*.egg-info` artifacts исключаются.
После каждой пачки CLI печатает flush-строку `AUDIT_PROGRESS`. Консольный итог
ограничен, а полный report сохраняет requirements matrix, дедуплицированные
findings и batch evidence в UTF-8 text/JSON.

## Веб-интерфейс

Web UI использует те же workspace, `AgentRuntime`, контекст/audit SQLite,
provider failover и policies, что CLI — отдельной обходной логики нет. Доступны
обзор инженерных ролей, task/thread-чат с историей и SSE, контекстный поиск,
Autopilot внутри чата, файловый browser/editor, live-провайдеры и безопасные
настройки. Корпус не загружается в браузер целиком: API возвращает только
ограниченные страницы и top-k.

В окне чата доступны только пять режимов: `Agent` доводит инженерную задачу до
проверяемого результата; `Ask` изучает проект только на чтение; `Plan` задаёт
необходимые вопросы и готовит план до отдельного одобрения; `Debug` ведёт цикл
«гипотеза → диагностика → воспроизведение → root cause»; `Multitask` запускает
до четырёх независимых фоновых workers с отдельными task/thread ID. Прежние
узкие названия режимов удалены из UI и API. Режим сам по себе не расширяет
workspace и права записи: Ask/Plan всегда read-only, а Agent/Debug/Multitask
используют отдельный trusted `allow_write`. Для конкурентных изменений одного
проекта нужны разные Git worktree. Опция «Enter отправляет» хранится локально в
браузере и выключена по умолчанию; `Shift+Enter` всегда добавляет новую строку.

Закреплённый заголовок чата позволяет сменить provider и совместимую chat-модель
для следующего сообщения. Каталог моделей загружается с сервера провайдера,
ограничивается и кэшируется; выбранная пара хранится для thread. Уже идущий
turn использует неизменяемый snapshot, поэтому переключение не меняет модель
посреди tool-call цепочки.

Круг справа внизу поля ввода показывает приблизительное заполнение активного
контекста. При приближении к пределу встроенный механизм Deep Agents создаёт
summary ранней переписки и продолжает ход; полная история остаётся в SQLite и
доступна поиску. Это оценка, а не точный счётчик биллинговых токенов.

Переключатель «Выполнение» находится в самом чате: `Авто` направляет широкие
проектные задачи в persistent Autopilot и автоматически повторяет там ход после
`agent_step_limit`; `Autopilot` принудительно выполняет даже короткую цель до
terminal status; `Обычный ответ` оставляет один bounded ход. Отдельной вкладки
Autopilot нет. Chat SSE показывает job ID, фазу, количество обработанных файлов,
work units, generation, heartbeat, interrupted units и replans, а сохранённые
задачи отображаются в боковой панели чата. В инженерных режимах action-запрос с
ТЗ, кодом, модулями или тестами сразу выбирает Autopilot, не расходуя перед этим
длинный ordinary turn.

В контексте путь `/workspace` означает сам `AGENT_CONTEXT_ROOT`, а не вложенную
папку с таким именем. Индексация выполняется ограниченными страницами,
показывает частичный результат, курсор продолжения,
новые/неизменившиеся/пропущенные файлы и число фрагментов. Единая политика
исключает `.git`, virtualenv, dependencies, build/cache/temp и базы агента из
glob, grep, индексации и аудита. Во вкладке аудита include ограничивает проверяемые
пути, exclude добавляет исключения, batch size задаёт число файлов одного
bounded LLM-шага (рекомендуется 8).

Во вкладке файлов каталоги и текстовые файлы кликабельны. `Назад`
идёт по истории переходов, `Выше` открывает родительский каталог, а
`Открыть` показывает path, счётчик и success/error. Полный UTF-8 файл
можно сохранить с optimistic SHA-256; частичный preview большого файла доступен
только для чтения. Во вкладке провайдеров настроенные модели можно добавлять,
убирать и переставлять: новый порядок применяется к последующим запросам до
перезапуска Web-процесса. Можно создать process-local OpenAI-compatible
profile `custom-*`: локальный HTTP разрешён только на loopback, remote требует
HTTPS и server-side `CUSTOM_<ID>_API_KEY`. Browser не принимает и не возвращает
ключи. LM Studio live-check бесплатен на уровне provider API, не показывает
payment dialog и при default `local-model` автоматически выбирает загруженную
чат-модель. Remote live-check по-прежнему требует opt-in подтверждения
возможной оплаты.

По умолчанию сервер слушает только loopback. State-changing API защищён
same-origin и CSRF, ответы имеют CSP, секреты/ключи не передаются JavaScript,
files повторно проверяются после `resolve()`, а сохранение использует ожидаемый
SHA-256. Web-delete выключен; включение `AGENT_WEB_ALLOW_DELETE=1` всё равно
требует точного подтверждения пути. Внешний bind требует `--allow-remote`,
`AGENT_WEB_AUTH_TOKEN`, `AGENT_WEB_TRUSTED_HTTPS_PROXY=1` и production HTTPS
reverse proxy; remote API дополнительно ограничивает частоту запросов.

Модель угроз локального релиза — один доверенный локальный пользователь; публичный
SaaS, tenant isolation и совместное редактирование не поддерживаются. Для
резервной копии остановите CLI/Web процессы и скопируйте весь
`AGENT_DATA_DIR` (включая SQLite `-wal`/`-shm`, если они остались). Для
восстановления верните каталог целиком при остановленных процессах и сначала
запустите `doctor`; частичное копирование отдельных SQLite-файлов не считается
надёжной резервной копией.

## Безопасность файлов

- Реальный диск доступен Deep Agent только по маршруту `/workspace/`.
- `FilesystemBackend` работает с `virtual_mode=True` и отдельным
  `AGENT_WORKSPACE`.
- `.env.local` и SQLite БД по умолчанию находятся вне этой директории.
- Дополнительные инструменты повторно проверяют пути через `Path.resolve()`;
  выход через `..` или symlink блокируется.
- Общий встроенный `delete` отключён. Удаление выполняется только через
  `remove_path`; удаление корня блокируется до обхода его содержимого, поэтому
  дочерние файлы сохраняются. Явно названный подкаталог целиком удаляется одним
  `remove_path(..., recursive=true)`, без раздельного обхода его файлов.
- Запрос изменения явного пути вне `/workspace/` отклоняется до вызова LLM и не
  создаёт placeholder или файл-замену внутри workspace.
- При точном запросе чтения агенту запрещено читать и показывать несвязанные
  файлы или угадывать другой путь.
- Для запросов, требующих tools, выводится `Verified tool operations`,
  построенный из реальных результатов текущего хода. Audit охватывает runtime,
  context, чтение/поиск/изменение файлов и web; тела файлов и страниц в него не
  копируются. Если обязательный tool не вызван, завершился ошибкой или был
  запрещён, это указывается явно.
- Runtime разрешает не более одного tool call на model step и передаёт
  `parallel_tool_calls=false`. Поэтому read/write/edit/delete не исполняются
  параллельно, даже если провайдер вернул несколько calls. Идентичная файловая
  мутация или идентичный runtime/context/listing/web-вызов в одном
  пользовательском ходе отклоняется как `denied`. Третье чтение одного
  неизменённого пути также блокируется; успешная мутация пути открывает новый
  цикл проверки состояния.
- Если `edit_file` получил устаревший или неоднозначный `old_string`,
  runtime возвращает безопасный `stale_edit_conflict` и разрешает ровно
  одно дополнительное чтение того же файла. Агент обязан построить новый
  точный `old_string` по свежему результату и повторить правку не более одного
  раза; retry до свежего чтения отклоняется. Второй конфликт останавливает
  редактирование; fuzzy-замена и скрытая перезапись файла запрещены.
- `read_context_window` работает только с indexed source из
  `search_context`, а не с `/workspace`-путём. Неверные chunk/radius
  возвращают safe error без падения graph; восемь вызовов на user turn —
  жёсткий нерасширяемый лимит против runaway-loop.
- Явное указание «не читай/не открывай/не показывай PATH» или английский
  эквивалент имеет приоритет над другими упоминаниями пути в том же prompt и
  блокирует `read_file` до обращения к backend. Запрет ограничен текущим
  предложением: путь следующей положительной инструкции не блокируется.
- Атомарные значения с маркером `DO_NOT_SHOW` или `НЕ_ПОКАЗЫВАТЬ` сохраняются в
  запрошенном файле, но заменяются на `[REDACTED]` в assistant-ответе и audit.
- Текущие provider/model доступны модели через доверенный системный блок и
  `runtime_info`; API-ключ в них не включается.
- Произвольный shell execution агенту не предоставляется. Инструмент
  `run_project_checks` допускает только фиксированные `ruff_check`,
  `ruff_format_check`, `pytest`, `mypy`, `compileall`, всегда использует
  `shell=False`, удаляет секреты из окружения дочернего процесса и ограничивает
  время/размер вывода.
- Веб-загрузчик принимает только HTTP(S) на стандартных портах, блокирует
  loopback/private/non-public адреса, повторно проверяет перенаправления и читает
  не более 1 МБ текста за запрос.
- Веб-страницы, сниппеты и документы считаются недоверенными данными и не могут
  переопределять системный промпт.
- Актуальная версия, дата, релиз или цена не считаются подтверждёнными без
  успешного web verification-tool текущего хода и его UTC `checked_at`. Обычно
  это `fetch_web_page`; для версии пакета PyPI доступен
  `get_pypi_package_info`, читающий официальный ограниченный JSON API и
  возвращающий только имя, версию, URL и время проверки. После неуспешного
  запроса runtime добавляет явный `FAIL`, даже если модель повторила старое
  утверждение.
- Transient model errors повторяются с exponential backoff и jitter только на
  уровне одного model call. Повтор всего agent graph не используется. При
  окончательной ошибке точный снимок checkpoints/writes этого thread
  восстанавливается, поэтому провалившаяся команда не попадёт в следующий ход.
  Если filesystem tool успел успешно завершиться до более поздней ошибки модели,
  его подтверждённый side effect не отменяется автоматически и явно указывается
  в тексте ошибки.
- Явно оборванная изменяющая команда отклоняется до модели и tools; отсутствующее
  содержимое не угадывается.

Версия 0.22.0 предназначена для production-эксплуатации как локальный
однопользовательский CLI/Web UI в границах `AGENT_WORKSPACE`. Для
многопользовательского сервиса добавьте
процессную изоляцию workspace, аутентификацию, лимиты запросов, централизованные
логи и human-in-the-loop для необратимых действий.

## Проверка проекта

```powershell
.\.venv\Scripts\python.exe -m ruff check --no-cache .
.\.venv\Scripts\python.exe -m ruff format --check --no-cache .
.\.venv\Scripts\python.exe -m mypy src/context_agent
.\.venv\Scripts\python.exe -m pytest -ra
```

Обычные тесты не вызывают платные API. Реальный сетевой smoke-test запускается
только явно через `doctor --live`. Pytest использует временный каталог текущего
пользователя и не создаёт общий `.pytest_cache` в проекте. Это предотвращает
конфликт ACL, если проект по очереди проверяют Codex sandbox и обычный Windows-
пользователь. Ruff запускается с `--no-cache` по той же причине.

Индексатор поддерживает UTF-8, CP1251 и BOM-тексты UTF-16/UTF-32. Он не
загружает служебные `.deps`, `.pytest-*`, `.coverage*`, `reports`,
`*.egg-info`, browser-profile, Playwright/cache и build-артефакты.
`list_context_sources` возвращает 20
источников по умолчанию и не более 50 за один вызов, чтобы аудит больших
проектов не переполнял окно модели.

Явные ограничения пользователя вида «не более 15 functional tool calls» или
«не более двух `search_context`» исполняются runtime-политикой, а не только
текстовым обещанием модели. После исчерпания budget tool скрывается из
следующего model request, а устаревший provider-call не выполняется.
Нулевой бюджет скрывает запрещённый tool уже до первого model call. Пути внутри
`<acceptance_manifest>` используются только evaluator и не превращаются в
filesystem allowlist; область чтения определяется prose-запросом.

Изменения по версиям перечислены в `CHANGELOG.md`. Автоматический pytest —
внешний источник итогового acceptance-статуса; таблица PASS/FAIL, написанная
самой LLM, всегда остаётся `NOT_VERIFIED`. Канонический приёмочный prompt может
содержать ограниченный `<acceptance_manifest>`: runtime независимо от LLM
сверяет точные количества, порядок required events, forbidden events и
ожидаемые negative statuses, затем выводит per-tool counts и собственный
`PASS`/`FAIL`. Недетерминированный служебный tool можно явно перечислить в
`allowed_unlisted_tools`; первичные ошибки показываются как FAIL, а зависимые
проверки — как BLOCKED. Pytest остаётся внешней проверкой качества кода.
Если модель преждевременно формирует итог или выбирает неверный следующий call
после уже доказанного cleanup,
ограниченный runtime completion gate завершает только явно запрошенные
dependency-ready проверки отсутствия и удаление точного пути. Он не начинает
исходные операции, не выходит за `/workspace/` и не удаляет корень workspace;
путь, упомянутый только внутри JSON manifest, не является разрешением.
После первого доказанного event runtime также фиксирует `tool_choice` именем
следующего prose-разрешённого ordered event, предотвращая перестановки дешёвой
модели без самостоятельного запуска сценария.
Manifest версии 2 дополнительно проверяет `min_results` и `content_sha256`:
runtime получает cardinality из структурированного ToolMessage, а SHA-256 — из
фактических байтов workspace-файла на момент успешной операции, не копируя тело
файла в audit. При прямом вопросе о количестве ответ формируется из evidence.
Явный контракт `ровно один раз`/`exactly once` удаляет завершённый tool из
следующего model request и не исполняет устаревший повтор provider.

## Ограничения

- Качество вызова инструментов зависит от выбранной модели.
- Один `AgentRuntime` рассчитан на последовательные запросы; несколько процессов
  не должны одновременно изменять один и тот же `thread_id`/SQLite-файл.
- DDGS может быть недоступен из некоторых сетей или временно ограничивать
  запросы.
- Некоторые сайты блокируют автоматическое чтение или формируют содержимое
  только JavaScript-кодом; агент обязан сообщить, что точная проверка не удалась.
- Первый запуск hybrid retrieval загружает локальную FastEmbed-модель и может
  занять больше времени; при недоступности vector-слоя поиск автоматически
  остаётся работоспособным через FTS5/BM25.
- Изменение файлов агентом необратимо; используйте отдельную рабочую директорию
  и систему контроля версий.
