# Production-промт исправления ozon_market_analytics

## Роль и результат

Ты — ведущий Python/TypeScript-разработчик, инженер данных и специалист по
безопасности. Доработай существующий `ozon_market_analytics` 0.5.0 до
production-состояния, не переписывая проект с нуля и не уничтожая рабочую
fixture/offline-функциональность.

Обязательные источники истины:

- `/workspace/TECHNICAL_SPECIFICATION.md`;
- `/workspace/INTERFACE_TECHNICAL_SPECIFICATION.md`;
- `/workspace/README.md`;
- `/workspace/pyproject.toml`;
- исходники, миграции и тесты.

Baseline уже подтверждён:

- Ruff check: success;
- Ruff format: 51 файлов отформатированы;
- mypy: success для 30 source files;
- pytest: 58 passed;
- compileall: success;
- Node.js tests: 4 passed;
- frontend typecheck и lint: success.

Эти результаты являются baseline, но не доказывают выполнение всех требований
ТЗ. После любых правок проверки должны быть запущены заново.

## Безопасные границы

1. Работай только внутри `/workspace/`.
2. Не читай и не печатай `.env`, API keys, cookies и пользовательские данные.
3. Не выполняй HTML scraping Ozon, не обходи CAPTCHA/403/rate limits и не
   имитируй запрещённые browser headers.
4. Official API реализуется только по актуальной официальной документации и
   только для разрешённых методов. Не выдумывай endpoint или DTO.
5. Все network-тесты используют `httpx.MockTransport`/fakes. Live contract test
   является отдельным opt-in и требует явного ключа.
6. Не удаляй production БД, отчёты и конфигурацию. Миграции транзакционны и
   проверяются на копии.
7. Не объявляй production-ready до завершения всех acceptance-критериев.

## Подтверждённые проблемы, которые необходимо устранить

### 1. Источник данных и CLI

- `collect` и `collect-periodic` сейчас являются заглушками.
- `run` работает только через fixture/import.
- UI прямо сообщает, что official API adapter не подключён.

Реализуй интерфейс official API adapter с dependency injection и отдельными
DTO. Перед кодированием проверь официальные схемы; зафиксируй использованные
URL документации и дату проверки. Если доступ к конкретному endpoint не
подтверждён, capability должен оставаться `unsupported`, а код не должен
притворяться рабочим.

CLI `collect`, `collect-periodic` и web run должны вызывать общий application
service. Fixture и user import остаются полностью offline. Добавь идемпотентный
checkpoint/cursor, bounded pagination и graceful cancellation.

### 2. Матрица ошибок и retry

Строго реализуй правила ТЗ:

- timeout, transport error, 408 и разрешённые 5xx: bounded exponential backoff
  с jitter;
- 401/403/CAPTCHA: немедленная остановка без обхода;
- 429: поведение строго по ТЗ и официальному контракту, без бесконечного retry;
- invalid DTO: typed parse error с безопасным bounded sample;
- все attempts, latency и итог записываются без authorization headers.

Timeout обязан передаваться фактическому HTTP-клиенту. Retry не дублирует
сохранённые наблюдения.

### 3. Конфигурация и пути

- `load_config()` не должен создавать каталоги или менять filesystem.
- Вынеси подготовку окружения в явный `prepare_directories()` application
  step.
- Нормализуй reports/logs/checkpoints/import paths относительно project data
  root. Абсолютный путь и `..` требуют отдельного явно разрешённого режима.
- Проверь symlink escape после `resolve()`.
- Запись конфигурации должна быть атомарной, валидированной и не раскрывать
  секреты.

### 4. Модель данных, provenance и миграции

- Сохрани существующие Pydantic `DataProvenance`, `SellerFacts`,
  `ReviewsSummary`, `QuestionsSummary`, `DeliveryFact` и `ProductEnrichment`, но
  доведи persistence до требований ТЗ.
- Добавь нормализованные сущности/таблицы ProductHistory, SellerData,
  ReviewsQnA и DeliveryFacts либо документированную эквивалентную модель с
  индексами и FK. Один JSON blob в `attributes` не считается достаточным без
  доказанной схемы и запросов.
- Каждая новая запись содержит UTC timestamp, `source_kind`, безопасный
  `source_ref`, `schema_version`, `run_id`, estimation flag и версию adapter.
- Миграция старой БД сохраняет данные, повторный запуск идемпотентен, downgrade
  или backup/restore задокументированы.
- Добавь uniqueness и deduplication согласно ТЗ.

### 5. Аналитика и отчёты

- Выясни причину `UNKNOWN` margin в baseline fixtures. Если расходы не заданы,
  сохраняй корректный unknown с явной причиной; если заданы — вычисляй unit
  economics и проверяй формулы эталонными тестами.
- Проверь 1d/7d/30d окна на фиксированной UTC timeline и граничных датах.
- `top_niches` не должен быть неосознанной копией `top_categories`.
- Добавь позитивные граничные fixtures для `stable`, `high_competition` и
  `low_comp_high_demand`.
- Денежные значения используют Decimal или документированную стратегию
  округления; пользовательские проценты не содержат float noise.
- JSON root содержит `schema_version`, `generated_at`, период, timezone,
  provenance, formula/model versions и run ID.
- JSON хранит массивы/объекты как структуры, а не вложенные JSON-строки.
- CSV/XLSX/HTML получают метаданные либо безопасный sidecar manifest.
- HTML показывает «Нет данных» вместо `None`/`NaN`, безопасно экранирует текст и
  явно маркирует модельные оценки продаж.

### 6. Backend и Web security

- State-changing API защищены same-origin, Origin/CSRF и аутентификацией при
  любом non-loopback deployment.
- Loopback mode остаётся default; внешний bind запрещён без явной безопасной
  конфигурации.
- Не возвращай raw `str(exc)` клиенту. Введи стабильные error codes, безопасные
  сообщения и server-side request ID; traceback остаётся только в redacted log.
- Валидируй product/media/report URLs по allowlist `http/https`; блокируй
  `javascript:`, опасный `data:` и protocol-relative ambiguity.
- Ограничь размеры upload, JSON, CSV, report download и pagination.
- Удаление отчёта и импорт fixture должны иметь проверки пути и symlink.

### 7. Frontend

- Пропускай `item.url` через `safeExternalUrl` до формирования `href`.
- Экранируй все значения фильтров, в том числе восстановленные из localStorage.
- Замени `Record<string, any>` на точные DTO и удали dead code.
- Обработай отсутствующий динамический ключ `estimated_sales_${days}d` без
  молчаливого отображения 0.
- Сделай timeout ожидания запуска согласованным с periodic jobs.
- Добавь pagination/virtualization для отчётов и больших списков товаров.
- Добавь `.chart-fill.green` или устрани неподдержанный вариант цвета.
- Node tests должны вычислять пути от `import.meta.url`, а не от
  `process.cwd()`.
- Добавь поведенческий headless E2E для критического flow, а не только поиск
  строк в bundle.

### 8. Зависимости и воспроизводимость

- Зафиксируй совместимый lock/constraints и верхние границы критичных
  зависимостей.
- Сохрани Python 3.11+ и Node >=22.13.
- Build webui должен быть воспроизводимым; тест проверяет, что regenerated
  static bundle не создаёт неожиданный diff.
- Сгенерированные reports, `.pytest_tmp*`, coverage, DB и bundle cache не
  попадают в git.

## Метод работы

1. Сначала создай requirement matrix из обоих ТЗ.
2. Проверь git status и сохрани пользовательские изменения.
3. Перепроверь каждую HIGH/CRITICAL находку по исходнику и тесту. Не меняй код
   по неподтверждённому утверждению LLM-аудита.
4. Раздели работу на вертикальные этапы:
   security/config -> storage/migrations -> official adapter -> analytics/report
   -> frontend -> documentation.
5. Для каждого дефекта сначала добавь failing regression test, затем минимальный
   production fix.
6. После этапа запускай узкие тесты; после всех этапов — полный набор.
7. Проведи миграцию на временной копии старой БД и полный offline E2E.
8. Live contract test выполняй только при явной конфигурации и не делай его
   обязательным для pytest.
9. Обнови ТЗ при уточнении контракта, README, `.env.example`, capability matrix,
   changelog и версию.
10. В финальном отчёте перечисли только подтверждённые изменения, тесты с exit
    code, миграционный результат и оставшиеся unsupported capabilities.

## Обязательные тесты

- official adapter DTO/pagination/cursor/retry/timeout через MockTransport;
- отсутствие retry для запрещённых статусов по ТЗ;
- CLI collect/periodic cancellation и resume;
- config load без filesystem mutation;
- path traversal, absolute path и symlink escape;
- миграция, FK, uniqueness, dedup и повторная миграция;
- ProductHistory/SellerData/ReviewsQnA/DeliveryFacts persistence/query;
- provenance/schema_version/run_id/UTC round-trip;
- margin с расходами и без них;
- 1d/7d/30d boundary cases;
- positive/negative cases всех report filters;
- JSON schema и отсутствие double encoding;
- safe error DTO, CSRF/Origin/auth и secret redaction;
- dangerous product URL и localStorage XSS;
- API contracts, offline E2E и headless browser flow;
- Node test/typecheck/lint/build reproducibility.

## Финальные quality gates

```powershell
.\.venv\Scripts\python.exe -m ruff check --no-cache .
.\.venv\Scripts\python.exe -m ruff format --check --no-cache .
.\.venv\Scripts\python.exe -m mypy . --cache-dir .mypy_cache
.\.venv\Scripts\python.exe -m pytest -ra -p no:cacheprovider
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

```powershell
$node = "C:\Users\Михаил\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
Push-Location -LiteralPath .\webui
& $node --test "tests/*.test.mjs"
& $node --disable-warning=ExperimentalWarning scripts/typecheck.mjs
& $node scripts/lint.mjs
& $node --disable-warning=ExperimentalWarning scripts/build.mjs
Pop-Location
```

Путь bundled Node является примером локальной машины и не должен попадать в
код или конфигурацию проекта. В CI используй `node` из PATH требуемой версии.

## Критерий production-ready

Проект готов только тогда, когда official capability честно реализован либо
явно помечен unsupported, все новые данные имеют версионированный provenance,
миграция сохраняет старые данные, security-находки закрыты, отчёты имеют
стабильные схемы, полный Python/Node/offline E2E проходит, а документация не
выдаёт fixture и модельные оценки за подтверждённые production-данные Ozon.
