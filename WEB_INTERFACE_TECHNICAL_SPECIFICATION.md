# Техническое задание: веб-интерфейс Deep Context Agent

## 1. Назначение

Создать локальный production-интерфейс для Deep Context Agent, который даёт
пользователю безопасный доступ к чату, большому индексированному контексту,
пакетным аудитам, файлам workspace, провайдерам и диагностике. Интерфейс не
заменяет CLI и использует те же runtime, SQLite, политики и конфигурацию.

Первая целевая конфигурация — один доверенный пользователь на локальном
компьютере. Удалённый многопользовательский режим не входит в первый релиз.

## 2. Принципы

1. Русский язык по умолчанию; архитектура допускает локализацию.
2. Backend является единственным источником истины. Клиент не принимает
   решений о filesystem/security policy.
3. Большой контекст не отправляется браузеру целиком. Все списки, сообщения,
   источники и находки имеют серверную пагинацию.
4. Секреты никогда не возвращаются клиенту. UI показывает только
   `configured/not configured`.
5. Любая операция отображает фактический статус backend, а не оптимистично
   придуманное завершение.
6. CLI и Web UI должны безопасно работать с одной БД одновременно.

## 3. Технологический контур

- Python 3.11+.
- FastAPI/Starlette и Uvicorn как optional extra `web`.
- Pydantic DTO и OpenAPI.
- Frontend: TypeScript и статическая production-сборка без обязательного CDN.
- Server-Sent Events для токенов, tool events и прогресса; WebSocket допускается
  только при доказанной необходимости.
- SQLite WAL с короткими транзакциями, busy timeout и read-only status queries.
- Все JS/CSS/fonts поставляются локально; интерфейс работает без внешней сети,
  кроме явно запущенного LLM/web tool.

## 4. Запуск

```powershell
.\.venv\Scripts\context-agent.exe web --host 127.0.0.1 --port 8765
```

- Значение host по умолчанию: `127.0.0.1`.
- Bind на внешний интерфейс запрещён без `--allow-remote`, HTTPS/reverse proxy,
  authentication token и явного предупреждения.
- Приложение не должно автоматически открывать браузер без флага.
- `doctor` проверяет web dependencies, занятость порта, доступность БД и
  корректность static bundle.

## 5. Разделы интерфейса

### 5.1. Обзор

- версия приложения;
- активный provider/model и цепочка fallback;
- workspace и data directory в безопасном сокращённом виде;
- число источников, chunks, threads и audit runs;
- активные задачи, последние ошибки и состояние БД;
- быстрые действия: новый чат, индексирование, новый аудит, doctor.

### 5.2. Чат

- выбор/создание thread ID;
- закреплённый внутри чата заголовок с mode, execution, provider, model,
  connection status и context meter;
- динамический server-validated выбор модели для следующего turn с сохранением
  предпочтения thread и неизменяемым snapshot уже активной задачи;
- многострочный ввод, отмена генерации и повтор безопасного запроса;
- потоковый текст и отдельные карточки tool calls;
- ссылки на использованные context sources и evidence;
- provider/model, fallback и продолжительность каждого turn;
- markdown с sanitization; HTML и scripts из ответа не исполняются;
- виртуальные пути отображаются как `/workspace/...`.

### 5.3. Контекст

- индексирование workspace или разрешённого подкаталога;
- hybrid `FTS5/BM25 + FastEmbed/Qdrant` и явный аварийный `lexical-only`;
- прогресс: найдено, проиндексировано, unchanged, skipped, chunks, ошибки;
- partial/timeout/cancel status и продолжение с persisted opaque cursor;
- поиск с `query`, source, kind, top-k и pagination;
- просмотр одного hit и соседнего окна chunks;
- список источников с размером, hash, mtime и последней индексацией;
- удаление источника только из индекса не удаляет исходный файл;
- никакой кнопки «загрузить весь контекст в LLM».

### 5.4. Аудиты

- создание read-only audit по умолчанию;
- отдельный явно подтверждённый переключатель `allow write`;
- выбор ТЗ, include/exclude, batch size и лимитов;
- таблица запусков: run ID, objective, mode, provider, status, coverage,
  severity, elapsed;
- live progress по SSE после каждой пачки;
- фильтры findings по severity/status/path/requirement;
- матрица requirement -> code evidence -> test evidence -> status;
- partial/excluded показываются отдельно с причиной;
- pause, resume и cancel не повреждают manifest;
- экспорт compact/full отчёта в UTF-8 text и JSON.

### 5.5. Файлы workspace

- дерево с ленивой загрузкой и серверной пагинацией;
- чтение текстовых файлов ограниченными страницами;
- создание и редактирование только внутри workspace;
- сохранение через optimistic concurrency по hash/mtime;
- конфликт изменения не перезаписывается молча;
- удаление отключено по умолчанию и требует ввода точного виртуального пути;
- секретные/запрещённые файлы не отображаются и не доступны по прямому URL;
- binary preview только для безопасных поддержанных типов и с лимитом размера.

### 5.6. Провайдеры

- список поддержанных провайдеров и приоритет;
- paginated live-каталог совместимых chat/tool моделей с cache/refresh и
  фильтрацией embedding/media/deprecated entries;
- model/base URL и состояние ключа без значения ключа;
- drag-and-drop приоритета с серверной валидацией;
- `doctor` без live-вызова и отдельная подтверждаемая кнопка live-check с
  предупреждением о стоимости;
- latency и результат последнего check;
- никакой передачи API key в JavaScript/localStorage.

### 5.7. Настройки и диагностика

- безопасные несекретные настройки retrieval, audit, retry и timeout;
- краткий понятный health-status на «Обзоре», а raw diagnostics и safe export —
  только в раскрываемом advanced-разделе;
- значения по умолчанию, effective value и источник env/config;
- просмотр bounded tool audit без тел секретных данных;
- health БД, WAL, свободное место, версия Python и web bundle;
- скачивание диагностического JSON с автоматическим redaction.

## 6. API-контракт

Минимальные endpoint-группы:

- `GET /api/health`;
- `GET /api/runtime`;
- `GET/POST /api/threads`, `GET /api/threads/{id}/messages`;
- `GET/PUT /api/threads/{id}/model-preference`;
- `POST /api/chat`, `POST /api/chat/{turn_id}/cancel`;
- `GET /api/events/{task_id}` для SSE;
- `POST /api/context/index`, `GET /api/context/sources`,
  `GET /api/context/search`;
- `GET/POST /api/audits`, `GET /api/audits/{run_id}`;
- `POST /api/audits/{run_id}/pause|resume|cancel`;
- `GET /api/audits/{run_id}/findings|requirements|report`;
- `GET /api/files`, `GET/PUT/POST/DELETE /api/files/{virtual_path}`;
- `GET /api/providers`, `GET /api/providers/{id}/models`,
  `POST /api/providers/doctor`;
- `GET/PUT /api/settings` только для разрешённых несекретных параметров.

Каждый ответ содержит `request_id`; ошибки используют единый DTO:

```json
{
  "error": {
    "code": "stable_machine_code",
    "message": "Безопасное сообщение пользователю",
    "request_id": "uuid",
    "retryable": false
  }
}
```

Raw exception, traceback, SQL и реальный secret path клиенту не возвращаются.

## 7. Безопасность

1. Same-origin по умолчанию; CORS выключен.
2. CSP запрещает inline/eval и внешние script/style origins.
3. State-changing запросы защищены CSRF token и проверкой Origin.
4. Session cookie: `HttpOnly`, `SameSite=Strict`, `Secure` в HTTPS.
5. Remote mode требует аутентификацию и rate limiting.
6. URL и markdown проходят allowlist/sanitization; `javascript:`, `data:text/html`
   и опасные redirects блокируются.
7. Virtual path нормализуется сервером и повторно проверяется после `resolve()`.
8. Symlink за workspace блокируется.
9. Логи и telemetry редактируют ключи, authorization headers и содержимое
   помеченных секретов.
10. Prompt, документы и результаты retrieval считаются недоверенными данными.

## 8. UX и доступность

- адаптивная ширина от 360 px;
- клавиатурная навигация, видимый focus и skip link;
- WCAG 2.1 AA по контрасту и семантическим ролям;
- aria-live для прогресса и ошибок без спама;
- таблицы имеют header associations и режим карточек на узком экране;
- даты показываются в локальном часовом поясе с доступным UTC tooltip;
- длительные действия никогда не блокируют страницу;
- пустые, loading, partial, paused, failed и retrying состояния различимы;
- destructive confirmation объясняет точный объект и последствия.

## 9. Производительность

- initial compressed JS+CSS не более 500 KiB без отдельного обоснования;
- первая локальная отрисовка до 2 секунд на типовом компьютере;
- API list page по умолчанию не более 100 элементов;
- SSE не передаёт тела больших файлов и полный внутренний state;
- поиск по миллиону строк выполняется сервером, UI получает только top-k;
- таблица 10 000 findings использует серверную pagination или virtualization;
- отменённый browser request отменяет или отсоединяет соответствующего
  подписчика, но не повреждает audit run.

## 10. Тестирование

- unit: DTO, path normalization, sanitization, CSRF, redaction, pagination;
- API: все endpoints, статусы 400/401/403/404/409/422/429/500;
- concurrency: CLI writer + Web status reader, два browser readers;
- frontend: chat, context search, audit progress, filters, file conflict;
- security: traversal, symlink, XSS, dangerous URL, CSRF, secret leakage;
- accessibility: axe или равноценная автоматическая проверка плюс keyboard flow;
- E2E offline: LM Studio/mock model, временный workspace и чистая SQLite;
- Windows: PowerShell 5.1/7, кириллический путь и UTF-8 report download;
- build reproducibility: чистая установка создаёт тот же static bundle.

Тесты не используют реальные платные API и публичную сеть. Live smoke является
отдельным opt-in этапом.

## 11. Критерии приёмки

1. Все функции CLI сохраняются и используют те же данные, что Web UI.
2. Чат работает потоково, показывает provider/fallback и не раскрывает ключ.
3. Контекст из сотен документов ищется без передачи корпуса браузеру или LLM.
4. Аудит большого проекта показывает живой прогресс, resume и компактный итог.
5. Read-only audit не может писать даже при вредоносном prompt.
6. Files API не выходит за workspace через `..`, absolute path или symlink.
7. Русский текст корректен в браузере, JSON и скачанном UTF-8 отчёте.
8. Ни один endpoint не возвращает traceback, SQL, API key или содержимое `.env`.
9. Все Python/TypeScript quality gates и offline E2E проходят.
10. Документация содержит установку optional web extra, запуск, модель угроз,
    резервное копирование и восстановление БД.

## 12. Вне первого релиза

- публичный SaaS и tenant isolation;
- OAuth/SSO и управление организациями;
- мобильное приложение;
- хранение API-ключей в браузере;
- выполнение произвольных shell-команд;
- совместное редактирование одного файла несколькими пользователями.

## 13. Обновление UX и единого API 0.14.0

### 13.1. Архитектурная граница

1. Web UI является только клиентом единого локального API. Chat, audit,
   context, files и providers используют те же `AgentRuntime`, provider
   failover middleware, `ContextStore`, `ProjectAuditStore`, SQLite-файлы и
   workspace policy, что CLI.
2. Запрещены Web-only база сообщений, второй context index, отдельная файловая
   реализация и browser-side вызов LLM provider.
3. Динамическая цепочка провайдеров хранится в thread-safe server registry.
   Каждая новая операция получает immutable snapshot цепочки, поэтому
   перестановка не меняет уже выполняющийся запрос.
4. Изменение live priority действует до перезапуска Web-процесса и не
   переписывает `.env`. Долговременная конфигурация остаётся операторской.

### 13.2. Чат уровня инженерной задачи

1. Основная сущность интерфейса — task/thread. Слева показываются существующие
   thread из context SQLite, новая задача получает безопасный уникальный ID.
2. При выборе thread Web загружает bounded page истории, различает user/agent и
   не считает assistant history доказательством текущего состояния файлов.
3. Composer закреплён снизу, автоматически растёт до ограниченной высоты,
   показывает busy, cancel, failed и completed состояния.
4. Настройка «Enter отправляет» является несекретным device preference в local
   storage и по умолчанию выключена. При включении Enter отправляет сообщение;
   Shift+Enter всегда вставляет перевод строки, IME composition не прерывается.
5. Режимы `agent`, `ask`, `plan`, `debug`, `multitask` доступны на обзоре и в
   чате. Старые названия отсутствуют в production HTML/API. Backend применяет
   их execution/read-write policy и не полагается на JavaScript.

### 13.3. Контекст и аудит

1. `/workspace` означает корень `AGENT_CONTEXT_ROOT`; prefix нормализуется ровно
   один раз. Вложенный путь передаётся как `/workspace/<relative>`.
2. Индексация показывает started, result или safe failed; итог содержит
   `files_indexed`, `files_unchanged`, `files_skipped`, `chunks_written` и
   bounded errors count без закрытых real paths.
3. Поля аудита подписываются `Включить файлы / Include glob`,
   `Исключить файлы / Exclude glob`, `Файлов в пакете / Batch size`.
4. Help-текст объясняет: include ограничивает набор, exclude добавляет
   исключения, batch size — число файлов в одном bounded LLM step; рекомендуемое
   значение 8. Пустые include/exclude допустимы.

### 13.4. Файлы

1. Directory list возвращает virtual path и кликабельный тип. UI поддерживает
   открытие каталога, переход вверх и ручной virtual path.
2. UTF-8 файл до 2 MiB открывается bounded page. Если весь файл не получен,
   preview помечается как частичный и write блокируется.
3. Полное содержимое сохраняется только с `expected_sha256`; конфликт даёт 409
   и требует перечитать. UI не выполняет silent overwrite.
4. Secret filtering, resolved path/symlink checks, запрет корня и
   disabled-by-default delete применяются на сервере независимо от UI.

### 13.5. Live-провайдеры

1. UI показывает configured catalog без ключей и ordered active chain.
2. В цепочку можно добавить только server-configured provider; последний
   активный provider удалить нельзя; дубликаты запрещены.
3. Move up/down, add и remove атомарно валидируют всю новую цепочку и применяют
   её ко всем последующим chat/audit/doctor вызовам.
4. У каждого активного provider есть `Проверить / Live check`. Remote-
   проверка требует явного подтверждения возможной оплаты. Loopback-
   проверка запускается без payment dialog и помечается «без платы API».
5. API никогда не сериализует credential, authorization header или raw SDK
   exception. Missing configuration возвращает safe 422.

### 13.6. Настройки и обзор

1. Обзор предлагает инженерные режимы и кратко описывает ожидаемый результат;
   выбор синхронизирует mode и открывает чат.
2. Технический health JSON находится в сворачиваемом блоке и не является
   основным пользовательским экраном.
3. Настройки отображаются отдельными строками: bilingual label, env name,
   numeric value и русский комментарий. Секретные переменные отсутствуют.
4. PUT settings принимает только server allowlist, атомарно откатывает env при
   ошибке и повторно создаёт/валидирует `AppConfig`.

### 13.7. Дополнительная приёмка 0.14.0

1. API regression доказывает, что `/workspace` не удваивается.
2. Index task на реальном временном документе выдаёт result и completed.
3. Provider reorder немедленно отражается в `/api/runtime`, а serialized body
   не содержит test secret.
4. Bundle содержит wiring index, provider priority, Enter preference и SHA.
5. Browser E2E подтверждает role -> chat, реальный primary response, Enter send,
   Shift+Enter newline, context index, file preview и provider reorder/restore.
6. Адаптивный viewport 390x844 сохраняет все семь разделов в нижней навигации,
   читаемые mode cards и доступный keyboard flow.
7. Console error/warning log пуст после bootstrap и основных переходов.

### 13.8. Provider/files acceptance 0.15.0

1. LM Studio doctor читает bounded `/models`, отличает unreachable,
   empty catalog и missing model. Placeholder `local-model` заменяется
   загруженной не-embedding model и отражается в catalog.
2. `POST /api/providers` принимает только `custom-*`, model и URL.
   Extra/browser secret отклоняется. Remote HTTP, URL credentials,
   query/fragment отклоняются; remote API key остаётся в server env.
3. Созданный profile можно атомарно добавить в active chain,
   переместить, проверить и убрать до перезапуска process.
4. Files `Назад` возвращает предыдущий просмотр, `Выше` открывает
   parent, а `Открыть` всегда выдаёт видимый status. В корне Back/Up
   disabled пока нет history/родителя.
5. Unit/API/bundle и real browser E2E подтверждают local-free label,
   no-confirm LM Studio, custom provider flow, history/up/open status и нулевые
   console errors.

### 13.9. Stale-edit errors 0.16.0

1. Web chat и audit используют общий runtime stale-edit recovery; browser не
   реализует собственный retry и не повторяет mutation.
2. В SSE/chat не попадает failed `old_string`, raw file content или physical path.
   Финальное unresolved state показывает safe conflict guidance и verified
   error/denied tool evidence.
3. Invalid `read_context_window` arguments и runaway budget exhaustion
   отображаются как safe tool status; Web task не падает с raw
   `ValueError` и не реализует browser-side retry.

### 13.10. Long-task recovery and diagnostics 0.17.0

1. Chat/audit model input применяет общий runtime active-context budget;
   браузер не обрезает историю и не создаёт собственную memory ветку.
2. `GET /api/tasks/{task_id}` возвращает `running` либо bounded terminal
   status/data. Raw provider exception, credential и physical path запрещены.
3. Завершённая задача сохраняет terminal event для повторного
   `GET /api/events/{task_id}`. Reconnect после сетевого разрыва не должен
   зависать или возвращать немой пустой результат.
4. Failure DTO использует стабильные коды `context_window_exceeded`,
   `quota_exhausted`, `rate_limited`, `authentication_failed`,
   `provider_timeout`, `provider_unavailable`, `agent_step_limit` либо
   `provider_chain_failed` и даёт краткое действие оператору.
5. Закрытие браузером/SSE Windows-соединения не засоряет server log известным
   Proactor reset 10054. Иные asyncio exceptions не подавляются.

### 13.11. Persistent failed-request diagnostics 0.18.0

1. Web task и failed request имеют общий `request_id`; sanitized terminal event
   хранится в `diagnostics.sqlite3` и доступен после restart.
2. Список diagnostics серверно пагинируется и по умолчанию возвращает только
   UTC, IDs, kind, safe code, provider attempts, duration и rollback status.
3. Текст запроса зависит от server mode `off|metadata|redacted|full`. Клиент не
   может переключить режим на `full` и не передаёт credentials.
4. Просмотр/export сохранённого query требует local/authenticated operator,
   явного include-query, CSRF для мутаций и отображения retention/privacy warning.
5. Purge удаляет точный диапазон/ID после подтверждения; UI не выполняет VACUUM
   в request thread и отображает фактическое число удалённых записей.
6. Raw SDK response, exception, traceback, SQL, environment и physical path не
   сериализуются. Все ответы содержат request ID для корреляции с локальным
   structured log.
7. Browser acceptance воспроизводит failed turn, перезапускает app, получает
   terminal state, находит diagnostic summary и доказывает отсутствие fixture
   secret во всех API/exports.
8. Rollback использует secure checkpoint deletion и WAL truncate; browser/live
   acceptance дополнительно сканирует DB/WAL/JSONL/export побайтово, чтобы
   удалённый failed prompt не оставался физическим артефактом.

### 13.12. Автопилот длительных задач / Long-running autopilot 0.19.0

1. Persistent Autopilot использует основной чат; отдельная вкладка не нужна.
   Оператор вводит цель, выбирает read-only/allow-write и execution mode. Поля
   batch size и max batches отсутствуют в основном UX.
2. `POST /api/jobs` создаёт или возобновляет persistent job и возвращает
   `job_id`, `task_id`, mode. `GET /api/jobs` и `/api/jobs/{id}` доступны без
   LLM; control endpoints защищены CSRF/auth.
3. SSE передаёт `job_progress`, `job_replanned`, `job_verification`, result и
   terminal. После reconnect terminal и последний persistent progress доступны
   через task/job status.
4. Карточка показывает phase, reviewed/total, pending, attempts, replans,
   внутренний current batch как диагностику и safe blocker. Raw exception,
   credential, provider payload и физический путь запрещены.
5. Pause/cancel вступают в силу между атомарными units. Resume не меняет
   read-only/allow-write mode и продолжает тот же manifest/hash progress.
6. После Web restart interrupted unit восстанавливается в pending; job можно
   автоматически или одной кнопкой продолжить без повторного ввода цели.
7. Browser acceptance воспроизводит прежний broad Ozon request: UI не требует
   ручного деления, несколько work units видны в прогрессе, step-limit unit
   вызывает replan, а terminal соответствует сохранённой SQLite job.

### 13.13. Autopilot внутри чата / Chat-integrated autopilot 0.19.1

1. В шапке чата доступен selector `Авто / Autopilot / Обычный ответ`.
2. Explicit Autopilot не зависит от ключевых слов. Auto использует server-side
   classifier и после безопасно откатанного step/context limit переключается в
   persistent job. Explicit single-turn запрещает такое переключение.
3. События execution/job progress обновляют pending bubble и отдельную строку
   статуса. Пользователь видит job ID, фазу, files, units и replans.
4. Список persisted jobs текущего thread находится в боковой панели чата;
   отдельные nav button, panel и form Autopilot отсутствуют в production HTML.
5. Завершённый Autopilot архивирует turn в исходный thread, поэтому reload не
   теряет постановку и итог. UI считает `user` и `human` пользовательскими
   ролями.
6. Regression использует точный запрос из production-журнала, explicit short
   job, automatic step-limit fallback и explicit single-turn negative case.

### 13.14. Durable lease и живой прогресс / Heartbeat and fencing 0.20.0

1. `job_heartbeat` обновляет pending message без создания нового сообщения:
   отображаются «модель работает», phase, generation и время последней связи.
2. `job_deadline` означает превышение soft unit deadline, но не ложный terminal;
   UI сообщает о безопасном завершении текущей unit и последующей переоценке.
3. `GET /api/jobs/{id}` возвращает safe `lease_generation`,
   `last_heartbeat_at`, `active_unit_started_at` и interrupted count. Token,
   physical workspace, raw exception и provider payload не возвращаются.
4. Reconnect/reload получает те же timestamps из SQLite; browser не генерирует
   heartbeat и не считается источником состояния владения.
5. Engineering modes в `auto` сразу используют Autopilot для action +
   code/spec/test/module задач. Явный `single-turn` не повышается автоматически.
6. Browser/live regression удерживает unit дольше прежнего lease, видит не менее
   двух heartbeat, не получает premature failure и после restart видит
   `interrupted`/новую generation либо корректный terminal result.

### 13.15. Пять режимов чата / Five chat modes 0.21.0

1. `Agent` — основной до результата; `Ask` — только чтение; `Plan` — вопросы и
   план до одобрения; `Debug` — hypothesis/log/reproduce/analyze; `Multitask` —
   несколько независимых фоновых задач.
2. Ask/Plan отключают write в UI и backend. Agent/Debug предлагают write, но
   trusted boolean и общие filesystem policies остаются обязательными.
3. Multitask разрешает несколько одновременных composer submissions, показывает
   отдельный pending bubble/SSE каждой task и кнопку отмены всех активных.
4. Server присваивает каждой Multitask task child thread; одновременные writes в
   один checkout требуют внешней worktree isolation и явно предупреждаются UI.
5. В правой нижней части composer расположен доступный круговой meter с percent,
   tooltip и `aria-valuenow`. Цвет меняется на warning у границы compacting.
6. `GET /api/threads/{id}/messages` дополнительно возвращает aggregate
   `context_usage`; тела сообщений остаются bounded указанной page.
7. UI поясняет automatic Deep Agents summary. Полная SQLite history не удаляется
   и остаётся доступной context search после active-context compaction.
8. Browser/API tests проверяют ровно пять modes, legacy 422, read-only policy,
   параллельную отправку, child IDs и meter без утечки дополнительной истории.

### 13.16. Модели, hybrid memory и bounded scans / Models and retrieval 0.22.0

1. Заголовок внутри чата закреплён под общей topbar и содержит thread, mode,
   execution, provider, model, connection status и context meter. История имеет
   независимую прокрутку; на экране 360 px controls доступны без горизонтального
   overflow.
2. Provider/model selectors применяются только к следующему turn. Активный task
   сохраняет immutable snapshot. Выбор хранится на сервере для thread; browser
   localStorage может быть только UX-cache, но не источником исполнения.
   Chat provider select содержит ту же активную chain и в том же порядке, что
   раздел «Провайдеры». Каждая активная строка provider имеет validated model
   dropdown; смена модели там или в чате синхронно обновляет process-default,
   текущий thread preference и обе видимые controls.
3. Каталог моделей загружается сервером с timeout/TTL/pagination. UI группирует
   `Авто`, `Качество`, `Баланс`, `Экономия`, `Локально` и полный совместимый
   список; embedding/image/audio/moderation/deprecated entries исключаются из
   chat selector. `unverified` явно маркируется и не выдаётся за tool-capable.
4. Ответ и terminal card показывают фактические provider/model, fallback и
   duration. Переключение приоритета или модели не изменяет активный запрос.
5. Раздел «Контекст» показывает `hybrid`, `lexical-only`, состояние FastEmbed и
   Qdrant, pinned embedding signature без физического пути. Никакие документы
   не отправляются внешнему embedding API.
6. Index/search/discovery показывают authoritative counters: discovered,
   scanned, matched, indexed, unchanged, skipped, excluded, chunks и errors.
   Повторный index отображает нулевые значения, а не только ненулевые.
7. `partial`, `timed_out`, `paused`, `cancelled`, `failed`, `degraded` и
   `complete` имеют разные текстовые/визуальные состояния. Partial card содержит
   причину, counts и кнопку `Продолжить`, если сервер вернул cursor.
8. Resume использует persisted opaque cursor и task/job status. Reload или SSE
   reconnect не начинает новый полный обход и не дублирует уже показанные items.
9. Raw Technical diagnostics на «Обзоре» заменяется понятным статусом
   `Система готова / Ограниченный режим / Требуется внимание`. Детальный JSON и
   безопасный export доступны в `Настройки → Дополнительно → Диагностика`.
10. Terminal diagnostics любой задачи содержат safe provider/model snapshot,
    duration, file counts, partial reason и correlation ID. `200`, `202` и SSE
    connection сами по себе не отображаются как успешное завершение.
11. Browser/API/E2E acceptance покрывает sticky/mobile header, dynamic catalog,
    per-thread switch, in-flight immutability, fallback label, hybrid/lexical
    status, partial/resume, repeated index counts и redacted diagnostics.
12. Полный нормативный контракт и последовательность реализации заданы в
    `DEEP_CONTEXT_AGENT_0_22_HYBRID_RETRIEVAL_MODEL_UI_PROMPT.md`.

### 13.17. Структурированная маршрутизация / Structured routing 0.23.0

1. `POST /api/chat` возвращает safe routing object: `execution`, `workflow`,
   `scope`, `allow_project_scan`, `mutation_requested`, `confidence`, reason
   codes и только размеры instruction/data segments.
2. Backend классифицирует direct instruction, исключив fenced/quoted/tagged
   content и узнаваемые terminal/traceback payload. JavaScript не повторяет
   classifier и не является источником authority.
3. Auto log/error explanation выполняется single-turn без project manifest,
   даже если журнал содержит команды исправить проект и запустить тесты.
4. Explicit Autopilot сохраняет определённый workflow. Для log-analysis и
   targeted workflows показывается durable execute progress без фиктивного
   файлового счётчика; project workflows показывают manifest progress.
5. Trusted write checkbox действует только совместно с прямым mutation intent.
   Текст внутри журнала или вложения не может активировать запись.
6. UI показывает понятную строку `Маршрут: workflow · один ход/длительная
   задача · область`, а SSE terminal повторяет фактически исполненное решение.
7. Structured JSONL фиксирует решение по task/thread ID без query body,
   вложений, secret, physical path или raw provider response.
8. Browser/API/live acceptance повторяет исходный PowerShell-log не менее пяти
   раз и подтверждает отсутствие `chat_autopilot`/project audit; отдельный
   explicit persistent log test подтверждает `execute` unit и `audit_run_id`
   `NULL`.
