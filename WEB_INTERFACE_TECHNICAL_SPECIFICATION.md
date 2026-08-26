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
- многострочный ввод, отмена генерации и повтор безопасного запроса;
- потоковый текст и отдельные карточки tool calls;
- ссылки на использованные context sources и evidence;
- provider/model, fallback и продолжительность каждого turn;
- markdown с sanitization; HTML и scripts из ответа не исполняются;
- виртуальные пути отображаются как `/workspace/...`.

### 5.3. Контекст

- индексирование workspace или разрешённого подкаталога;
- прогресс: найдено, проиндексировано, unchanged, skipped, chunks, ошибки;
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
- model/base URL и состояние ключа без значения ключа;
- drag-and-drop приоритета с серверной валидацией;
- `doctor` без live-вызова и отдельная подтверждаемая кнопка live-check с
  предупреждением о стоимости;
- latency и результат последнего check;
- никакой передачи API key в JavaScript/localStorage.

### 5.7. Настройки и диагностика

- безопасные несекретные настройки retrieval, audit, retry и timeout;
- значения по умолчанию, effective value и источник env/config;
- просмотр bounded tool audit без тел секретных данных;
- health БД, WAL, свободное место, версия Python и web bundle;
- скачивание диагностического JSON с автоматическим redaction.

## 6. API-контракт

Минимальные endpoint-группы:

- `GET /api/health`;
- `GET /api/runtime`;
- `GET/POST /api/threads`, `GET /api/threads/{id}/messages`;
- `POST /api/chat`, `POST /api/chat/{turn_id}/cancel`;
- `GET /api/events/{task_id}` для SSE;
- `POST /api/context/index`, `GET /api/context/sources`,
  `GET /api/context/search`;
- `GET/POST /api/audits`, `GET /api/audits/{run_id}`;
- `POST /api/audits/{run_id}/pause|resume|cancel`;
- `GET /api/audits/{run_id}/findings|requirements|report`;
- `GET /api/files`, `GET/PUT/POST/DELETE /api/files/{virtual_path}`;
- `GET /api/providers`, `POST /api/providers/doctor`;
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
