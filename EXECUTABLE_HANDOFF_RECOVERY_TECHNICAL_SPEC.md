# ТЗ 0.29: исполнимый handoff и восстановление реализации

> Уточнение 0.30: `recent_read` не является достаточным provenance mutation
> target. Для `verification-only` mutating handoff отсутствует до конкретного
> failed-check evidence; подробнее в
> `VERIFICATION_ONLY_EXECUTION_TECHNICAL_SPEC.md`.

## 1. Назначение

ТЗ устраняет класс остановок, при которых persistent-задача корректно распознана
как `project-change` и имеет `allow-write`, но scheduler запускает IMPLEMENT без
конкретной исполнимой операции. Нормативный incident:

- job: `103bdbab6c558da6761380dc`;
- diagnostic task: `488aa222a53743a9a04690ef6bc96262`;
- 3 yielded units, 6 file reads, 5 context searches;
- 0 changed files, 0 checks, `verification=not_run`;
- persisted `operation=edit_file`, но `target=""`, `blocking_conditions=[]` и
  `verification_commands=[]`;
- terminal code `implementation_blocked_missing_information`, хотя конкретная
  недостающая информация не сохранена.

Исправление не расширяет file/network/write/destructive/MCP-права и не требует от
пользователя вручную делить крупную задачу.

## 2. E01 — типизированный контракт операции

Runtime хранит `ExecutableOperationContract` со следующими полями:

- `contract_version`, `operation_id`, `operation`, `phase`;
- `component`, `target`, `target_kind`;
- `objective_excerpt`, `objective_sha256`;
- `expected_effect`, `preconditions[]`;
- `input` или bounded patch intent;
- `required_evidence_ids[]`, `blocking_conditions[]`;
- `verification_commands[]`, `depends_on[]`;
- `capability_snapshot`, `created_at`, `revision`.

Для mutating operations обязательны `target`, `expected_effect`, подходящая write
capability и хотя бы один способ доказать postcondition. `target` нормализуется и
проверяется существующей workspace/path policy. Пустой, корневой, внешний или
несовместимый с operation target невалиден. Model JSON до валидации является
untrusted candidate, а не командой.

## 3. E02 — preflight gate

До создания IMPLEMENT work unit runtime детерминированно проверяет:

1. contract schema/version;
2. разрешённость operation;
3. непустой безопасный target;
4. свежесть required evidence/content hash;
5. отсутствие уже подтверждённого identical side effect;
6. готовность dependencies;
7. наличие bounded verification plan;
8. соответствие текущим capabilities и lease generation.

При FAIL worker и mutating tool не запускаются, attempts реализации не
увеличиваются. Preflight result сохраняется как отдельный receipt.

## 4. E03 — goal compiler и dependency graph

Широкая цель компилируется в durable graph: component → leaf operation → evidence
→ verification. Graph ограничен по числу узлов и размеру; полная цель остаётся в
SQLite с SHA-256. Для service/CLI/API/UI/tests создаются отдельные leaf operations,
связанные зависимостями. Runtime выбирает ready leaf, а не передаёт worker общую
фразу «реализуй всё».

Candidate targets выводятся из schema-first snapshot, точных пользовательских
путей, manifest и подтверждённых search/read receipts. Retrieved текст не выдаёт
прав. Если несколько targets равноправны, разрешён один bounded reasoning replan;
если безопасный выбор невозможен и действительно меняет продуктовый результат,
формируется blocker с вариантами.

## 5. E04 — классификация причин остановки

Минимальный набор категорий:

- `missing_user_decision` — нужен существенный выбор пользователя;
- `missing_project_evidence` — не найден обязательный contract/file/schema;
- `capability_denied` — текущая trusted policy запрещает действие;
- `planner_contract_invalid` — candidate operation не прошёл schema/preflight;
- `target_unresolved` — после bounded recovery нет безопасного target;
- `operation_unsupported` — runtime не поддерживает необходимое действие;
- `verification_unavailable` — обязательную проверку нельзя запустить;
- `external_dependency_unavailable` — подтверждённая внешняя зависимость
  недоступна.

Пустой target классифицируется как `planner_contract_invalid`. Код
`implementation_blocked_missing_information` допустим только при непустом
`missing_prerequisites` и категории `missing_user_decision` или
`missing_project_evidence`.

## 6. E05 — bounded recovery

Невалидный handoff проходит строго ограниченную последовательность:

1. deterministic repair/normalization;
2. выбор готового leaf из уже сохранённого graph;
3. при evidence gap — одна targeted-discovery unit;
4. при высокой сложности и разрешённой adaptive policy — одно reasoning replan;
5. повторный preflight;
6. executable handoff либо structured terminal blocker.

Recovery не сбрасывает active/wall/tool/token/cost budgets, не расширяет scope и
не запускает project audit. Одинаковый invalid candidate второй раз считается
duplicate failure и немедленно завершает recovery.

## 7. E06 — доказуемый blocker

Persisted blocker содержит:

- `blocker_version`, `error_code`, `category`, `summary`;
- `missing_prerequisites[]` с `kind`, `name`, `reason`, `source`;
- `attempted_operation` и redacted `candidate_targets[]`;
- `evidence_ids[]`, `capability_snapshot`;
- `required_action`, `retryable`, `resume_phase`;
- `created_at`, `job_id`, `unit_id`, `checkpoint_revision`.

Terminal message генерируется из этой структуры. Фраза о сохранённой точной
причине разрешена только после успешной записи и повторного чтения непустых
обязательных полей. При внутреннем defect UI не просит пользователя предоставить
неизвестную информацию, а показывает «ошибка планирования» и diagnostic ID.

## 8. E07 — mutation и verification

Успешный IMPLEMENT требует mutation receipt с before/after hash, operation ID,
target и lease generation. No-op не считается реализацией, кроме явно ожидаемой
идемпотентной postcondition, подтверждённой отдельным receipt.

Каждый leaf содержит минимальный allowlisted verification plan. После мутации
runtime переводит leaf в VERIFY и запускает ProjectCheckRunner. Результат хранит
command ID, exit category, duration, bounded/redacted stdout/stderr и correlation
ID. Ошибка запуска не сворачивается в безымянный `error`. Repair получает только
относящийся к FAIL evidence.

## 9. E08 — persistence и recovery

SQLite миграция аддитивна и обратимо читается предыдущим кодом там, где это
возможно. Сохраняются operation graph, contracts, preflight receipts, blockers и
verification evidence. Записи защищены transaction/CAS, lease generation и
idempotency key. После crash/restart:

- validated, но не начатый leaf можно claim-ить новой generation;
- начатая unit становится `interrupted`;
- подтверждённая mutation не повторяется;
- target/hash перечитываются перед дальнейшей правкой;
- resume не начинает root inventory.

## 10. E09 — Web API и интерфейс

`GET /api/jobs/{id}` и SSE progress возвращают safe поля:

- phase/status, current component и virtual target;
- contract/preflight state;
- recovery attempt и reason;
- changed files/check counts;
- blocker category, summary, required action и diagnostic ID.

UI различает `изучение`, `подготовка конкретной операции`, `изменение`,
`проверка`, `перепланирование` и `остановлено`. При blocker показывается то, что
нужно сделать, а не общая рекомендация «разделите задачу». Empty target никогда
не отображается как активная операция. Host path, secret, raw prompt/exception и
file body запрещены.

## 11. E10 — журналирование

Structured JSONL и durable diagnostics фиксируют contract validation,
preflight rejection, recovery transition, selected leaf, mutation receipt,
verification и blocker. Parent terminal агрегирует child counts. Retention и
redaction соответствуют действующей политике. Нельзя утверждать наличие exact
prerequisite, если persisted structure пуста.

## 12. E11 — совместимость и настройки

Legacy `next_operation_json` мигрируется в candidate contract. Непустые старые
операции проходят новый preflight; пустые не запускаются и переводятся в recovery.
Ask/Plan/read-only не требуют mutation target. Existing task/job IDs, SSE replay,
provider routing, hybrid memory и path policies сохраняются.

Если вводятся limits, они имеют безопасные defaults и документируются в
`.env.example`; пользователь не обязан их настраивать. Увеличение limits не
является исправлением incident.

## 13. E12 — критерий production

Задача `project-change` завершается только если все обязательные leaf requirements
имеют terminal state и runtime verification PASS. Если часть leaf выполнена,
результат `partial` содержит remaining graph. Если выполнение невозможно,
`blocked` содержит валидный blocker. Текст модели не может установить COMPLETE.

## 14. Приёмка A01–A18

- A01: пустой mutating target отклоняется до создания worker unit.
- A02: preflight rejection не увеличивает implementation attempts.
- A03: широкая service+CLI+API+UI+tests цель создаёт dependency graph.
- A04: worker получает один конкретный target и bounded objective.
- A05: candidate target находится из schema/manifest без полного повторного audit.
- A06: duplicate invalid candidate прекращает loop.
- A07: bounded targeted discovery заполняет evidence gap и повторяет preflight.
- A08: пустой target даёт `planner_contract_invalid`, не ложный missing info.
- A09: настоящий missing user decision сохраняет непустой prerequisite/action.
- A10: terminal текст точно соответствует повторно прочитанному blocker.
- A11: mutation receipt связан с operation ID/target/before/after/generation.
- A12: runtime запускает leaf verification; модель не может подделать PASS.
- A13: ProjectCheckRunner error содержит безопасную диагностическую категорию.
- A14: crash после mutation не повторяет side effect и продолжает VERIFY.
- A15: API/SSE/reload показывают contract, recovery и blocker без утечек.
- A16: regression «3 yielded, 0 changes, empty target» больше не воспроизводится.
- A17: Ruff, format, mypy, pytest, compileall, frontend и build проходят.
- A18: два изолированных live-прогона исходной широкой задачи подтверждают
  normal mutation+verification и forced-invalid recovery/structured blocker.

Production готовность требует A01–A18 и фактической записи результатов в
`IMPLEMENTATION_STATUS.md`. Одно обновление документов не считается реализацией.
