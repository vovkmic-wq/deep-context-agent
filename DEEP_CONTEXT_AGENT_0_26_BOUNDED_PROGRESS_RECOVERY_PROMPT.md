# Промт реализации 0.26: Autopilot без потери прогресса между этапами

Статус: исполняемый промт этапа 0.26; готовность подтверждается приёмкой.
Нормативное ТЗ: `AUTOPILOT_PROGRESS_RECOVERY_TECHNICAL_SPEC.md`, P01–P15 и A01–A26.
Общие документы: `TECHNICAL_SPEC.md`, `IMPLEMENTATION_PROMPT.md`,
`WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`, `TASK_RESUME_MODEL_ROUTING_SPEC.md`.

## Задача исполнителя

Устрани причины инцидента job `5dc5c86b57fd25f873da7acd` в версии 0.25.0:
два graph limits после 13 model calls, повторные чтения, затем no_verified_progress;
план/next_step не сохранены, в UI неработающая связь общей и дочерней диагностики.
Это не transport failure и не ложный запуск полного аудита. Не повторяй эти
диагнозы без новых доказательств. Оригинальные диапазоны чтения журнал не сохранил.

Работай в `C:\script\20260820-langchain`. Пользователь ставит одну цель, а агент
сам распределяет работу по безопасным units. Увеличение лимитов или одна правка
system prompt не считаются исправлением. Архитектурная ответственность за durable
handoff принадлежит runtime, а не добровольному вызову инструмента моделью.

## Порядок выполнения

### 1. Сохранить исходное состояние и подтвердить причину — P01, P09

- Прочитай актуальные ТЗ и состояние репозитория. Сохрани чужие/uncommitted файлы.
- Изучи три request IDs из таблицы инцидента только read-only. Сохрани безопасные
  метрики, не копируй реальные секреты/документы в fixtures и Git.
- Зафиксируй текущую версию/набор тестов. Составь короткую матрицу
  «требование → код → тест → evidence → статус»; обновляй её после каждого пункта.
- Не меняй Ozon и рабочую БД ради воспроизведения. Используй временный fixture.

### 2. Воспроизвести ошибки до исправления — P10

- Добавь failing integration cases: третья новая страница, >13 tool decisions
  при recursion=40, модель не вызывает save_task_checkpoint, повторный worker,
  blocked Web task с отсутствующим parent diagnostic record.
- Проверь assertions на реальные calls/ranges/DB transitions, не на текст ответа.
- Отдельно зафиксируй корректные границы: root/SSRF/read-only/cancel/lease/once.

### 3. Ввести durable runtime ledger — P01

- Расширь TaskStateStore additive-миграцией: structured phase/current operation,
  evidence references, read coverage, cursor, recovery reason, budgets, revision.
- Пиши required checkpoint до начала исполнения и после завершённых tools,
  независимо от model-written plan. Для неизвестного плана храни unknown и
  ограниченную следующую planning operation, а не выдуманный completed checklist.
- Раздели proposed plan/model claims и verified evidence. Не складывай весь корпус
  или повторяющиеся excerpts в model prompt/одну гигантскую JSON-строку.
- При недоступной БД останови новые side effects. Crash gap между действием и
  receipt решай reconciliation по состоянию файла; не заявляй exactly-once там,
  где эффект ещё не подтверждён.

### 4. Исправить range-aware read guard — P03

- Нормализуй реальные интервалы строк/версии файлов и фиксируй их в safe metadata.
- Разреши несколько полезных страниц; считай новый coverage через union интервалов.
- Не давай обходить budgets сменой limit/offset на почти идентичном участке.
- Сохрани ограниченный reread перед edit, свежую проверку версии и существующий
  stale-edit-conflict протокол. Не разрешай запись по старому excerpt.
- Проверь short/large files, EOF, truncation, внешнюю мутацию, denied и errors.

### 5. Исправить детектор прогресса — P04

- Раздели новое знание, изменение реализации и подтверждение тестом.
- Исключи из progress delta новый request ID, повтор summary/плана, heartbeat,
  дубли страниц и одно лишь успешное HTTP. Новая страница — новое знание, не код.
- Счётчики привяжи к завершённым полезным операциям и logical task, а не случайному
  числу заходов middleware. Model retries и graph nodes считай отдельно.
- Recovery reread разрешай по собственному бюджету без имитации нового discovery.

### 6. Добавить soft yield до жёсткого лимита — P02

- Измеряй реальное число graph steps в текущей зависимости. Зарезервируй выход до
  hard recursion limit, timeout и переполнения активного prompt.
- На безопасной границе сохраняй handoff без обязательного дополнительного LLM-call.
- Завершай unit как yielded; следующий worker получает конкретное действие и
  релевантные факты, неизменную цель и пересечённые текущие права.
- Не повышай retry/failure counters при штатном yield и не обнуляй общий бюджет.
- Проверяй deadline/cancel/lease перед следующей unit и перед каждым side effect.

### 7. Организовать исполнение узких шагов — P05

- Выдели discovery/planning/implement/verify/repair без создания audit manifest.
- Не начинай каждую unit с ls корня, если путь уже известен и версия актуальна.
- Поддержи восстановление точного нужного excerpt из bounded cache/хранилища,
  не подменяя fresh edit verification старым текстом.
- Не требуй от пользователя разбивки задачи. Обязательные missing choices/права
  не угадывай и не извлекай из logs/retrieval.
- Тесты запускай доступными allowlisted средствами; результат без exit code/log
  не считается проверенным. Новая мутация делает прежний PASS устаревшим.

### 8. Добавить контролируемую execution escalation — P06

- Сохрани model-call transport fallback. Новый механизм работает на уровне
  неуспешной подзадачи, даже если все HTTP ответы были успешны.
- Реализуй порог/лимиты P06 и persisted failure streak; не переключай модель
  из-за нормального многопакетного чтения с положительным delta.
- В auto выбирай разрешённого кандидата из trusted metadata. Manual — только
  с отдельным заранее данным opt-in. Не расширяй согласованную цепочку самовольно.
- Проверь все ограничения risk/context/tools/cost/latency/local-only и совместимость
  параметров модели. Если кандидата нет, объясни blocked без бесконечного ping-pong.
- Handoff передай новому исполнителю; не replay успешные tools предыдущего.

### 9. Связать parent/child диагностику — P07

- Создавай реальную parent request запись для принятой задачи до запуска worker.
- Сохраняй typed IDs и ссылки unit → child request → parent request → Web task/job.
- Aggregate terminal не подменяет фактический child exception. Указывай
  last_failed_request_id, partial evidence, duration и реальные totals.
- Добавь resolver для старого ID инцидента через уже сохранённые child records,
  не фабрикуя родительский запрос. Retention/off показывай как явное отсутствие
  журнала; диагностика не должна утекать через retrieval/SSE.

### 10. Обновить Web/CLI отображение — P08

- Показывай конкретный этап, прочитанные новые диапазоны, ожидание модели,
  yield/resume/escalation, изменения и факт запуска проверок.
- Разведи heartbeat timestamp/elapsed, unit counters/file counts и partial evidence/
  implementation progress. Не показывай условный 100% при неизвестном объёме.
- Почини кнопку диагностики для parent и child. Сохрани JSONL/SSE terminal и replay.
- Не добавляй вторую вкладку Autopilot или отдельный Web-only orchestration path.

### 10a. Сделать выбор ресурса и память наблюдаемыми — P13–P15

- Замени byte-count на единый token estimator с output/safety reserve, указанием
  метода и консервативным offline fallback. Не выдавай estimate за provider usage.
- Добавь в Web валидируемую настройку model profiles, cost/latency/local-only и
  execution escalation. Сохраняй атомарно без ключей; применяй со следующего запроса.
- Frontier/reasoning model используй только как один из разрешённых ресурсов.
  Workflow, права и model tier не объединяй в один классификатор.
- В закреплённом заголовке чата и Overview покажи FTS5/BM25 и actual vector state:
  disabled/lazy/ready/degraded. Не загружай модель только ради индикатора.
- После поиска/индексации обновляй состояние. При FastEmbed/Qdrant failure
  продолжай через FTS5/BM25 и показывай degradation.
- Добавь golden retrieval checks с hit@k/MRR для смысловых фраз и точных кодов;
  наличие индекса или один удачный ответ не считай доказательством качества.

### 11. Проверить restart, concurrency и безопасность — P09

- Повтори миграцию на копии старой БД, два открытия после миграции, expiry lease,
  stale owner и crash после effect/до receipt. Старые unknown ranges не восстанавливай
  «по догадке». Один ограниченный recovery-planning шаг допустим.
- Убедись, что failed graph rollback не трогает ledger/diagnostics и не возвращает
  устаревшие полномочия. Инъекции из логов и смена thread/workspace не расширяют scope.

### 12. Выполнить все автоматические проверки — P10

- Покрой A01–A26. Отдельный fixture должен требовать не менее пяти страниц одного
  файла и нескольких units без voluntary checkpoint вызова LLM.
- Выполни Ruff check/format, полный pytest, mypy/TypeScript, production Web build,
  bundle tests, compileall, pip check и packaging. Проверь отсутствие секретов,
  рабочих БД и больших тестовых корпусов в артефактах.
- Исправь найденные ошибки и повтори связанные тесты, затем полный набор.
  Прошлые 353 passed не заменяют новую приёмку и не скрывают известный инцидент.

### 13. Провести live и повтор после исправлений — P11

- Используй существующие ключи и только изолированную копию/fixture с отдельными
  workspace/data/thread. Не трать API-вызовы до прохождения детерминированных тестов.
- Повтори исходную задачу с glm-5.3-flash при её доступности; зафиксируй actual model,
  все handoffs и отсутствие полного аудита. Не выдавай другую модель за точный повтор.
- Проверь forced handoff и restart, затем реальное ограниченное изменение/проверку.
  Повтори успешный сценарий на второй чистой тестовой БД. Исходная рабочая БД не очищается.
- Отдельно проверь разрешённую и запрещённую escalation и UI parent/child link.
  Отделяй injected faults от естественных ошибок. Недоступные обязательные live
  проверки помечай blocked/skipped и не объявляй production PASS.

### 14. Синхронизировать документы и результат — P12

- Для каждого P/A пункта запиши реализацию, тесты и ссылки на безопасные evidence.
- Обнови глобальное ТЗ/промт, системный промпт, Web-ТЗ, README, CHANGELOG и
  IMPLEMENTATION_STATUS. Различай готовое, частичное и не проверенное.
- Релизную версию Python/Web/пакета повышай только при фактическом завершении,
  не из-за существования этого документа. Production — критерии приёмки, не лозунг.
- Перезапуск сервера и Git публикацию выполняй только в пределах актуального
  пользовательского запроса; подготовка этих документов их не разрешает.

## Ожидаемый результат

Агент сам проходит длинное первичное чтение и реализацию без потери coverage на
границах units; осмысленно продолжает сохранённую задачу, сохраняет отрицательную
диагностику и выполняет проверки. При объективном блокере останавливается с
проверяемым partial и рабочей диагностикой, а не выдаёт готовность по намерению.
