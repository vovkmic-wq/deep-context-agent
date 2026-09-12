# ТЗ 0.27: долговечный планировщик и доказуемое продвижение задачи

## Дополнение к завершению 0.32 — запланировано

Нормативное дополнение: [ТЗ R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
и [порядок реализации](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).
Оно уточняет сохраняемый verification context, полный check plan, устойчивое
владение/reconciliation, сквозной REPAIR, Web/API и live/release gates.
Исходные A01–A34 и ограничения прав остаются обязательными. Опубликованная ветка
и 449 passed не означают закрытие production-приёмки. В этом этапе изменены
только документы; код новых требований ещё предстоит реализовать.

## Запланированное уточнение 0.32

[ТЗ жизненного цикла](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md)
L01–L05 уточняет S02: heartbeat продлевает не только job, но и связанную saved
task; checkpoint revision, fencing generation и budgets различаются. Длительный
model call/VERIFY не должен оставлять saved-task lease без обновления.
Terminal state проецируется из durable outcome с CAS и обработкой finish=False.
Это план исправления выявленного дефекта 0.31.0, не подтверждение его устранения.

Статус: нормативный документ реализованного этапа 0.27.0. Сам документ не является
доказательством PASS; фактические automated/live evidence и ограничения записаны
в `IMPLEMENTATION_STATUS.md`.

Связанные документы:

- `DEEP_CONTEXT_AGENT_0_27_DURABLE_SCHEDULER_PROMPT.md` — порядок реализации;
- `DEEP_CONTEXT_AGENT_0_27_WEB_DURABLE_SCHEDULER_PROMPT.md` — Web/API/UX;
- `AUTOPILOT_PROGRESS_RECOVERY_TECHNICAL_SPEC.md` — инварианты 0.26;
- `TASK_RESUME_MODEL_ROUTING_SPEC.md` — identity, permissions и model routing.

## 1. Подтверждённый инцидент

Диагностика `83bbc5d39bf7410b87462378c5259487`, job
`5dc5c86b57fd25f873da7acd`. Persistent project-change продолжился после restart,
но был остановлен общим `task_deadline` примерно через 902 секунды.

- 11 units: 7 `soft_yield`, 4 failed, 0 completed;
- 12 чтений, 960 уникальных строк, несколько повторных search/list/denied reads;
- 0 changed files, 0 checks, verification `not_run`;
- `next_operation` оставался описательным: прочитать ещё несколько файлов и начать
  history store → pipeline → export;
- provider/vector transport не был причиной terminal state.

0.26 устранил исчерпание failure retries плановыми yield, но не отделил срок одного
model turn от активного времени job и абсолютного срока жизни задачи. Чтение новых
данных могло продлевать discovery без перехода к реализации.

## 2. Цель и архитектурный выбор

Пользователь формулирует бизнес-задачу один раз и не рассчитывает размеры этапов.
Runtime сам создаёт конечные work units и продолжает их через долговечную очередь.
Production-основа объединяет:

1. фазовый автомат `discover → plan → implement → verify → repair`;
2. persistent scheduler, отделённый от HTTP/SSE и одного процесса;
3. evidence-driven progress/renewal;
4. раздельные model/unit/task/wall-clock бюджеты как базовую настройку.

Уточнение 0.30: этот общий автомат относится к разработке. Workflow
`verification-only` использует отдельную ветку QUEUED→VERIFY→COMPLETE либо
VERIFY→REPAIR→VERIFY и не создаёт discovery/implementation unit.

Система остаётся ограниченной: durable не означает бесконечное выполнение. Права,
privacy/local-only, стоимость, токены, число units, операции и абсолютный TTL не
сбрасываются при handoff, restart или смене модели.

## S01. Независимые временные бюджеты

Runtime вводит отдельные значения:

| Настройка | Назначение | Значение по умолчанию |
| --- | --- | --- |
| `AGENT_MODEL_TURN_TIMEOUT_SECONDS` | один вызов LLM | 180 |
| `AGENT_AUTOPILOT_UNIT_TIMEOUT_SECONDS` | одна work unit | 900 |
| `AGENT_AUTOPILOT_TASK_ACTIVE_TIME_SECONDS` | суммарное активное вычисление job | 14400 |
| `AGENT_AUTOPILOT_MAX_WALL_TIME_SECONDS` | абсолютный TTL job | 86400 |

`AGENT_TASK_TIMEOUT_SECONDS` сохраняется как legacy timeout одноходового ask/chat,
но не должен завершать persistent job целиком. Значения валидируются, отражаются в
doctor/settings и не переписываются скрыто.

Активное время включает model/tool/runtime execution. Время `paused`, очередь без
выделенного worker, downtime процесса и ожидание обязательного решения пользователя
не расходует active-time budget. Wall-clock TTL продолжает идти и ограничивает
забытые задачи. Метрики времени основаны на монотонных интервалах исполнения;
UTC timestamps служат журналу и restart recovery.

## S02. Durable scheduler

1. Приём Web/CLI-задачи сохраняет job и queue record в SQLite до ответа клиенту.
2. HTTP возвращает task/job ID; жизненный цикл не принадлежит открытому SSE.
3. Worker атомарно claim-ит одну unit через owner, generation, lease и CAS revision.
4. Для job одновременно активна не более чем одна mutation-capable unit.
5. Изменение состояния и outbox event записываются транзакционно в выбранной
   owner-БД. Между отдельными SQLite применяются идемпотентные проекции/ACK и
   recovery по L04/L05 этапа 0.32, а не обещание одной межбазовой транзакции.
6. После crash просроченный lease переводится в interrupted/requeue без повторения
   подтверждённых side effects.
7. Restart поднимает scheduler и продолжает разрешённые queued/running jobs после
   reconciliation. Отдельное явное operator pause/cancel имеет приоритет.
8. Scheduler не создаёт project-audit manifest для project-change/project-test.
9. Retries транспорта, retries unit и переход между фазами имеют разные счётчики.
10. Все side-effect tools используют idempotency/evidence receipts; смена provider
    не разрешает replay успешной мутации.
11. Shared ownership controller продлевает task/job leases с проверкой обеих
    аренд; старый worker не возрождает expiry и не завершает новую generation.
12. Uncertain side effect после crash до receipt требует сверки, не автоматического
    повторного исполнения. Pending terminal projections сохраняются до ACK.

## S03. Фазовый автомат

Допустимые основные состояния:

```text
QUEUED → DISCOVER → PLAN → IMPLEMENT → VERIFY → COMPLETE
                              ↑          ↓
                              └─ REPAIR ─┘

VERIFICATION_ONLY: QUEUED → VERIFY → COMPLETE
                              ↓
                           REPAIR ─→ VERIFY
```

Дополнительные состояния: `PAUSED`, `WAITING_USER`, `BLOCKED`, `CANCELLED`,
`FAILED`, `INTERRUPTED`. Переход хранится с reason code и evidence IDs.

- `DISCOVER`: только минимальные необходимые файлы/контекст.
- `PLAN`: executable operations с путями, входами и проверками.
- `IMPLEMENT`: мутации только при текущем `allow-write`; иначе read-only result.
- `VERIFY`: реально запущенные allowlisted checks, не model claim.
- `REPAIR`: исправление конкретного failed check; число циклов ограничено.
- `COMPLETE`: требования закрыты и обязательная verification прошла.

Возврат из IMPLEMENT/VERIFY в полный DISCOVER запрещён. Разрешено точечное
`targeted-discovery` для одного доказанного пробела с отдельным малым бюджетом.
Для `verification-only` DISCOVER, PLAN и общий IMPLEMENT отсутствуют; выбор
project root выполняет bounded deterministic resolver из ТЗ 0.30.

## S04. Ограничение discovery-only работы

Настройки:

| Настройка | Default |
| --- | --- |
| `AGENT_DISCOVERY_MAX_UNITS` | 2 |
| `AGENT_DISCOVERY_MAX_READS` | 20 |
| `AGENT_DISCOVERY_MAX_UNIQUE_LINES` | 2000 |
| `AGENT_DISCOVERY_MAX_SEARCHES` | 8 |
| `AGENT_TARGETED_DISCOVERY_MAX_UNITS` | 1 |

Достижение любого ceiling требует перехода в PLAN/IMPLEMENT или точного blocker.
Нельзя продлевать discovery heartbeat, переформулировкой того же поиска, повторным
listing, covered range, denied/error tool result или чтением нерелевантного файла.

Для известного пути используется `read_file`, для неизвестного символа — один
ограниченный grep/search. После denied result модель получает разрешённые варианты
следующего действия и не должна угадывать обход ограничения.

## S05. Evidence-driven progress и продление

Новый evidence относится к одному из видов:

- `information`: новый релевантный диапазон/символ/требование;
- `implementation`: подтверждённая create/edit/delete receipt с before/after hash;
- `verification`: новый фактический check result;
- `decision`: executable plan или структурированный blocker с достаточными фактами.

Не являются новым evidence: повтор запроса, повтор уже покрытого диапазона, heartbeat,
model prose, HTTP 200, сохранение того же checkpoint и смена provider без результата.

Successful handoff:

1. атомарно завершает текущую unit как `yielded`;
2. сохраняет новые receipts, range union, phase и executable next operation;
3. выдаёт следующей unit новый unit deadline;
4. уменьшает общий active-time/token/cost/tool budget;
5. не продлевает wall TTL и не обнуляет failure/replan/repair counters.

Если delta отсутствует, unit не requeue-ится автоматически бесконечно. После
ограниченного replan выбирается escalation или terminal blocker.

## S06. Исполнимая следующая операция

`next_operation` хранится не только как текст, но и как валидируемая структура:

```json
{
  "phase": "implement",
  "operation": "create_file",
  "target": "/workspace/project/src/history/store.py",
  "objective": "Implement SQLite snapshot history",
  "required_evidence_ids": ["receipt-id"],
  "expected_effect": "new source file",
  "verification_commands": ["python -m pytest tests/test_history_store.py -q"],
  "blocking_conditions": []
}
```

Schema ограничивает operation allowlist, target внутри workspace, размер полей и
число команд. Raw shell из model checkpoint не становится разрешённой командой.
Worker начинает следующую unit с этой операции и bounded evidence summary, а не с
повторного inventory.

## S07. Результат IMPLEMENT без слепой мутации

При `allow-write=true` IMPLEMENT unit обязана закончиться одним из результатов:

- подтверждённая мутация и следующая verification operation;
- `blocked_missing_information`;
- `blocked_scope_conflict`;
- `blocked_permission_denied`;
- `blocked_unsupported_tool`;
- `blocked_spec_conflict`.

Структурированный blocker содержит точный missing item, уже проверенные evidence,
почему безопасная мутация невозможна и единственное требуемое действие. Создавать
фиктивный файл ради счётчика запрещено. Ask/Plan/read-only никогда не принуждаются
к записи.

## S08. Адаптивная модель и escalation

После двух discovery-only units без executable transition runtime может выбрать
допустимую reasoning-модель. Escalation:

- соблюдает Manual, local-only, tools, context, cost и latency constraints;
- получает цель, requirements, новые evidence и covered/denied operations;
- не получает право повторить side effects;
- должна вернуть executable transition или blocker;
- фиксируется в diagnostics с причиной `discovery_stall`.

Если допустимой модели нет или она не создаёт новый evidence, terminal code:
`discovery_exhausted_without_implementation`. Frontier-модель не является default.

## S09. Диагностика и счётчики

Job status отдельно показывает:

- `completed_units`, `yielded_units`, `failed_units`, `interrupted_units`;
- phase и phase attempts;
- active/wall elapsed и remaining budgets;
- discovery reads/searches/unique lines;
- changed files и checks run;
- last verified progress и next executable operation;
- renewal reason, provider/model и escalation history;
- terminal reason и diagnostic ID.

`units=0/11` без расшифровки запрещено. Parent diagnostic связывает каждую unit и
model attempt. Planned yield не маркируется failed в пользовательском представлении.
Секреты, raw prompt/full file bodies и абсолютные пути хоста не выдаются Web API.

## S10. Web/API

Web-требования детализированы в отдельном промте 0.27. Обязательны:

- submit не удерживает один 15-минутный handler;
- SSE поддерживает replay по sequence/Last-Event-ID после reload;
- reconnect не запускает новую unit и не отменяет job;
- pause/resume/cancel используют expected revision и CSRF;
- UI показывает фазу, полезный прогресс и четыре временных бюджета;
- пользователь не выбирает batch size или размер этапа для обычной задачи;
- terminal blocker объясняет причину и безопасное продолжение.

## S11. Безопасность и совместимость

1. Миграции SQLite additive, идемпотентны и сохраняют job 0.19–0.26.
2. Legacy running job без новых полей проходит reconciliation и получает bounded
   defaults; права не расширяются.
3. Workspace/path/symlink, command allowlist, stale-edit, CSRF/auth и diagnostics
   redaction сохраняются.
4. Resume восстанавливает минимальные прежние права, пересечённые с текущим mode.
5. Текст ТЗ, журналов, retrieved chunks и checkpoints остаётся недоверенными данными.
6. Cost/token/unit ceilings кумулятивны. Handoff/restart их не сбрасывает.
7. Cancel проверяется перед каждым side effect и между provider attempts.
8. Scheduler lock не держится во время LLM/network/tool execution.

## S12. Коды terminal и recovery

Минимальный набор:

- `model_turn_timeout` — истёк один вызов модели, возможен bounded retry/fallback;
- `unit_deadline` — unit остановлена, progress сохранён, возможно requeue;
- `task_active_time_exhausted` — исчерпан суммарный compute budget;
- `task_wall_time_exhausted` — истёк абсолютный TTL;
- `discovery_budget_exhausted` — требуется PLAN/IMPLEMENT/blocker;
- `discovery_exhausted_without_implementation` — escalation не помогла;
- `implementation_blocked_*` — точная структурированная причина;
- `verification_failed` / `repair_budget_exhausted`;
- `ambiguous_project_root` / `verification_unavailable`;
- `repair_target_unresolved`;
- `cancelled_by_operator`.

Любой terminal BLOCKED, включая legacy/recovery paths, обязан атомарно сохранить
валидный structured blocker с category, summary, required action и diagnostic ID.
Пустой blocker заменяется `internal_runtime_error`, а не публикуется как `{}`.

`task_deadline` legacy отображается с миграционной расшифровкой, но новые
persistent jobs не должны использовать его как общий неразделённый таймер.

## 3. Acceptance-матрица

### A01. Разделение timeout

Model turn timeout завершает только attempt. Unit timeout сохраняет/requeue-ит
unit. Ни один из них сам по себе не закрывает persistent job.

### A02. Active time

Пауза, downtime и ожидание пользователя не расходуют active budget. Реальная
работа расходует; restart не обнуляет счётчик.

### A03. Wall TTL

Просроченная забытая задача блокируется точным `task_wall_time_exhausted`.

### A04. Handoff

Новый evidence создаёт новую unit с новым unit deadline, но сохраняет кумулятивные
token/cost/tool/active counters и identity.

### A05. Нет ложного renewal

Heartbeat, duplicate search/read/list и неизменившийся checkpoint не продлевают job.

### A06. Discovery ceiling

После двух discovery-only units runtime переходит в PLAN/IMPLEMENT, reasoning
escalation либо точный blocker; третья полная discovery unit не создаётся.

### A07. Исполнимый handoff

Next operation проходит schema/path/allowlist validation и начинается без повторного
root inventory.

### A08. Write mode

IMPLEMENT с write authority даёт реальную mutation receipt либо structured blocker.
Read-only/Ask/Plan не мутируют.

### A09. Verification

COMPLETE невозможен без требуемых фактических checks. Model prose не считается PASS.

### A10. Repair

Failed check создаёт bounded REPAIR; повторный PASS закрывает задачу, исчерпание
циклов даёт `repair_budget_exhausted`.

### A11. Crash/restart

Процесс завершается между mutation receipt и handoff. После restart side effect
не повторяется, scheduler продолжает со следующей операцией.

### A12. Два worker

CAS/lease fencing не допускает две mutation-capable units одной job.

### A13. SSE disconnect

Закрытие браузера не останавливает job. Reload воспроизводит события без дублей.

### A14. Pause/resume/cancel

Команды revision-safe, переживают restart и проверяются до side effects.

### A15. Provider timeout/rate limit

Fallback/escalation не повторяют подтверждённые tools и не сбрасывают общий бюджет.

### A16. Legacy migration

Копия SQLite 0.26 открывается без потери jobs/receipts/diagnostics. Старый blocked
job остаётся blocked, пока оператор явно не возобновит его.

### A17. Исходный инцидент

На чистой БД повторить задачу service layer при legacy 900-second request limit.
Job должна либо выполнить реальную мутацию+verification, либо дать точный blocker;
общий одноходовый `task_deadline` не должен закрыть durable workflow.

### A18. Большой проект

Задача на 1 000 000+ строк не загружается в prompt. Discovery ceilings, range
coverage и hybrid retrieval соблюдаются, следующий шаг конкретен.

### A19. Diagnostics/UI

UI различает yielded/failed/completed, показывает remaining budgets, phase,
changed files/checks и существующий diagnostic ID после restart.

### A20. Повторная live-приёмка

Не менее двух независимых чистых SQLite, реальный primary и fallback provider,
Web submit → disconnect/reconnect → restart → mutation → check → terminal.
Повтор должен подтвердить exact-once side effects. API-ключи не попадают в отчёт.

## 4. Definition of Done

- выполнены A01–A20 и полный regression suite;
- Ruff check/format, pytest, mypy, compileall, Web build/tests и package build PASS;
- live-инцидент повторён минимум дважды после исправления;
- ТЗ, prompts, README, env, changelog и status описывают фактические возможности;
- версия повышается только после доказательств;
- секреты, SQLite, logs, caches, model files и workspace не публикуются;
- rollback/recovery релиза задокументированы.
