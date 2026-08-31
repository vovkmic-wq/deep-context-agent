# Deep Context Agent 0.19.0 — persistent autopilot orchestrator

## 1. Цель

Пользователь описывает результат и функциональность одной задачей. Он не обязан
вычислять размер пачки, число запусков, глубину graph или вручную продолжать
работу после безопасного лимита шагов. Deep Context Agent обязан превратить
такой запрос в долговечную job, самостоятельно дробить её на ограниченные
work units, сохранять прогресс и доводить разрешённую работу до проверяемого
результата.

Лимит одного model/tool graph остаётся защитой от зацикливания. Его достижение
является ошибкой work unit, а не всей пользовательской задачи.

## 2. Установленная первопричина

1. Корпус в 1 000 000+ строк хранится в SQLite/FTS и не должен целиком попадать
   в prompt модели.
2. Ранее широкая задача могла остаться одним graph invocation. Большие `glob`,
   повторные чтения и действия модели исчерпывали `recursion_limit`.
3. Существующий пакетный аудит сохранял file manifest, но ограничивал один
   процесс ручным `max_batches` и показывал пользователю внутренний batch size.
4. При ошибке пачка возвращалась в pending, однако отсутствовал верхнеуровневый
   controller, который автоматически уменьшает её, меняет worker thread,
   повторяет проверку и формирует единый итог.

## 3. Пользовательский контракт

1. Новый режим `job` принимает цель, thread ID и явное `--allow-write`.
2. По умолчанию job read-only; текст задачи не расширяет права записи.
3. Пользователь не вводит batch size или max batches. Это внутренние адаптивные
   параметры, доступные оператору только через environment.
4. CLI ждёт job до терминального состояния. Web возвращает task/job ID и
   передаёт прогресс через тот же persistent SSE/task механизм.
5. Состояния: `queued`, `running`, `paused`, `blocked`, `cancelled`, `complete`.
6. Терминальный `complete` допустим только после завершения file manifest и,
   для allow-write job, после фактических production checks.
7. `blocked` допустим только для внешнего блокера или исчерпания ограниченных
   безопасных стратегий; сообщение обязано содержать стабильный safe code.

## 4. Долговечное состояние

1. Создать отдельную `autopilot.sqlite3` в `AGENT_DATA_DIR`, вне workspace.
2. Таблица jobs хранит identity, objective hash/текст, workspace, mode, phase,
   status, audit run ID, текущий batch size, attempts/replans, safe error,
   verification summary, timestamps и итоговый отчёт.
3. Таблица work units хранит последовательность, отдельный worker thread ID,
   phase, status, размер пачки, attempt, error code и bounded summary.
4. Все переходы атомарны, БД использует WAL, foreign keys и busy timeout.
5. При открытии store зависшие `running` units возвращаются в `pending`, а job
   получает resumable `paused`, не теряя уже завершённые units.
6. Повтор с той же workspace/thread/objective/mode возобновляет ту же job.

## 5. Планирование и исполнение

1. Детерминированно сформировать безопасный file manifest существующим
   `ProjectAuditStore`; generated, cache, secret и binary paths исключить до LLM.
2. Один work unit вызывает не более одной audit batch и использует новый
   namespaced worker thread. В prompt попадают только выделенные пути,
   summaries, применимые требования и bounded evidence.
3. Успешные файловые операции из ToolMessage — единственный источник прогресса.
4. Непрочитанные файлы остаются pending. Изменённый SHA возвращает только
   изменённый файл в очередь.
5. При `agent_step_limit` или `context_window_exceeded` вернуть пачку в pending,
   уменьшить batch size вдвое вплоть до 1, зарегистрировать replan и продолжить
   в новом worker thread.
6. Transient provider failures допускают ограниченный автоматический retry с
   новым work unit; authentication/quota и повторяющаяся ошибка batch size 1
   переводят job в `blocked` с сохранённым прогрессом.
7. Нулевой доказанный прогресс также уменьшает пачку; после ограниченного числа
   попыток одного файла job блокируется, не зацикливаясь.
8. Для allow-write batch модель сначала анализирует, затем изменяет только
   разрешённые пути и использует stale-edit recovery. Новые пути не создаются
   без отдельного явно разрешённого implementation unit.

## 6. Проверка и ремонт

1. После завершения manifest allow-write job запускает фиксированные
   `ruff_check`, `ruff_format_check`, `pytest`; при наличии конфигурации также
   может запускать `mypy`/`compileall` через существующий allowlist.
2. Нельзя передавать пользовательскую shell-команду или секреты в subprocess.
3. FAIL создаёт bounded repair unit с точным safe output и новым thread ID.
4. После подтверждённой мутации проверки запускаются повторно. Старый PASS не
   переносится через изменение файлов.
5. После ограниченного числа repair cycles job становится `blocked`, а не
   ложным `complete`.

## 7. CLI, Web API и UX

1. CLI: `context-agent job [query|--file] [--allow-write] [--report-file]` и
   `job-status --job-id ... [--json]`.
2. Web: `POST/GET /api/jobs`, `GET /api/jobs/{id}`,
   `POST /api/jobs/{id}/pause|resume|cancel`, отчёт и список work units.
3. SSE события: `job_progress`, `job_replanned`, `job_verification`, terminal.
4. Главная форма «Автопилот» не показывает размер пачки/число пачек. Include и
   exclude остаются необязательными расширенными фильтрами с русско-английскими
   подписями.
5. Отображать phase, reviewed/total, current batch size как диагностику,
   attempts/replans, verification и safe blocker; не показывать raw exception,
   физические секретные пути или provider payload.

## 8. Наблюдаемость и безопасность

1. Каждая model attempt продолжает записываться в durable failure journal.
2. job ID, work unit ID, task ID и request ID должны быть взаимно связаны.
3. Мутация разрешается только доверенным boolean из CLI/API и только внутри
   workspace. Read-only job не может стать writable при resume.
4. Pause/cancel проверяются между work units; уже выполняющаяся атомарная
   операция завершается или корректно откатывает graph checkpoint.
5. Cost confirmation применяется к фактическому remote provider attempt;
   LM Studio остаётся local-free.

## 9. Контекст LangChain и целевого Ozon-проекта

1. Использовать Deep Agents для bounded planning/tool use и context isolation,
   LangGraph/SQLite — для durable execution между единицами.
2. Чекпоинт ставится на границе work unit. Side effects должны быть
   идемпотентны, потому что unit может быть повторён после restart.
3. Полный корпус остаётся в file ledger/FTS; active context содержит только
   релевантные фрагменты и компактные результаты завершённых units.
4. Проверка Ozon учитывает его `src/`, `tests/`, `webui/`, `e2e/`,
   `TECHNICAL_SPECIFICATION.md`, Python 3.11, Ruff, mypy и pytest. Cache,
   reports, data, virtualenv и browser artifacts исключаются из manifest.
5. GitHub-репозиторий Ozon может быть private: доступность через авторизованный
   GitHub CLI является external context, но workspace остаётся источником истины
   для изменений и тестов.

## 10. Обязательные тесты

1. Store schema, identity/resume, crash recovery, transition validation,
   concurrency и workspace isolation.
2. Успешная multi-batch job без ручного max batches.
3. Инъекция `GraphRecursionError`: первая пачка падает, batch уменьшается,
   worker thread меняется, job завершается без сообщения пользователю
   «разделите задачу».
4. Повторный step-limit при batch size 1 даёт bounded `blocked` и сохраняет
   ранее завершённый progress.
5. Provider transient retry и authentication/quota blocker.
6. Read-only не мутирует; allow-write не выходит за workspace.
7. Verification FAIL → repair → повторная verification PASS и отдельный тест
   исчерпания repair cycles.
8. CLI parsing/output/status/report, Web CRUD/control/SSE, CSRF/auth/redaction.
9. Корпус 1 000 001 строк: поиск начала/конца, bounded active prompt и complete
   manifest без единого огромного glob output.
10. Ruff, format, mypy, pytest, web bundle, package build/install smoke.

## 11. Live acceptance

1. `doctor --live` для основной provider chain без печати ключей.
2. Реальный tiny-workspace job через основной provider и fallback metadata.
3. Повтор сценария прежнего отказа: broad objective запускается как job,
   прогресс проходит несколько worker threads и не завершается
   `agent_step_limit` на уровне пользователя.
4. Restart/resume на чистой временной БД, проверка terminal report и durable
   diagnostics.
5. Любая найденная регрессия исправляется, после чего полный набор и live smoke
   выполняются повторно.

## 12. Definition of Done

Этап завершён только если документация соответствует коду, миграции обратимо
открывают существующие БД, автоматические и live тесты имеют фактические логи,
версия обновлена, wheel устанавливается в чистое окружение, git diff не содержит
секретов/пользовательских артефактов, commit/tag опубликованы в GitHub.
