# ТЗ 0.26: сохранение прогресса и восстановление Autopilot

Статус: требования P01–P15 реализованы и приняты 2026-09-06. Точные автоматические
и live-evidence приведены в `IMPLEMENTATION_STATUS.md`; этот нормативный документ
сам по себе не заменяет воспроизводимый отчёт о проверках.
Промт реализации: `DEEP_CONTEXT_AGENT_0_26_BOUNDED_PROGRESS_RECOVERY_PROMPT.md`.

## 1. Проблема и подтверждённые исходные данные

Инцидент: job `5dc5c86b57fd25f873da7acd`, Web task
`5aef963507e2489083e2ecaf5ac1ebf0`, версия 0.25.0, 2026-09-04.
Задание: написать и проверить сервисный слой сбора, истории, анализа и экспорта
по ТЗ проекта Ozon. Workflow был `project-change`, units — `execute`,
`audit_run_id=null`: ошибочного запуска полного аудита в этом прогоне не было.

| Unit | Request ID | Model calls | Результат |
| --- | --- | --- | --- |
| 1 | `07c046cec8c84331bb914c6812e700bc` | 13 | `agent_step_limit`, recursion limit 40 |
| 2 | `9f9766aa79c144ee90eb15cae650bbcc` | 13 | `agent_step_limit`, recursion limit 40 |
| 3 | `0f72360b0d2a400d97bb40ee02396a92` | 7 | `no_verified_progress` |

Все 33 зарегистрированных API-вызова `glm-5.3-flash` успешны; ошибок транспорта
нет. Зарегистрировано около 247 секунд работы. Записи/изменения файлов и запуск
проверок не подтверждены. Сохранены 27 tool receipts, но отсутствуют plan/next_step.
Model routing: `configured-chain`, причина `PROFILES_NOT_CONFIGURED`.
UI отдал общий Web task ID как request ID диагностики, для которого отсутствует
родительская запись `request_attempts`; дочерние записи диагностики существуют.

Текущий read guard ограничивает путь двумя чтениями независимо от диапазона.
Текущий progress fingerprint не содержит диапазона и использует хеш всего файла.
Старый журнал не сохранял offset/limit: нельзя утверждать, какие именно страницы
повторялись. Это ограничение доказательств, а не основание выдумать аргументы tools.

## 2. Цель, область и неизменяемые границы

Пользователь ставит цель один раз. Runtime сам выделяет ограниченные этапы,
сохраняет факты чтения и исполнения до исчерпания бюджета, продолжает ту же задачу
с нужного шага и при допустимых условиях меняет модель. Объём ТЗ не должен
требовать от пользователя подбора batch/recursion limits.

Приоритет: требования P01–P15 ниже дополняют R1–R6 этапа 0.25 и заменяют старые
правила «не более двух чтений пути», «повтор с той же исходной целью без handoff»
и «прогресс определяется только хешем целого файла». Остальные safety-инварианты
не ослабляются. Этот этап не реализует функциональность Ozon вместо самого агента.

Запрещено решать проблему отключением guards, бесконечным увеличением лимитов,
широким аудитом вместо разработки, replay всего изменяющего graph или обещанием
успеха любой LLM. Рабочие данные/ключи/БД пользователя не удалять и не обнулять.
Произвольный shell или внешние сервисы не добавляются обходным путём.

## P01. Runtime-owned durable ledger

1. Использовать существующий TaskStateStore и additive migration: прогресс задачи
   хранится вне транзакционного rollback неудачного LangGraph turn. Не создавать
   независимую логику для Web и CLI. Записи атомарны, защищены owner/revision/lease.
2. Перед первым действием создать минимальный runtime checkpoint: task ID,
   исходная цель/её hash, effective scope/rights, workspace identity, фаза,
   ближайшая ограниченная операция, budgets и версия схемы.
3. После каждого завершённого разрешённого tool call фиксировать receipt и
   продвижение курсора, даже если LLM ни разу не вызвала save_task_checkpoint.
   При чтении текста фиксировать диапазон; при listing/search — cursor/page;
   при изменении — до/после fingerprint, outcome; при проверке — профиль,
   exit code, hash лога и fingerprints проверяемых входов.
4. План LLM и completed claims хранить отдельно от runtime evidence. Если плана
   ещё нет, записать это явно и назначить bounded planning unit, не объявлять
   требования выполненными и не повторять полный discovery.
5. Разрешённые excerpt/cache references ограничены по размеру и защищены как
   исходные документы. Полные файлы не дублировать в каждом checkpoint. Табличный
   ledger пагинируется; компактный handoff содержит ссылки и только нужные факты.
6. При read-only ходе разрешено внутреннее журналирование фактов чтения, но оно
   не переписывает согласованную цель/план и не выдаёт новые права записи.
7. Если required ledger нельзя сохранить, не начинать следующее побочное действие:
   безопасный blocked с отдельной причиной. Сбой между filesystem effect и receipt
   помечается как unknown/reconciliation_required; после restart проверять фактическое
   состояние, а не слепо повторять запись. SQLite и filesystem не объявлять общей
   атомарной транзакцией. Failed/denied tool не продвигает курсор успешной работы.

## P02. Soft budget и передача следующей unit

1. Считать реальные шаги графа отдельно от числа model calls и tool calls.
   40 graph steps не считать 40 доступными вызовами LLM. Проверять фактические
   границы в используемой версии Deep Agents/LangGraph.
2. До hard recursion/time/context limit применять soft boundary. Зарезервировать
   достаточно шагов для завершения текущего tool cycle и выхода; при невозможности
   резерва завершиться до первого model call с понятной конфигурационной ошибкой.
3. На safe boundary прекратить новые model/tool calls, сохранить checkpoint и
   reason, завершить unit как `yielded` и передать тот же task/job следующему worker.
   Для forced handoff не требовать ещё одного успешного LLM-вызова или генерации
   summary: использовать уже подтверждённые факты. Summary — только оптимизация.
4. Повтор начинается с next operation/current phase и handoff, не только с исходной
   большой цели. Исходная цель остаётся неизменной и используется как контекст.
5. Бюджеты task elapsed/model attempts/cost estimates/escalations остаются общими
   между units; yield не обнуляет их. Task cancellation/deadline/lease loss не
   запускают новую unit, retry или модель. Плановая передача не увеличивает счётчик
   неудач или replans и не является поводом перейти на более дорогую модель.
6. Неожиданный hard limit сохраняется как failed/interrupted с forensic evidence;
   recovery восстанавливается из последнего подтверждённого ledger без потери
   checkpoint и без повторного исполнения уже завершённых side effects.

## P03. Чтение по диапазонам, а не лимит на весь путь

1. Ключ документа: workspace identity + canonical relative path + content version.
   Учитывать реальные изменения извне, не только мутации текущего runtime.
   Symlink/path traversal/root protections сохраняются.
2. Для read_file фиксировать requested offset/limit, фактически возвращённый
   нормализованный интервал `[start_line, end_line)`, excerpt hash, file version,
   bytes/lines returned, truncated и next_cursor/offset. Согласовать нумерацию
   с реальным backend; не выдавать запрошенный диапазон за реально прочитанный.
3. Третья и последующие новые страницы неизменённого файла разрешены, пока текущие
   права и бюджеты допускают чтение. Повтор полностью покрытого диапазона не даёт
   нового information progress. Изменение limit/сдвиг на одну строку не обходит
   лимиты: считать объединение интервалов и новый объём, ограничить минимальный
   полезный page step, вызовы и суммарные bytes на unit/job.
4. Частичное перекрытие даёт прогресс только для нового участка. EOF/пустой ответ,
   denied, error и повтор старого cursor не продвигают coverage. Повторный prompt
   или новый worker не обнуляют известное покрытие неизменившегося документа.
5. Разрешённое восстановление свежего фрагмента перед edit — отдельное действие:
   перечитать или взять проверенный актуальный excerpt cache. Оно не считается
   новым discovery. При изменённой версии файла старые excerpts не использовать
   для old_string; сохранить bounded stale-edit recovery, не разрешать overwrite
   по памяти или retry бесконечное число раз.
6. Для точного файла применять read_file с нужным диапазоном. Для поиска неизвестного
   пути — ограниченный discovery с едиными exclusions и cursor, не полный аудит.

## P04. Семантика прогресса и остановок

1. Разделить information progress, implementation progress и verification progress.
   Новый участок ТЗ полезен, но не означает готовый код; новые tool receipts не
   означают, что все требования выполнены. End-of-response не закрывает задачу.
2. Fingerprint включает тип операции, target/version, range/cursor, outcome и
   релевантные результаты. Счётчик не зависит от одного file SHA, call ID, request ID,
   изменившегося summary или записи очередного плана самим LLM.
3. Не смешивать model transport retries, шаги graph и завершённые действия при
   подсчёте stagnation. Recovery/re-read старого участка не сбрасывает счётчик
   нового знания, но допустим в отдельном ограниченном recovery budget.
4. Отсутствие новых данных запускает bounded replanning/эскалацию согласно P06;
   повтор того же состояния без полезного delta не продолжается бесконечно.
   Блокировка содержит конкретную причину и сохранённый следующий шаг/пробел.
5. Хранить units started/succeeded/yielded/failed/interrupted отдельно, как и model
   calls, file reads, unique lines, changed files, checks. `0/3` не подписывать как
   «обработано файлов». `verification=not_run` не равен FAIL выполненного теста.

## P05. Узкая постановка этапов разработки

1. Допустимые внутренние фазы: targeted discovery, planning, implement, verify,
   repair. Это не новые режимы чата и не отдельная вкладка Autopilot.
2. Каждая unit получает ограниченную подзадачу, relevant paths/ranges, критерий
   результата, текущие права, оставшиеся budgets и указание неполного покрытия.
   Выбор путей выводится из согласованной цели и уже подтверждённых фактов.
3. Большое ТЗ читается страницами/по разделам с coverage ledger. Агент не обязан
   просмотреть весь проект для реализации одного сервиса. Read-only review не
   превращается в кодирование. Только явный project-audit создаёт audit manifest.
4. Проверки запускаются доступным allowlisted runner; отсутствующий runner или
   зависимость дают not_run/blocked с причиной, не выдуманный PASS. После мутации
   устаревшие результаты тестов помечать stale, повторять релевантные проверки.

## P06. Эскалация после неудач выполнения

1. Отделить transport fallback от execution escalation. Успешный HTTP не доказывает
   успешность этапа. Повтор agent_step_limit/no_verified_progress при том же subtask
   может быть сигналом смены модели, но плановый yield с новым coverage — нет.
2. Хранить policy version, failure streak и историю переключений между units и
   restart. Базовая policy: после двух неуспешных восстановлений одной подзадачи
   без нового verified delta — не более одного повышения tier для этой подзадачи,
   не более двух escalation events на job. Порог и cap валидируются в конфигурации.
3. Auto escalation выбирает только явно разрешённый профиль/цепочку с известными
   capabilities. Соблюдать risk tier floor, tools, context+output reserve, local-only,
   cost/latency budgets, credentials и availability. Не выдумывать цены/качество,
   не считать последнего провайдера автоматически более сильным.
4. Manual selection не меняется без отдельного trusted opt-in в execution escalation;
   согласованный transport fallback сохраняет прежнюю семантику. Настройка UI не
   меняет уже выполняющийся запрос задним числом. Local-only исключает облако также
   для planning, summary, semantic classifier и восстановления.
5. Если profiles отсутствуют, честно сообщать configured-chain и отсутствие
   настроенной escalation policy. Durable checkpoint/range-aware recovery должны
   работать и без профилей. Если подходящей модели нет — bounded blocked с причиной,
   а не бесконечный возврат к той же модели. Смена не повторяет tools автоматически.
6. Перед escalation проверять cancellation/lease/deadline. Сохранять прежний и новый
   provider/model, причину, policy, число попыток и checkpoint revision без ключей.

## P07. Связная диагностика parent/child

1. Для принятой авторизованной Web/CLI-задачи создавать родительскую request_attempt
   до начала asynchronous worker. job ID, Web task ID, authorized task ID и
   request ID — разные типы идентификаторов, не подменять их друг другом.
2. Каждая unit/model attempt связана с parent_request_id, job_id, unit_id и
   собственным request_id. Родительский terminal содержит last_failed_request_id,
   safe error code, duration, totals, checkpoint revision и outcome восстановления.
   Internal planned yield не помечать как failed user request.
3. UI-ссылка «Диагностика» должна открывать существующую aggregate-запись и список
   дочерних запросов; прямой ID дочернего запроса открывает именно его ошибку.
   Включить CLI-переход по известному Web task/job ID без ручного поиска SQLite.
4. Старые Web tasks, как в инциденте, разрешать через существующие дочерние записи
   по task_id. Не фабриковать потерянный запрос, время или parent attempt. Если
   журнал очищен retention или logging off, показывать явное недоступно/expired/off,
   а не выдавать несуществующий request ID или сломанную ссылку.
5. Сохранить режимы off/metadata/redacted/full, redaction, size caps, retention,
   hashes, отсутствие diagnostics в retrieval и отдельную rollback boundary.
   Parent/child cleanup не оставляет видимую рабочую ссылку на удалённый child.
   Read-only просмотр статуса/истории не выполняет migration или восстановление.

## P08. Чат, события и понятные сообщения

1. Один общий runtime для CLI и Web. Показать текущий узкий этап, фактически
   прочитанные страницы/строки, remaining coverage, changed files, test status,
   soft yield/resume/escalation и финальную причину остановки на русском.
2. Не показывать бесконечное «анализирует». Heartbeat — отдельно от полезного
   прогресса; длительность — отдельно от timestamp. SSE хранит terminal и
   монотонный cursor; reload/replay не перезапускают LLM или tools.
3. Блокировка не скрывает уже выполненную работу: partial_evidence=true при
   сохранённых фактах, даже если completed_units=0. Это не implementation PASS.
   Если знаменатель полного корпуса неизвестен, показывать unknown, не процент.
4. Не предлагать пользователю разрезать задачу на batch sizes. При обязательном
   выборе пути/прав/конфликте задач просить только действительно недостающую
   информацию. Запрет прав и подтверждение дорогостоящей escalation не обходятся.
5. Не отправлять в status/SSE содержимое файлов, ключи, абсолютные системные пути
   или полный запрос. Safe next_step может содержать лишь ограниченный и очищенный
   текст; подробности доступны только через существующий защищённый diagnostics API.

## P09. Восстановление и совместимость

- Миграции additive, idempotent; старые job/task IDs сохраняются. Для старых receipts
  без диапазонов coverage=unknown: нельзя считать весь файл прочитанным или выдавать
  догадку за восстановленный next_step. Допускается один bounded recovery-planning
  этап с нужными разрешениями, не полный rescan.
- Rollback failed graph не удаляет job ledger/receipts/diagnostics. Нельзя переносить
  старые logs/history в trusted permissions. Current rights intersect saved rights.
- Stale worker не сохраняет checkpoint, не меняет terminal и не запускает write.
  Crash/cancel между side effect и подтверждением разрешается через reconciliation.
- Resume использует ту же БД/workspace/thread/task identity. Новая чистая БД для
  теста изолирована; она не используется как способ «починить» рабочую задачу.
- Сохраняются SSRF, shared exclusions, root protection, exact-once, bounded read/edit
  recovery, read-only, model budgets и транзакционные гарантии существующих версий.

## P13. Достоверная оценка входа и контекстный резерв

1. Нельзя использовать число UTF-8 bytes как число токенов. Для маршрутизации
   применяется единый estimator для текста, сообщений, schemas и checkpoint с
   явным output/safety reserve. Результат содержит метод и границы уверенности.
2. Поддерживаемый tokenizer может быть включён оператором, но его отсутствие,
   неизвестная модель или сбой загрузки не блокируют работу: используется
   консервативная Unicode-aware оценка, не требующая сети.
3. Один estimate передаётся в context filter, cost estimate, диагностику и routing
   metadata. Оценка не выдаётся за billing usage провайдера.

## P14. Наблюдаемый адаптивный выбор моделей

1. Workflow routing, authority/scope и resource routing остаются разными слоями.
   Выбор более сильной модели не выдаёт права чтения, массового обхода или записи.
2. В Web UI оператор может включить/выключить adaptive mode, редактировать
   валидируемые профили `fast/standard/reasoning`, cost/latency/local-only и
   политику execution escalation. Изменение действует только на новые запросы.
3. Политика сохраняется атомарно в `AGENT_DATA_DIR` без ключей. Повреждённый файл
   не ломает запуск: применяется безопасная конфигурация среды и видимое warning.
4. Router учитывает complexity, tools, risk, context+reserve, цену, latency,
   availability и transport reliability. Неизвестные цена/latency не считаются
   нулевыми. Frontier/reasoning model — ограниченный ресурс, а не default.
5. Без профилей применяется видимый `configured-chain`. Ручной выбор и local-only
   не обходятся скрытым fallback/escalation. Решение и фактическая попытка модели
   журналируются без prompt body и секретов.

## P15. Наблюдаемая и измеримая память контекста

1. Web-заголовок чата и Overview показывают фактический режим: FTS5/BM25 и
   состояние vector слоя `disabled/lazy/ready/degraded`, а не только конфигурацию.
2. Hybrid означает SQLite FTS5/BM25 + FastEmbed/Qdrant с RRF. При vector failure
   запрос продолжается через lexical-only; состояние обновляется после поиска и
   индексации. Документы не отправляются внешнему embedding API.
3. Индикатор не должен принудительно загружать embedding model. Он показывает
   последнюю наблюдаемую проверку и безопасное описание fallback без путей/секретов.
4. Качество retrieval проверяется воспроизводимым обезличенным golden corpus:
   минимум hit@k и MRR отдельно для естественных формулировок и точных кодов.
   Порог фиксируется в отчёте; наличие Qdrant не равно доказанному качеству.
5. Смена embedding signature создаёт контролируемую отдельную коллекцию и требует
   явной переиндексации; incremental SHA-256, bounded pages и exclusions сохраняются.

## P10. Обязательная матрица автоматических проверок

| ID | Проверка | Критерий |
| --- | --- | --- |
| A01 | Модель никогда не вызывает save_task_checkpoint | Runtime ledger и next operation существуют до hard limit |
| A02 | Более 13 зависимых операций, recursion=40 | Soft yield, новая unit, тот же task/job, без GraphRecursionError в штатном сценарии |
| A03 | Неожиданный hard limit | Последний подтверждённый курсор сохранён, side effects не replay |
| A04 | 5+ страниц одного файла | Уникальные диапазоны разрешены; нет legacy third-read denial |
| A05 | Повтор/перекрытие/изменение limit | Coverage — union фактических интервалов; duplicate не даёт ложный delta |
| A06 | EOF/empty/denied/error/truncated | Нет ложного продвижения; next cursor только по фактам |
| A07 | Та же страница в новой unit | Известное покрытие не обнуляется и не выдаётся за новый прогресс |
| A08 | Изменение файла извне и перед edit | Версия обновлена, stale excerpt не используется, bounded recovery сохранён |
| A09 | Сбой после write, до receipt; restart | Reconciliation, нет повторной записи вслепую |
| A10 | Crash/yield/lease expiry/cancel | Без stale write/terminal, без вызовов после отзыва прав |
| A11 | Read-only/Ask/Plan, quoted instructions | Нет расширения scope/scan/write; внутренний receipt не меняет goal |
| A12 | Repeated summary/write_todos/new IDs | Не сбрасывают счётчик реального прогресса |
| A13 | Два сбоя этапа, HTTP 200 | Ограниченная escalation при разрешённой policy, без tool replay |
| A14 | Manual/local-only/no profiles/no eligible | Нет скрытой смены/облака; recovery работает без profiles либо честный blocked |
| A15 | Cost/context/tool/latency/risk constraints | Сохраняются во всех recovery/model/summary путях |
| A16 | Parent+children и legacy Web task ID | Диагностика открывает реальные записи, общий task ID не выдаётся за отсутствующий request |
| A17 | Retention/logging off/restart/SSE replay | Понятная доступность журнала, terminal сохранён, нет повторного исполнения |
| A18 | Failed unit с полезными чтениями | UI разделяет partial evidence, units и отсутствие реализации |
| A19 | Разработка → лог → продолжение | Прежний task/job/scope, нет audit manifest, следующий конкретный шаг |
| A20 | Corpus 1 000 000 строк | Bounded RAM/prompt/page sizes, чтение начала/середины/конца и resume курсора |
| A21 | Unicode/token reserve | UTF-8 bytes не равны tokens; method/confidence/reserve едины во всех routing paths |
| A22 | Web adaptive policy | Валидация, atomic persistence/restart, next-request semantics, нет секретов |
| A23 | Resource boundaries | Adaptive/manual/local-only/cost/latency/tools/context не расширяют authority |
| A24 | Memory indicator | disabled/lazy/ready/degraded и lexical fallback соответствуют actual store status |
| A25 | Vector failure | FTS5/BM25 возвращает результат, UI/API видит degraded, embedding не уходит наружу |
| A26 | Retrieval golden set | hit@k/MRR natural/lexical запросов воспроизводимы и не подменены mock PASS |

Тесты должны проверять реальные runtime/middleware/store/API переходы, а не только
текст промпта или mock-финальный PASS. Добавить контроль роста памяти/размера handoff
и bounded DB queries; точные пороги и среду записать в отчёте, не выдумывать замеры.

## P11. Live-приёмка инцидента

1. Только после offline-регрессий подготовить отдельную копию Ozon или воспроизводимый
   обезличенный fixture с большим ТЗ, исходниками и тестами. Исключить .git/.venv,
   секреты, пользовательские БД, кэши, артефакты. Существующий сервер не останавливать
   и исходный Ozon не менять без отдельной необходимости и согласованного окна.
2. Зафиксировать workspace manifest/hashes, отдельный AGENT_DATA_DIR/thread,
   endpoint/ID модели, policy и лимиты. Использовать существующие ключи без вывода.
   Нет необходимости выполнять живой сбор данных Ozon или обращаться к его API.
3. Первый сценарий: исходная постановка сервисного слоя, объём требует более одной
   unit. При доступности использовать точную модель инцидента glm-5.3-flash;
   недоступность этой модели явно отметить, замену не называть точным повтором.
4. Усложнённый сценарий: исходные необходимые чтения занимают >13 операций, один
   файл требует >=5 страниц; намеренно не полагаться на LLM checkpoint. Проверить
   forced handoff, restart на той же БД и продолжение без повторного inventory.
5. Выполнить ограниченное реальное изменение и релевантный тест через runner.
   Подтвердить файлы/hash/exit code после изменения. В точном Ozon-прогоне проверять
   его критерии сервиса; PASS небольшого fixture не равен завершению Ozon-проекта.
6. Повторить весь позитивный сценарий на второй чистой тестовой БД. Отдельно проверить
   deliberate stagnation и разрешённую/запрещённую escalation; injected fault
   отличать в отчёте от естественной ошибки провайдера. Проверить parent/child UI.
7. Приложить safe logs, model attempts, unit transitions, coverage deltas, test
   results, версии и ограничения. Недоступный API/ключ/quota/runner = blocked/skipped
   с причиной, не PASS. Не объявлять live-тест выполненным по одному mock.

## P12. Критерии завершения этапа

Готовность подтверждается A01–A26, серией P11 и успешными Ruff check/format, pytest,
type checks, Web build/tests, compileall, pip check, wheel/sdist. Исправления проходят
повторный полный прогон; live — повтор после значимых изменений recovery.
Версию кода менять только при фактическом выпуске. Документация/история/статус должны
различать план, реализацию, пройденные проверки и оставшиеся ограничения.
Git commit/push и restart пользовательского сервера — отдельные явные действия,
не следствие одной подготовки промпта и ТЗ.
