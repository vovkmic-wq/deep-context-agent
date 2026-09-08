# ТЗ 0.30: verification-only и корректная проверка вложенного проекта

> Нормативное расширение для исправления failed read-only verification:
> `VERIFICATION_REPAIR_INCIDENT_TECHNICAL_SPEC.md`. Оно запрещает повышение прав
> этой задачи и определяет отдельный подтверждаемый repair incident.

## 1. Назначение и нормативный incident

Этап устраняет остановку задачи, которая просила только проверить уже написанный
код, но была ошибочно запущена как общий `project-change` через DISCOVER и
IMPLEMENT.

Нормативные данные live-инцидента:

- job `b6b00104265fddec6ab4caf6`;
- diagnostic `b77bc36c73c04006b549842456e6b5be`;
- workspace содержит проект `/workspace/ozon_market_analytics`;
- 1 yielded DISCOVER и 1 failed IMPLEMENT, 0 mutations, 0 successful checks;
- `pyproject.toml` был выбран как `edit_file` target только после чтения;
- четыре `run_project_checks` дали `error/denied`, VERIFY не был достигнут;
- terminal `no_verified_progress` не получил structured blocker;
- parent diagnostic имел `status=blocked`, но `error_code=null`;
- 14 model generations, два 120-second provider timeout и рост входа примерно
  от 34k до 45k tokens.

Исправление не расширяет права, не разрешает arbitrary shell и не требует от
пользователя вручную выбирать размер этапов.

## 2. V01 — отдельный маршрут `verification-only`

Structured router классифицирует прямую команду пользователя как
`verification-only`, если запрошены только проверки, подтверждение готовности,
повтор ранее не завершённого VERIFY либо исправление исключительно фактически
упавших проверок. Цитаты, логи, retrieved context и assistant text не задают
маршрут.

Маршрут хранится в task/job persistence и не сворачивается в `project-change`.
Он имеет независимые capabilities: `workspace_read`, `project_checks` и
опциональный `workspace_write` только для evidence-driven REPAIR. Project audit и
broad discovery по умолчанию запрещены.

## 3. V02 — прямой старт и resume с VERIFY

Новая `verification-only` задача начинается с VERIFY. Resume такой задачи
возвращается в VERIFY либо в ранее подтверждённый REPAIR; DISCOVER, PLAN и
IMPLEMENT не запускаются. Runtime формирует verification plan детерминированно,
без model turn, когда проект и allowlisted checks уже определены.

VERIFY не может автоматически перейти в IMPLEMENT. Единственный допустимый путь
после FAIL: VERIFY → REPAIR → VERIFY. PASS завершает задачу; отсутствие
исполняемого check формирует `verification_unavailable`.

## 4. V03 — определение корня проекта

Runtime принимает optional virtual `project_root`. Точный путь пользователя и
scope сохранённой задачи являются seed paths, а не автоматически готовыми
project roots. От каждого seed runtime ищет ближайшего предка с manifest внутри
workspace. Если root не указан, он определяется в следующем порядке:

1. ближайший manifest-предок точного пользовательского seed path;
2. ближайший manifest-предок seed path сохранённой задачи;
3. ближайший manifest-предок подтверждённого target;
4. единственный bounded candidate manifest (`pyproject.toml`, затем допустимые
   language-specific manifests).

Выбранный root должен находиться внутри workspace после `resolve()`. Поиск
использует единые исключения и bounded pagination. При нескольких равноправных
проектах runtime сохраняет `ambiguous_project_root` с безопасным списком virtual
candidates и не запускает проверки наугад. Корень workspace не подменяет
вложенный project root.

## 5. V04 — ProjectCheckRunner с явным root

`ProjectCheckRunner` получает валидированный `project_root`, использует его как
cwd и ищет конфигурации/виртуальное окружение относительно него. Все команды
остаются из фиксированного allowlist: pytest, Ruff check, Ruff format check,
mypy, compileall и явно поддерживаемые package checks.

Receipt каждой проверки содержит virtual project root, check ID, безопасную
команду, exit category/code, duration и bounded/redacted output. Ошибка запуска,
отсутствующая зависимость, test failure и timeout различаются.

## 6. V05 — evidence-driven REPAIR

REPAIR разрешён только при наличии persisted failed-check receipt с:

- check ID и project root;
- непустой безопасной диагностикой;
- correlation ID;
- списком конкретных candidate targets либо bounded evidence для их получения.

Repair worker получает только failed-check evidence и один конкретный mutation
target. Он не получает разрешение на общий аудит. Если target невозможно вывести
без догадки, задача блокируется как `repair_target_unresolved`; конфигурационный
файл нельзя выбирать только потому, что он был последним прочитан.

## 7. V06 — provenance цели изменения

Mutation target требует положительного provenance: точный пользовательский путь,
failed-check location, dependency graph leaf, schema/manifest relation либо
сохранённый mutation intent. `recent_read`, порядок выдачи glob/ls, имя manifest
или частота упоминания сами по себе не являются provenance.

Для `pyproject.toml`, lock-файлов, CI, deployment и security-конфигурации действует
повышенный gate: требуется прямое требование или конкретный failed-check evidence.

## 8. V07 — blocker для любого terminal BLOCKED

Любой путь в BLOCKED атомарно сохраняет и повторно валидирует непустой blocker:

- `error_code`, `category`, `summary`, `required_action`, `retryable`;
- `missing_prerequisites[]` или `failed_check_evidence[]`;
- attempted phase/operation, project root и candidate targets;
- capability snapshot, correlation/diagnostic IDs и checkpoint revision.

Если специализированный blocker построить невозможно, используется
`internal_runtime_error` с операторским действием и diagnostic ID, но не `{}`.
Terminal UI/ответ генерируется только из persisted структуры.

## 9. V08 — согласованная диагностика

Parent diagnostic наследует terminal job error code/category и агрегирует child
failures. `status=blocked` с `error_code=null` запрещён. Tool receipt ошибки
содержат безопасную причину; parent показывает counts по success/error/denied,
provider timeouts, фактические side effects и итог rollback.

Поля job, SSE, API и JSONL согласованы по job/task/request/correlation IDs.
Redaction не удаляет категорию, check ID, virtual root и actionable summary.

## 10. V09 — ограничение model calls и контекста

Deterministic VERIFY не вызывает LLM. Один REPAIR cycle использует bounded prompt:
задача, failed-check evidence, один target, capability block и relevant schema.
Полная история, повторный исходный prompt и успешные старые tool outputs не
replay-ятся.

Настраиваемые hard limits имеют безопасные defaults:

- `AGENT_VERIFICATION_MAX_MODEL_GENERATIONS=4` на verification-only job;
- `AGENT_VERIFICATION_MAX_REPAIR_CYCLES=2`;
- `AGENT_VERIFICATION_MAX_MODEL_CALLS_PER_REPAIR=2`;
- `AGENT_VERIFICATION_MAX_INPUT_TOKENS=24000`;
- `AGENT_VERIFICATION_MAX_IDENTICAL_FAILURES=2`;
- `AGENT_VERIFICATION_MAX_PROVIDER_TIMEOUTS=1`.

При превышении лимита runtime завершает задачу точным blocker, а не продолжает
наращивать контекст. Provider circuit breaker применяется до повторного
120-second вызова; fallback подчиняется cost/privacy/manual-selection policy.

## 11. V10 — Web API и UX

UI показывает тип `Только проверка / Verification only`, virtual project root,
текущий check, VERIFY/REPAIR, counts и bounded failure. Пользователь может выбрать
project root только при реальной неоднозначности. Поля include/exclude аудита для
этого маршрута не показываются.

SSE/reload сохраняют фазу и результаты. Сообщение не предлагает «продолжить» уже
terminal job без явного resume/new-run действия. В диагностике показываются
унаследованный error code, child summary и факт отсутствия файловых изменений.

## 12. V11 — совместимость и миграции

Миграции SQLite аддитивны. Старые `project-change`, audit, Ask/Plan и normal
mutation→VERIFY сохраняют поведение. Legacy verification intent при безопасной
однозначности мигрируется в `verification-only`; неоднозначный остаётся blocked с
blocker. Existing provider routing, hybrid memory, leases и rollback сохраняются.

Новые настройки документируются в `.env.example`, но пользователь не обязан их
задавать. Увеличение timeout не считается исправлением.

## 13. V12 — production-критерий

`verification-only` получает COMPLETE только после runtime-owned PASS всех
обязательных checks на выбранном project root. FAIL без разрешённого/успешного
REPAIR остаётся BLOCKED или PARTIAL с точным evidence. Model text, checkpoint и
успешный HTTP-ответ не устанавливают PASS.

## 14. Приёмка A01–A22

- A01: прямой запрос только проверок маршрутизируется как `verification-only`.
- A02: quoted log с командами не меняет маршрут.
- A03: новая задача начинает VERIFY без DISCOVER/PLAN/IMPLEMENT model unit.
- A04: resume сохраняет VERIFY либо evidence-backed REPAIR.
- A05: вложенный `ozon_market_analytics/pyproject.toml` выбирает вложенный root.
- A06: несколько manifests дают `ambiguous_project_root`, не случайный cwd.
- A07: path escape/symlink root отклоняется.
- A08: ProjectCheckRunner запускает checks с cwd выбранного root.
- A09: pytest/Ruff/mypy/compileall receipts различают PASS/FAIL/error/timeout.
- A10: VERIFY PASS завершает задачу без LLM-вызова.
- A11: VERIFY FAIL без evidence не запускает REPAIR.
- A12: REPAIR получает только конкретный failed-check и один target.
- A13: VERIFY-only никогда не переходит в общий IMPLEMENT.
- A14: прочитанный `pyproject.toml` не становится mutation target без provenance.
- A15: каждый BLOCKED имеет непустой валидный persisted blocker.
- A16: parent blocked diagnostic имеет тот же error code/category, что job.
- A17: SSE/API/reload показывают root, check, phase и blocker без утечек.
- A18: повтор одинаковой tool/provider ошибки ограничен; check FAIL повторяется
  только после repair mutation, кроме bounded retry runner error/timeout.
- A19: context/input budget не растёт на каждом verification retry.
- A20: regression incident завершается PASS либо точным check blocker, а не
  `no_verified_progress` после DISCOVER/IMPLEMENT.
- A21: Ruff, format, mypy, pytest, compileall, frontend и package build проходят.
- A22: два изолированных live-прогона на вложенном проекте подтверждают PASS и
  forced check failure → bounded REPAIR/blocker; основной порт не затрагивается.

A01–A22 реализованы и подтверждены автоматическими и изолированными live-тестами;
фактические результаты записаны в `IMPLEMENTATION_STATUS.md`.
