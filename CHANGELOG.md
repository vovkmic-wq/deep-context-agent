# История версий

Формат основан на Keep a Changelog. Проект использует семантическое
версионирование.

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
