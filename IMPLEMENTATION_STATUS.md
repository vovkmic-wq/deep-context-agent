# Статус реализации

## Structured data-aware routing 0.23.0 — 2026-09-03

- Добавлен immutable RoutingDecision с независимыми execution/workflow/scope,
  scan authority, mutation intent, confidence и safe reason codes.
- Direct instruction отделяется от fenced, quoted, tagged и узнаваемого
  terminal/traceback payload до классификации; полный query сохраняется для LLM
  как untrusted data.
- Non-project persistent workflow выполняется durable `execute` unit без
  ProjectAuditStore manifest. Autopilot schema мигрирована полем `workflow`.
- Web backend применяет scope к доступным tools, а write — только при прямом
  mutation intent и trusted allow-write. API/SSE/UI/JSONL отражают решение.
- Нормативный контракт: `DEEP_CONTEXT_AGENT_0_23_STRUCTURED_ROUTING_PROMPT.md`,
  TECHNICAL_SPEC 7.18 и Web-ТЗ 13.17.

Финальные evidence 0.23.0:

- Ruff check/format, mypy и compileall — PASS; pytest — 298 passed,
  1 planned Windows symlink skip; TypeScript production build и bundle test —
  PASS; `pip check` — PASS.
- Исходный PowerShell-log route стабилен в пяти повторах: `single-turn`,
  `log-analysis`, `scope=message`, `allow_project_scan=false`,
  `mutation_requested=false`. Tool-policy regression подтверждает отказ
  project-wide `glob` для message scope.
- Live Web task `65aa43be1f31468a89ea38a1010bf9ce` получил такое же routing decision,
  завершился обычным ответом через failover `zhipu/glm-5.3` →
  `openai/gpt-5.6-sol`; в `autopilot.sqlite3` и `project_audit.sqlite3`
  осталось 0 jobs/runs, safe JSONL не содержит body журнала.
- Отдельный live explicit-persistent task
  `f5b7d95ea50d4ff7b2253d7a1da75ece` создал durable job
  `d4319e8d877fc28560b66906` с `workflow=log-analysis`, `phase=execute`,
  `audit_run_id=NULL` и 0 audit runs. Недоступный в момент проверки
  `openai/gpt-5-nano` после четырёх bounded attempts перевёл job в безопасный
  persisted `blocked`, не расширив scope и не запустив аудит.
- Browser acceptance подтвердил версию 0.23.0, пять chat modes, model controls,
  `position: sticky; top: 0px`, независимый `overflow-y: auto` истории и
  отсутствие console errors.
- Собраны sdist и wheel 0.23.0. Wheel установлен в чистый target, импортировал
  `context_agent.routing`; размер 180633 bytes, SHA-256
  `C184A9B5EC5C1DF498141F531DC055063FDC8C5F195ED3EAFD5B80565471930A`.

Этот файл связывает этапы `IMPLEMENTATION_PROMPT.md` с требованиями
`TECHNICAL_SPEC.md`.

## Dynamic models, hybrid retrieval and bounded scans 0.22.0 — 2026-09-03

- Нормативный промпт создан:
  `DEEP_CONTEXT_AGENT_0_22_HYBRID_RETRIEVAL_MODEL_UI_PROMPT.md`.
- Требования внесены в `IMPLEMENTATION_PROMPT.md`, `TECHNICAL_SPEC.md` и
  `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`.
- Реализованы sticky per-thread provider/model selection и пять presets;
  server-validated bounded catalog, immutable task snapshot и фактический
  provider/model/fallback в terminal evidence.
- Реализованы локальные FastEmbed/ONNX CPU + Qdrant, hybrid RRF с FTS5/BM25,
  SHA-256 incremental sync, lazy load, RAM-aware batch и lexical-only fallback.
- Один artifact policy используется context index, audit, glob и grep;
  широкие операции возвращают bounded pages, scope-bound cursor,
  partial/resume и явные indexed/unchanged/skipped counts.
- Terminal Web event сохраняет duration, provider/model/fallback, file counts,
  partial и cursor presence; Windows shutdown закрывает SQLite, workers и
  rotating JSONL handler.

Финальные evidence 0.22.0:

- Ruff check/format, mypy и compileall — PASS; pytest — 281 passed,
  1 planned Windows symlink skip; TypeScript и production bundle — PASS.
- Editable install и `pip check` — PASS; собраны wheel и sdist 0.22.0.
  Wheel: 173851 bytes, SHA-256
  `c2e3bb33765e1be671e96fabb7631efefe1c230ce03d52574ffc8cead7a77f9a`.
- Реальный FastEmbed/Qdrant CPU: русский semantic paraphrase и точный Python
  symbol найдены; forced invalid model дал рабочий `lexical-only`.
- Web live: `zhipu/glm-5.3` корректно перешёл на
  `openai/gpt-5.6-sol` после rate limit; следующий turn выбрал OpenAI напрямую,
  preference сохранён, JSONL handler освободил файл при shutdown.
- Browser live: sticky header `top=0`, три provider, model catalog и пять
  presets видимы, console errors отсутствуют.
- Ozon read-only scan: 112 уникальных файлов за 3 страницы; grep resume по 25
  results без дубликатов. Synthetic 1 000 000 lines/200 docs: 5 pages,
  6.051 s index, 1.285 ms two searches, 1.27 MiB Python peak, anchors PASS.

## Five chat modes 0.21.0 — 2026-09-02

| Этап 0.21.0 | Статус | Реализация/проверка |
|---|---|---|
| Mode contract | Завершён | Только Agent/Ask/Plan/Debug/Multitask в Pydantic, runtime metadata, HTML и TypeScript; 11 legacy values получают 422 |
| Server policy | Завершён | Ask/Plan forced read-only, Ask/Plan/Debug forced single-turn, trusted mutation middleware не зависит от JavaScript |
| Multitask | Завершён | До четырёх concurrent Web tasks, отдельный child thread/SSE/pending bubble, cancel-all и worktree warning |
| Context UX | Завершён | Aggregate SQLite usage API, accessible circular estimate и built-in Deep Agents summarization explanation |
| Browser/live | Завершён | Production bundle открыт на loopback; все mode defaults и meter проверены без console errors; настоящий GLM Ask-turn PASS |
| Quality/package | Завершён | Ruff/format/mypy/pytest/TypeScript/bundle/compile/package/secret contour выполнен перед release |

Финальные evidence 0.21.0:

- pytest — 268 passed, 1 planned Windows symlink skip; Ruff check/format и
  mypy — PASS; production JS+CSS bundle — 45 699 bytes;
- Browser production DOM содержит mode values `agent`, `ask`, `plan`, `debug`,
  `multitask` и ни одной прежней карточки; Ask/Plan disabled+unchecked write,
  Debug/Agent trusted-write UI, Multitask safe write default подтверждены;
- context meter изменился с 0 до 0.5% на 1 600 символах, tooltip сообщает об
  automatic summary и SQLite archive, browser console errors отсутствуют;
- реальный Web task `3ba4697d8ed34a49b9a7d8b751179751` через
  `zhipu/glm-5.3` принял raw `allow_write=true` + `execution_mode=autopilot`, но
  исполнился как Ask `single-turn`, `allow_write=false`, вернул контрольную
  строку и terminal completed; отдельный `doctor --live` — `OK`.
- wheel `deep_context_agent-0.21.0-py3-none-any.whl` содержит 25 файлов,
  запрещённые SQLite/JSONL/env artifacts отсутствуют, изолированный target
  вернул 0.21.0; SHA-256
  `72AD3F5F39947858196121E517A203D73C16F2016F10581A144118BD8C218755`.

## Durable lease orchestration 0.20.0 — 2026-09-02

| Этап 0.20.0 | Статус | Реализация/проверка |
|---|---|---|
| Schema/migration | Завершён | Job/unit generation, heartbeat, deadline и interrupted с idempotent migration существующей SQLite |
| Fencing | Завершён | Все owner transitions проверяют token+generation; mutation guard закрывается при lease loss |
| Long unit | Завершён | Audit/verify/repair heartbeat действует во время model/check call, batch ≤2, recursion 40, soft deadline 900 s |
| Recovery | Завершён | Expired running unit становится interrupted; job paused, resume создаёт новую generation/thread |
| Web progress | Завершён | Persisted heartbeat/deadline/generation/interrupted доступны job DTO и SSE |
| Tests/live | Завершён | Migration, stale owner, expiry, delayed unit, clean Web job и real provider tests PASS |

Финальные evidence 0.20.0:

- production SQLite миграция сохранила две старые jobs как
  paused/interrupted и две work units как interrupted без удаления истории;
- реальная unit дольше 30-second test lease завершилась без premature takeover;
  clean repeat task `1ad47374e6c54c129218dc557cd11bdd` получил 58 heartbeat
  events, terminal complete и generation 1;
- `zhipu/glm-5.3` с fallback `openai/gpt-5.6-sol` прошёл live doctor.

## Chat-integrated Autopilot 0.19.1 — 2026-09-01

| Этап 0.19.1 | Статус | Реализация/проверка |
|---|---|---|
| Root cause | Завершён | Production query не содержал прежний action verb; `/api/chat` выбрал ordinary turn и получил `agent_step_limit` |
| Backend routing | Завершён | explicit auto/autopilot/single-turn, расширенный classifier, automatic step/context fallback |
| Chat UX | Завершён | Отдельная вкладка удалена; execution selector, inline SSE progress и job history находятся в чате |
| Persistence/security | Завершён | Parent-thread archive, user/human roles, trusted allow-write и существующая SQLite job identity сохранены |
| Tests/package/live | Завершён | Полный Python/TypeScript/package-контур и два chat live-прогона PASS |

Финальные evidence 0.19.1:

- диагностика task `ee02592d8eb643a4854ea3bb72ec302f`: kind `chat`, status
  `failed`, error `agent_step_limit`; исходная body-фраза не прошла classifier;
- targeted backend regressions для точной проблемной фразы, explicit short
  Autopilot, automatic step-limit fallback и single-turn negative — PASS;
- TypeScript check, production build и bundle regression без отдельной вкладки
  Autopilot — PASS, bundle 39 223 bytes;
- Ruff check/format, mypy, compileall и `pip check` — PASS; pytest — 245 passed,
  1 planned Windows symlink skip;
- реальный `/api/chat` с точной проблемной фразой получил task
  `695815966ff24695837c9b0026043455`, kind `chat_autopilot`, события
  execution + 4 job_progress, terminal `complete`; job
  `3ab2fa8c8c0360922c856d88` завершена, parent thread содержит user/assistant;
- diagnostics подтверждает 5 успешных model attempts через `zhipu`; отдельный
  `doctor --live` для `glm-5.3` с fallback `gpt-5.6-sol` вернул `OK`;
- повтор той же chat job после завершения вернул execution/job_progress и
  `complete` за 79 ms без нового полного прохода; production HTML показывает
  chat execution selector и не содержит `data-panel="audits"`/`audit-form`;
- wheel 0.19.1 содержит 25 файлов, запрещённые SQLite/JSONL/env artifacts
  отсутствуют, изолированная target-install вернула 0.19.1; SHA-256
  `CC9F5CF0B3942FA9A71DDC3BFCDE7866A6A792AEEBF83E214EA8964E3E95AD15`.

## Persistent Autopilot 0.19.0 — 2026-08-31

| Этап 0.19.0 | Статус | Реализация/проверка |
|---|---|---|
| Root cause/context | Завершён | Graph recursion отделён от million-line retrieval; применены durable execution/context isolation patterns LangGraph/Deep Agents |
| Durable job state | Завершён | `autopilot.sqlite3`, stable identity, lease, work units, pause/cancel/resume, WAL/migration |
| Adaptive execution | Завершён | One batch/unit, new worker thread, split `2 → 1`, transient retry, bounded blocked states |
| Verification/repair | Завершён | Fixed allowlisted checks, safe output, bounded repair/recheck, current PASS gate |
| CLI/Web | Завершён | `job`, `job-status`, `/api/jobs`, SSE progress/replan/verification, no batch field, complex Web-chat routing |
| Security | Завершён | Trusted allow-write identity, workspace confinement, secret-free subprocess, physical path hidden in Web DTO |
| Tests/package/live | Завершён | Полный Python/TypeScript/package-контур, реальный GLM job, restart/resume и Web API smoke PASS |

Финальные evidence 0.19.0:

- Ruff check/format, mypy, compileall и `pip check` — PASS; pytest — 242 passed,
  1 planned Windows symlink skip; проверено 50 Python-файлов и 16 mypy source
  files;
- deterministic regression воспроизводит `GraphRecursionError`: batch
  автоматически уменьшается `2 → 1`, два новых worker threads завершают
  manifest без ручного продолжения;
- TypeScript check, bundle regression и esbuild — PASS; production bundle
  38 449 bytes;
- `doctor --live` на `zhipu/glm-5.3` с резервным
  `openai/gpt-5.6-sol` — `live_response=OK`;
- первый реальный read-only job обработал три независимых файла и обнаружил
  collision job/audit ID и physical path в отчёте; после исправления повторный
  clean-DB job обработал 2/2 файла, получил разные job/audit IDs, завершился в
  новом процессе со status `complete` и не раскрыл physical workspace;
- Web API smoke на `127.0.0.1:8768`: health/runtime/index/jobs/details/report —
  HTTP 200, версия 0.19.0, форма «Автопилот» присутствует, сохранённый job
  доступен после restart, physical path отсутствует в DTO и отчёте;
- wheel 0.19.0 содержит 25 файлов, запрещённые SQLite/JSONL/env artifacts
  отсутствуют, изолированная target-install вернула 0.19.0; SHA-256
  `10689C5EB6E895B2C280E5277ADBF0790749518F3D368701DF5C691C22E9140B`.

## Durable failure journal 0.18.0 — 2026-08-30

| Этап 0.18.0 | Статус | Реализация/проверка |
|---|---|---|
| Durable SQLite | Завершён | `diagnostics.sqlite3`, WAL, schema v2 migration, request/provider/task records |
| Privacy/retention | Завершён | off/metadata/redacted/full, SHA-256, truncation, deterministic redaction, bounded cleanup |
| Runtime rollback | Завершён | Provider/tool evidence, rollback counts, filesystem-side-effect flag, dual-failure chain |
| Web/CLI | Завершён | list/show/export/purge, safe DTO, correlation IDs, terminal SSE replay после restart |
| Process log | Завершён | Rotating JSONL, safe fields, Windows benign reset policy сохранена |
| Physical deletion | Завершён | Checkpoint `secure_delete=ON` и WAL truncate после rollback исключают deleted-prompt residue |
| Финальный контур | Завершён | Python/TS/package, GLM/OpenAI live, failover, controlled failure, restart/redaction PASS |

Финальные evidence 0.18.0:

- Ruff check/format, mypy и compileall — PASS; pytest — 234 passed, 1 planned
  Windows symlink skip до финальной packaging-проверки;
- TypeScript `tsc --noEmit`, bundle regression и esbuild — PASS, bundle 38 012
  bytes;
- `zhipu/glm-5.3` и `openai/gpt-5.6-sol` doctor live — `OK`; реальный
  failover `lmstudio(unavailable) → zhipu` вернул `LIVE_FALLBACK_84217` и
  записал `fallback_triggered/fallback_success`;
- controlled Web failure сохранил request `25f31e45c6ec4f93aff42c0b4ffe5889`,
  `rollback_success=true`; после restart status/SSE replay вернулся за 85 ms;
- fake credential отсутствует в diagnostics/checkpoints/context/JSONL/export,
  failed conversation history пуста, redacted marker и полный SHA-256 сохранены.
- wheel 0.18.0 содержит 24 файла, runtime SQLite/JSONL отсутствуют, target-install
  вернул 0.18.0; SHA-256
  `7AA2F146B68374C84D71DBAD7DDD91875624DD454985955F897C496AC177F319`.

## Active-context recovery 0.17.0 — 2026-08-30

| Этап 0.17.0 | Статус | Реализация/проверка |
|---|---|---|
| Root cause | Завершён | Thread `web`: 174 messages, 452 525 chars, 75 ToolMessage; terminal detail не сохранялся после SSE |
| Runtime compaction | Завершён | Transient copy, threshold 80k tokens, latest 8 tool results, original checkpoint/archive preserved |
| Web recovery | Завершён | Safe error codes, terminal status/replay, client reconnect reconciliation |
| Windows logging | Завершён | Только exact Proactor `_call_connection_lost` + WinError 10054 считается benign |
| Документация/version | Завершён | Release/global/system prompts, main/Web ТЗ, env/README/changelog, 0.17.0 |
| Финальный контур | Завершён | Проверен совместно с полным контуром release 0.18.0 |

Промежуточные evidence 0.17.0:

- реальный checkpoint: 452 525 → 154 874 model-input chars, очищено 67 старых
  tool bodies, original cleared count = 0;
- targeted Python tests и mypy — PASS; TypeScript/build/bundle — PASS.

## Bounded stale-edit recovery 0.16.0 — 2026-08-29

| Этап 0.16.0 | Статус | Реализация/проверка |
|---|---|---|
| Root cause | Завершён | Exact edit conflict был тупиком после two-read limit; recovery state отсутствовал |
| Runtime state machine | Завершён | One path/version recovery read, forced fresh read, one revised edit, second-conflict stop |
| Context-tool hardening | Завершён | `/workspace`/bare source denied, invalid radius safe, hard eight-call budget |
| Security | Завершён | No raw old_string/content/physical path; exact edit and all prior path/write policies preserved |
| Документация/version | Завершён | Release prompt, global prompt/ТЗ, Web-ТЗ, system prompt, README/changelog, 0.16.0 |
| Финальный контур | Завершён | Python/TS/package, LM Studio+GLM doctor, real GLM stale-edit live и Web smoke PASS |

Финальные evidence 0.16.0:

- `ruff check` и `ruff format --check` — PASS, 42 файла; mypy —
  13 source files без ошибок; pytest — 197 passed, 1 planned Windows
  symlink skip; compileall, pip check и `git diff --check` — PASS;
- TypeScript `tsc --noEmit`, esbuild и bundle test — PASS, JS+CSS
  37 370 bytes;
- deterministic external-mutation sequence и negative state-machine tests —
  PASS; LM Studio `qwen2.5-7b-instruct` и Zhipu `glm-5.3` doctor live — `OK`;
- чистый GLM-5.3 live audit: `read success, read success, edit error,
  read success, edit success, read success`; независимое exact-file
  assertion — `RECOVERED_LINE_51602`, PASS;
- Web smoke `127.0.0.1:8766`: `/api/health=ok`, runtime version `0.16.0`,
  provider `zhipu`; TypeScript bundle — PASS;
- wheel 0.16.0 содержит 22 файла, static bundle и system prompt,
  target-install вернул 0.16.0; SHA-256
  `D06294936A5CB11E9CBAD7726AE47A212683387CF27A86CD39A80AFCD5A3D503`.

## Provider/files hardening 0.15.0 — 2026-08-29

| Этап 0.15.0 | Статус | Реализация/проверка |
|---|---|---|
| LM Studio diagnostics | Завершён | Bounded `/models`, safe errors, `local-model` auto-selection, catalog update |
| Local/remote payment UX | Завершён | Loopback без confirm и с явным free label; remote с opt-in warning |
| Custom providers | Завершён | `custom-*` UI/API, immediate activation, HTTPS/loopback validation, server-only key |
| Files navigation | Завершён | Bounded history Back, parent Up, loading/success/error и object count |
| Error UX | Завершён | Expected AgentError в SSE даёт safe provider guidance без raw exception |
| Документация/version | Завершён | Release prompt, global prompt/ТЗ, Web-ТЗ, system prompt, README/env/changelog, 0.15.0 |
| Финальный контур | Завершён | Python/TS/package, real LM Studio/custom live, desktop/mobile browser acceptance PASS |

Финальные evidence 0.15.0:

- `ruff check` и `ruff format --check` — PASS, 41 файл; mypy —
  13 source files без ошибок; pytest — 193 passed, 1 planned Windows
  symlink skip; compileall и pip check — PASS;
- TypeScript `tsc --noEmit`, esbuild и bundle test — PASS, JS+CSS 37 370 bytes;
- реальный LM Studio `/models` вернул 7 models; Web doctor выбрал
  `qwen2.5-7b-instruct` вместо placeholder и получил `OK`;
- custom local provider создан, помещён первым в active chain и
  получил `OK` без сетевой платы;
- browser E2E: LM Studio no payment dialog, custom provider form, file Open/
  Back/Up/editor, mobile 390x844 и zero console warnings/errors — PASS;
- wheel 0.15.0 содержит 22 файла и static bundle, SHA-256
  `55608F53674F7EDC1D213C975911F42E04A66779771240759F5974D73076A2E2`.

## Единый Web API и Codex-like UX 0.14.0 — 2026-08-29

| Этап 0.14.0 | Статус | Реализация/проверка |
|---|---|---|
| Shared API boundary | Завершён | Web использует общие runtime, context/audit SQLite, provider failover и path policy |
| Chat UX | Завершён | Tasks/history, mode selector, bottom composer, stop, optional Enter send, Shift+Enter newline |
| Context/files | Завершён | Исправлен `/workspace`, index lifecycle/counters, directory navigation, bounded editor + SHA |
| Audit/settings labels | Завершён | Bilingual fields и русские help/comments вместо raw JSON |
| Live providers | Завершён | Thread-safe atomic priority, add/remove configured providers, per-provider live check |
| Responsive/accessibility | Завершён | Desktop sidebar, mobile bottom nav, keyboard/focus/aria-live, 390 px browser acceptance |
| Документация/version | Завершён | Web prompt, Web-ТЗ, global prompt/ТЗ, README, changelog, 0.14.0 |
| Финальный контур | Завершён | Ruff/format, mypy, 184 passed + 1 planned skip, compileall, TS build, wheel, pip check и browser live acceptance PASS |

Финальные evidence 0.14.0:

- `ruff check` и `ruff format --check` — PASS, 40 файлов formatted; mypy —
  24 source files без ошибок; pytest — 184 passed, 1 planned Windows symlink skip;
- `compileall` и `pip check` — PASS;
- TypeScript `tsc --noEmit`, esbuild и bundle test — PASS, JS+CSS 35 174 bytes;
- реальный browser E2E: GLM-5.3 chat ответил `ГОТОВ`, Enter/Shift+Enter,
  context index, file preview, provider reorder/restore и mobile 390x844 — PASS;
- console errors/warnings после основных flows — отсутствуют;
- wheel 0.14.0 содержит 22 файла и static bundle, SHA-256
  `F1AF91D1FB0B9CF97964F52D0951CB8278393C1919B5BB1B224E9B71B5F4A4E2`.

## Production audit and Web UI 0.13.0 — 2026-08-27

| Этап 0.13.0 | Статус | Реализация/проверка |
|---|---|---|
| Baseline | Завершён | Ruff lint/format, mypy, 168 passed + 1 planned symlink skip до изменений |
| Explicit audit mode | Завершён | Read-only default; только `--allow-write`; mode в identity/SQLite/status/report; regression против inference по `fix` |
| File selection | Завершён | Built-in generated/dependency/report filters, env include/exclude, selected/excluded/reasons |
| Requirements/findings | Завершён | Устойчивые REQ IDs, bounded batch subset, candidate evidence matrix, validated/deduplicated findings |
| Output/resume | Завершён | Compact console, direct UTF-8 text/JSON, flush progress, model-free status, pause/cancel/resume |
| Web backend/UI | Завершён | FastAPI/Uvicorn extra, local static UI, REST/SSE, shared runtime/SQLite, files/providers/settings |
| Web security | Завершён | Same-origin, CSRF, CSP, remote bearer gate, path/secret guards, optimistic hash, delete off |
| Large corpus | Завершён | Автотест реального файла 1 000 000 строк и 500 отдельных документов |
| Документация/version | Завершён | Global prompt, implementation prompt, ТЗ, Web-ТЗ, README, env, changelog, 0.13.0 |
| Финальный контур | Завершён | Ruff check/format, mypy, compileall, 181 passed + 1 planned symlink skip; wheel/static, CLI/doctor, HTTP Web smoke и GLM-5.3 live audit OK |

Финальные evidence 0.13.0:

- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 40
  файлов уже отформатированы; `mypy src/context_agent` — PASS для 13 modules;
- `pytest -ra` — 181 passed, 1 planned Windows symlink skip;
- `compileall`, `pip check` — PASS; wheel 0.13.0 содержит 22 файла и локальный
  static bundle, SHA-256
  `D0573D286BE9C458B6FF1ACF3CCAC0F6E4C166DA096A507A56F09DC808594B6E`;
- TypeScript `tsc --noEmit`, reproducible esbuild и bundle test — PASS,
  production JS+CSS 11 690 bytes;
- реальный Uvicorn HTTP smoke на чистых workspace/data — health, version,
  static page и CSP PASS;
- `doctor --live` выбрал primary `zhipu/glm-5.3` и получил `OK`;
- clean-DB live audit `aba479946ef46a305c72fa7c` обработал 2/2 файла,
  `file_reads=2`, `partial=0`, `mode=read-only`, сохранил UTF-8 text/JSON и не
  изменил fixture.

## Large-project audit orchestration 0.12.0 — 2026-08-25

- Wide project requests route to a persistent SQLite manifest and independent
  batches instead of one ever-growing graph turn.
- Manifest completion is evidence-backed; unread files remain pending and a
  changed SHA-256 reopens only the changed file.
- Unique reviewed-file counts are separate from actual paginated `file_reads`;
  the final report cannot hide a second page call as one read.
- Per-file page exhaustion is represented by `partial` and
  `complete_with_partial`; uncovered lines are never reported as reviewed.
- Cached summaries and a Python AST index provide a compact project map without
  importing target code or placing the corpus in an LLM prompt.
- `run_project_checks` exposes only a fixed no-shell allowlist with timeout,
  bounded/redacted output and secret-free child environment.
- Operational limits are configurable and validated; `recursion_limit` is no
  longer hardcoded in the graph call.

| Этап 0.12.0 | Статус | Проверка | Результат |
| --- | --- | --- | --- |
| Prompt/ТЗ/документация | Завершён | System prompt, `IMPLEMENTATION_PROMPT.md`, `TECHNICAL_SPEC.md`, README, env, changelog | Two-level large-context model and batch rules synchronized |
| Manifest/SHA/resume | Завершён | Unit: partial batch, restart, changed file | Only verified/current files become reviewed |
| Summary/AST | Завершён | Unit: cache reuse and qualified nested symbol | No import/execution of target code |
| Safe checks | Завершён | Unit: injection denial, `shell=False`, env redaction, repeat after mutation | Fixed allowlist only; bounded cycle |
| Scale | Завершён | Existing 1,000,001-line FTS5 test plus 300-file manifest | Beginning/end searchable; corpus never placed in one prompt |
| Финальный контур | Завершён | Ruff check/format, mypy `src tests`, compileall, wheel, CLI/doctor/live | 168 passed, 1 Windows symlink skip; wheel 0.12.0; GLM-5.3 doctor/agent/batch live OK |

Live evidence на чистых временных workspace/data/thread:

- `doctor --live`: `zhipu/glm-5.3`, `live_response=OK`;
- полный `AgentRuntime.ask`: точный ответ `DEEP_CONTEXT_012_OK`;
- batch audit: 2/2 файла, `partial=0`, `file_reads=4`, README 368/368
  строк, UTF-8 stdout без `charmap` exception;
- первый live-прогон обнаружил Windows stdout codec и page-budget дефекты;
  исправления выполнены и подтверждены повторными clean-DB live-прогонами.

## Default GLM/OpenAI chain 0.11.0 — 2026-08-24

- Основная модель по умолчанию: `zhipu/glm-5.3`.
- Резервная модель по умолчанию: `openai/gpt-5.6-sol`.
- Порядок failover без CLI/env override: `glm,openai`.
- Default Z.AI endpoint: `https://api.z.ai/api/paas/v4`.
- GPT-5.6 Sol Chat Completions tool calls используют `reasoning_effort=none`.
- Локальная `.env.local` синхронизирована без раскрытия ключей.

| Этап | Статус | Проверка | Примечание |
| --- | --- | --- | --- |
| 1. ТЗ и промпты | Завершён | `TECHNICAL_SPEC.md`, `IMPLEMENTATION_PROMPT.md`, `EVIDENCE_INTEGRITY_PROMPT.md` | Глобальный prompt и критерии приёмки синхронизированы с дефектами ручных и live-логов |
| 2. Каркас и провайдеры | Завершён | Unit-тесты конфигурации и фабрики | LM Studio, OpenAI, YandexGPT, DeepSeek и Qwen; provider retry выключен, retry выполняет middleware model call |
| 3. Большой контекст | Завершён | 1 000 001 строка, 200 документов, повторное открытие SQLite | Потоковый FTS5, BM25, соседние чанки и cross-thread archive без загрузки корпуса в окно LLM |
| 4. Deep Agent и CLI | Завершён | Fake-model tool loops, CLI unit/smoke, OpenAI live | `/paste`, `ask --file`, stdin, bounded input, `--no-auto-context` и доверенная runtime identity |
| 5. Безопасность файлов | Завершён | Root-delete, внешний путь, exact read, незавершённая команда | Нет shell/общего `delete`; запрещён root, placeholder и угадывание отсутствующего содержимого |
| 6. Надёжность | Завершён | Retry, точный rollback checkpoints/writes, web timeout | Model-call retry не дублирует tool; failed turn не попадает в следующий запрос; web error структурирован |
| 7. Доказательства | Завершён | Audit runtime/context/files/web и guards | Содержимое/секреты не копируются; текущий веб-факт требует успешного fetch; self-PASS не является приёмкой |
| 8. Финальная проверка 0.2.0 | История | Ruff, 61 passed, 1 planned skip, CLI help, `doctor --live` | Результат предыдущего production-hardening сохранён для трассировки |
| 9. Acceptance reliability 0.3.0 | Завершён | Последовательность, duplicate guard, redaction, PyPI, recursive cleanup | Каждый дефект последнего ручного лога закреплён кодом и регрессионным тестом |
| 10. Финальная проверка 0.3.0 | Завершён | Ruff, 72 passed, 1 planned skip, CLI help, `doctor --live` | Изолированный OpenAI `gpt-5-nano` smoke: один `runtime_info`, точные provider/model, ключ не выведен |
| 11. Acceptance completion 0.4.0 | Завершён | Repeat policy, forbidden read, manifest parser/evaluator | Exact counts, ordered/forbidden events и negative statuses проверяются независимо от LLM |
| 12. Финальная проверка 0.4.0 | Завершён | Ruff, 81 passed, 1 planned skip, package/CLI/doctor/live | OpenAI `gpt-5-nano`: один `runtime_info`, runtime manifest — 2 PASS, 0 FAIL |
| 13. Acceptance correctness 0.5.0 | Завершён | Sentence scope, allowed-unlisted, BLOCKED, completion gate | Runtime завершает только dependency-ready cleanup/postconditions и не расширяет filesystem scope |
| 14. Финальная проверка 0.5.0 | Завершён | Ruff, 91 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: полный manifest — 32 PASS; новый процесс/thread — 2 PASS и 4 context results |
| 15. Evidence integrity 0.6.0 | Завершён | Structured audit, manifest v2, cardinality/exact-once guards | Hash/count predicates независимы от LLM; устаревший exact-once повтор не исполняется |
| 16. Финальная проверка 0.6.0 | Завершён | Ruff, 97 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: полный v2 manifest — 32 PASS; restart — ровно один call, 4 results, 2 PASS |
| 17. Ozon hardening 0.7.0 | Завершён | Root-scope, index-filter и listing regression tests | Общий root не блокирует child-read; generated/browser/cache пути отсечены; listing ограничен 20/50 |
| 18. Финальная проверка 0.7.0 | Завершён | Ruff, 100 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: root-scope read успешен; полный manifest — 32 PASS; restart — 4 results и 2 PASS |
| 19. BOM text patch 0.7.1 | Завершён | Ruff, 102 passed, package/pip/doctor live, UTF-16/UTF-32 regression | Обнаруженный Ozon UTF-16LE отчёт теперь индексируется; clean-DB повтор выполняется после публикации |
| 20. Explicit tool budgets 0.8.0 | Завершён | Ruff, 105 passed, package/pip/doctor live, parser/tool-loop | Per-tool и total budget скрывают exhausted tools; stale provider-call не исполняется и не аудируется |
| 21. Empty toolset patch 0.8.1 | Завершён | OpenAI 400 regression, Ruff, 106 passed, package/pip/doctor live | Пустой toolset не отправляет tool-only model settings; повтор Ozon выполняется после публикации |
| 22. Middleware order patch 0.8.2 | Завершён | Ruff, 107 passed, package/pip/doctor live, композиционный regression | Sequential normalizer видит окончательный toolset; чистый Ozon-run выполняется после публикации |
| 23. Multiline budget patch 0.8.3 | Завершён | Ozon prompt regression, Ruff, 107 passed, package/pip/doctor live | Общий limit 15 доказан live; per-tool 2 распознаётся через перенос строки |

Финальная проверка выполнялась командами из README. Конфликт ACL между обычным
Windows-пользователем и Codex sandbox устранён отказом от общих pytest/Ruff-
кэшей. Платные провайдеры без настроенных ключей не вызывались; их конфигурация
и маршрутизация покрыты автоматическими тестами без отправки секретов в вывод.

Production-hardening от 2026-08-22 проверен в изолированных
`AGENT_WORKSPACE`/`AGENT_DATA_DIR`:

- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 22 файла отформатированы;
- `pytest -ra` — 61 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- OpenAI doctor — `model=gpt-5-nano`, `live_response=OK`;
- один многострочный stdin-запрос дошёл до live runtime одной транзакцией;
  `runtime_info` вернул `provider=openai`, `model=gpt-5-nano`, а audit подтвердил
  один успешный tool call без вывода ключа;
- незавершённая команда была отклонена до LLM/tool, файл не создан;
- fake model упал один раз и повторился на model-call уровне; файловый tool
  выполнился ровно один раз;
- после окончательной model error checkpoints/writes совпали с точным снимком
  до хода, а следующий запрос не содержал failed marker;
- если tool успел изменить файл до более поздней model error, исключение содержит
  безопасный audit фактического изменения и предупреждает, что side effect не
  был отменён;
- timeout web-search дал структурированный `error`, и runtime пометил старое
  утверждение об актуальной версии как `FAIL` без успешного page fetch;
- запрос удаления `/workspace/` не выполнил mutating tool, контрольный файл
  `ROOT_DELETE_MUST_KEEP_58317` остался на месте;
- точное чтение вернуло только `EXACT_REQUESTED_CONTENT_27419`;
- запрос `C:\outside-live-48271.txt` был отклонён до LLM, substitute отсутствует;
- фраза `СЕВЕРНЫЙ КВАРЦ 73184` найдена после нового запуска в другом thread ID,
  ответ правильно описал постоянный SQLite-архив.

Результат 0.2.0 выше сохранён как история версии. Проверка 0.3.0 от 2026-08-22
выполнена после устранения недостатков последнего acceptance-лога:

- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 25 файлов отформатированы;
- `pytest -ra` — 72 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- CLI `--help` и безопасный OpenAI `doctor` — успешно, ключ показан только как
  `configured`;
- реальный официальный PyPI JSON probe вернул `langchain=1.3.16` с
  `https://pypi.org/pypi/langchain/json`;
- изолированный live-run использовал
  `v030-844fe480ca63498ba3dbfafd17d1c40d`, новый workspace/data/thread,
  `gpt-5-nano`; `doctor --live` вернул `OK`, а agent audit — ровно один
  `runtime_info: success`;
- fake-model integration доказал, что из двух параллельных calls исполняется
  только первый, а на каждый bind передаётся `parallel_tool_calls=false`;
- повторная идентичная мутация в одном ходе получает `denied`, но ledger
  сбрасывается перед следующим пользовательским ходом;
- `DECOY_SECRET_DO_NOT_SHOW_71935` остаётся точным содержимым тестового файла,
  но заменяется на `[REDACTED]` в ответе и audit;
- private URL `http://127.0.0.1:8000/private` даёт фактический текущий
  `fetch_web_page: error` без сетевого доступа к локальному сервису;
- явно названный непустой подкаталог удаляется одним
  `remove_path [recursive=true]`, а root-delete по-прежнему сохраняет sentinel;
- `CANDIDATE_RESULT` и общий PASS/FAIL получают программный внешний вердикт
  `FAIL` либо `NOT_VERIFIED` и не используются как источник готовности.

Версия 0.3.0 считается production-ready для заявленного локального
однопользовательского CLI. Многопользовательский сетевой сервис требует
отдельной процессной песочницы, аутентификации и координации одновременных
записей; это не входит в область данного ТЗ.

Результат 0.3.0 выше сохранён как история. Проверка 0.4.0 от 2026-08-23
выполнена после анализа полного ручного audit из 26 tool calls:

- повторные идентичные runtime/context/listing/web-вызовы блокируются до
  повторного исполнения и получают `denied` в текущем audit;
- третье чтение неизменённого пути блокируется; успешная мутация самого пути
  или recursive removal родителя открывает новую проверяемую версию;
- basename из явного «не читай/не открывай/не показывай» сопоставляется только с
  явно указанным полным workspace-путём и запрещает `read_file` для decoy;
- строгий bounded manifest parser отклоняет неизвестные поля/tools/status,
  неверные зависимости и превышение размера;
- manifest evaluator сверяет все attempts, точные количества, ordered required
  events, forbidden calls и ожидаемые `denied/error/not_found`; один audit event
  не используется дважды, а необъявленный tool является FAIL;
- канонический `acceptance-prompt.txt` содержит 21 ordered event, один forbidden
  decoy read, точные количества для 11 tool types и одну restart-проверку;
- contract-тест канонического manifest подтверждает предусмотренную полную
  последовательность cleanup и post-delete reads без выполнения платного API;
- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 27 файлов отформатированы;
- `pytest -ra` — 81 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable wheel `deep-context-agent==0.4.0` собран и установлен, `pip check` не
  обнаружил конфликтов, CLI `--help` и безопасный OpenAI doctor прошли;
- изолированный live-smoke использовал
  `v040-ff3825cf02f8410bb7c7530eeb087bf2`, новый workspace/data/thread и
  `gpt-5-nano`; audit зафиксировал ровно `runtime_info: success`, а runtime
  manifest — `2 PASS, 0 FAIL, 0 PENDING` без вывода API-ключа.

Версия 0.4.0 считается production-ready для заявленного локального
однопользовательского CLI. Полный канонический live acceptance остаётся
отдельным операторским прогоном из-за числа платных model calls; его лог теперь
оценивается runtime автоматически. Многопользовательский сетевой сервис по-
прежнему требует отдельной процессной изоляции, аутентификации и координации
записей и не входит в область данного ТЗ.

Результат 0.4.0 выше сохранён как история. Проверка 0.5.0 от 2026-08-23
выполнена после итеративных диагностических live-прогонов и финального чистого
прогона:

- отрицательная инструкция ограничена своим предложением: `decoy.txt` остаётся
  запрещённым, а положительно указанный `result.txt` читается;
- `write_todos` разрешается только как явно объявленный unlisted planning-tool,
  а функциональные tools сохраняют exact counts;
- первичный missing event получает FAIL, зависимые события — BLOCKED;
- bounded completion middleware продолжает только доказанную ordered
  cleanup/postcondition цепочку, не начинает root event, не читает запрещённый
  путь, не выходит из `/workspace/` и не превышает exact count;
- после root event provider получает имя следующего prose-разрешённого tool, а
  dependency-ready `read_file`/`remove_path` — точный ordered target; JSON
  manifest без независимого prose-разрешения не инициирует действие;
- машинный manifest исключён из классификации текущих web-фактов, поэтому его
  поле `version` не создаёт ложный web FAIL для локального context search;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 91 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.5.0`, `pip check`, CLI `--help` и
  безопасный OpenAI doctor — успешно;
- полный изолированный live acceptance использовал run
  `20260823-125545`, `gpt-5-nano`, `AGENT_MODEL_TEMPERATURE=0` и новые
  workspace/data/thread; runtime manifest дал `32 PASS, 0 FAIL, 0 BLOCKED,
  1 PENDING`, а exact counts совпали для всех десяти функциональных tool types;
- физическая проверка после live-run подтвердила отсутствие тестового каталога,
  root sentinel и outside placeholder при сохранённом prompt-файле;
- новый процесс с тем же `AGENT_DATA_DIR` и thread
  `restart-v050-release-20260823-125545` вызвал `search_context` ровно один раз,
  получил `4 result(s)` и runtime verdict `2 PASS, 0 FAIL, 0 BLOCKED`; restart
  PENDING тем самым закрыт внешней проверкой.

Версия 0.5.0 считается production-ready в границах локального
однопользовательского CLI. Многопользовательский сетевой сервис требует
отдельной процессной изоляции, аутентификации и координации одновременных
записей и не входит в область данного ТЗ.

Результат 0.5.0 выше сохранён как история. Проверка 0.6.0 от 2026-08-23
выполнена после устранения трёх дефектов restart-аудита:

- `ToolAuditEntry` содержит безопасные `result_count` и `content_sha256`;
  SHA-256 вычисляется из фактических байтов workspace-файла и не раскрывает
  его тело;
- manifest v2 строго валидирует `min_results` и `content_sha256`, сохраняет
  совместимость v1 и запрещает evidence-предикаты в forbidden events;
- прямой вопрос о количестве получает детерминированный ответ из ToolMessage;
  unit tool-loop заменил ошибочный текст модели `0` фактическим числом `1`;
- exact-once middleware удаляет уже вызванный tool из следующего model request;
  regression с повторным provider call дал одну audit-запись без `denied`;
- canonical `acceptance-prompt.txt` v2 проверяет SHA-256 начального/изменённого
  result и root sentinel, а `restart-acceptance-prompt.txt` требует
  `search_context: 1` и `min_results=1`;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 97 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.6.0`, `pip check`, CLI `--help`,
  безопасный OpenAI doctor и `doctor --live` — успешно;
- полный изолированный live acceptance использовал run `20260823-221609`,
  `gpt-5-nano`, новые workspace/data/thread и manifest v2; runtime дал
  `32 PASS, 0 FAIL, 0 BLOCKED, 1 PENDING`, все exact counts и hash-предикаты
  совпали;
- PowerShell-проверка после live-run подтвердила отсутствие тестового каталога,
  root sentinel и outside placeholder;
- restart с тем же `AGENT_DATA_DIR` и новым thread вызвал `search_context`
  ровно один раз, вернул `4 result(s)` и manifest verdict
  `2 PASS, 0 FAIL, 0 BLOCKED, 0 PENDING`; повторный denied отсутствует.

Версия 0.6.0 считается production-ready в границах локального
однопользовательского CLI. Многопользовательский сетевой сервис по-прежнему
требует отдельной процессной изоляции, аутентификации, централизованного аудита
и координации одновременных записей и не входит в область данного ТЗ.

Результат 0.6.0 выше сохранён как история. Проверка 0.7.0 от 2026-08-23
выполнена после устранения дефектов первого Ozon-эксперимента:

- общий `/workspace/` исключён из exact-file allowlist, при этом точный
  дочерний путь по-прежнему ограничивает чтение;
- индексатор до обхода отсекает generated/cache/coverage, Playwright и browser-
  profile пути с регистронезависимым сопоставлением;
- `list_context_sources` ограничен 20 источниками по умолчанию и 50 максимум;
- Ozon-промпт сокращён до одного доказанного дефекта, узких retrieval-запросов
  и не более 15 функциональных tool calls;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 100 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.7.0`, `pip check`, CLI `--help`,
  безопасный OpenAI doctor и `doctor --live` — успешно;
- отдельный live root-scope ход нашёл и прочитал `/workspace/pyproject.toml`
  после общего упоминания `/workspace/`, вернул `0.7.0` без denied events;
- полный изолированный live acceptance использовал run
  `20260823-231533`, `gpt-5-nano` и новые workspace/data/thread; runtime дал
  `32 PASS, 0 FAIL, 0 BLOCKED, 1 PENDING`, а cleanup завершился;
- новый процесс/thread на той же SQLite-БД вызвал `search_context` ровно один
  раз, получил `4 result(s)` и закрыл restart-аудит с
  `2 PASS, 0 FAIL, 0 BLOCKED, 0 PENDING`.

Версия 0.7.0 считается production-ready в границах локального
однопользовательского CLI. Проверка Ozon выполняется отдельно на временной
копии и чистой БД; её изменения не переносятся в исходный проект автоматически.

Patch 0.7.1 добавлен после первого clean-DB индексирования Ozon: файл
`analysis_30_days.txt` имеет корректный UTF-16LE BOM, но 0.7.0 принимал его
NUL-байты за binary. После переноса BOM-detection перед binary guard выполнены
Ruff и format-check, `pytest -ra` дал 102 passed и 1 planned symlink skip,
editable package сообщает 0.7.1, `pip check` и OpenAI `doctor --live` успешны.

Hardening 0.8.0 добавлен после первого LLM-хода на чистой Ozon-БД. Модель
выполнила четыре `search_context` при prose-максимуме два и превысила общий
максимум 15 попыток. Новая model middleware парсит русские/английские числовые
ограничения, считает фактический current-turn audit, удаляет exhausted tools из
следующего model request и подавляет stale provider-call. Regression охватывает
парсер, per-tool и total budget; полный прогон дал Ruff PASS, 105 passed,
1 planned symlink skip, editable 0.8.0, чистый `pip check` и успешный OpenAI
`doctor --live`.

Первый Ozon live-run 0.8.0 остановился после ровно 15 audit events, доказав
работу total budget, но OpenAI вернул 400: `parallel_tool_calls` запрещён без
`tools`. Patch 0.8.1 удаляет этот model setting и сбрасывает `tool_choice` при
пустом toolset; regression проверяет итоговый request непосредственно.

Повтор 0.8.1 показал, что sequential normalizer был внешним middleware и видел
старый непустой toolset до budget narrowing. В 0.8.2 порядок изменён так, чтобы
sequential normalizer выполнялся после toolset policies; композиционный тест
проверяет именно этот полный путь до model handler.

Ozon live-run 0.8.2 успешно завершился без 400 и остановился ровно на 15 audit
events, однако сделал три `search_context`: строка prompt была перенесена между
«не более двух узких» и именем tool. Patch 0.8.3 допускает такой перенос только
внутри того же предложения и закрепляет точную формулировку regression-тестом.

Финальный clean-DB Ozon-run 0.8.3 описан в `OZON_EXPERIMENT_REPORT.md`:
индексация 230 файлов/582 чанков с exit 0, live exit 0, ровно 15 audit events и
2 `search_context`, temp worktree чист. Внешние проверки Ozon: Ruff lint PASS,
57 pytest PASS; один `ruff format --check` drift в exporter существовал уже в
baseline. Исходный Ozon-проект не изменялся.

## GLM-5.2 / Zhipu integration 0.9.0 — 2026-08-24

- Добавлены канонический provider `zhipu` и CLI-алиас `glm`; оба разрешаются в
  runtime identity как `zhipu` с моделью `glm-5.2`.
- Поддержаны официальные `ZAI_API_KEY`, `ZAI_MODEL`, `ZAI_BASE_URL` и
  совместимые `ZHIPU_*` aliases. Стандартный endpoint и Coding Plan endpoint
  выбираются конфигурацией, секрет не хранится в репозитории.
- Thinking включён через `extra_body`, а недопустимая для Zhipu температура
  отклоняется до сетевого запроса.
- Недокументированный Zhipu-параметр `parallel_tool_calls` не отправляется;
  defensive response normalization по-прежнему оставляет один tool call.
- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 31
  файл отформатирован; `pytest -ra` — 122 passed, 1 planned Windows symlink
  skip.
- CLI `doctor` для `--provider glm` и `--provider zhipu` — PASS с тестовым
  несекретным ключом; оба показали модель `glm-5.2` и официальный base URL.
- Изолированно собран `deep_context_agent-0.9.0-py3-none-any.whl`, SHA-256
  `52C2986B90E7400DB8F9D6E6641241FC81344280B200AD6F6872E437CEFAFDB8`.
- Реальный `doctor --live` не выполнялся: `ZAI_API_KEY`/`ZHIPU_API_KEY` не
  настроен ни в процессе, ни в `.env.local`. Это явное внешнее условие, а не
  дефект кода; live-проверка должна быть выполнена после локальной установки
  ключа без его вывода.

## Provider priority and failover 0.10.0 — 2026-08-24

- Добавлены взаимоисключающие `--provider` и `--providers`, а также
  `AGENT_PROVIDER_PRIORITY`; порядок списка задаёт порядок model calls.
- Конфигурация отклоняет пустые элементы, неизвестные имена и канонические
  дубликаты aliases. Пустая env-строка означает отключённую цепочку и сохраняет
  одиночный `AGENT_PROVIDER`.
- `ProviderFailoverMiddleware` даёт каждому провайдеру настроенные model-call
  retries, затем безопасно переключается на следующий. Успешный fallback
  закрепляется на текущий ход и сбрасывается к primary перед следующим ходом.
- Failover расположен только в model-call middleware: уже выполненные tools не
  переигрываются. Runtime identity и `runtime_info` отражают активный provider,
  полный приоритет, число failover и неуспешные имена без API-ответов/ключей.
- Одиночный provider сохраняет прежнее исключение для обратной совместимости;
  полная ошибка много-провайдерной цепочки содержит только provider/model и
  безопасный тип исключения.
- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 31
  файл отформатирован; `pytest -ra` — 135 passed, 1 planned Windows symlink
  skip.
- CLI doctor с тестовыми ключами корректно показал приоритет
  `openai,zhipu,deepseek`. Реальный `doctor --live` на существующем локальном
  OpenAI key и цепочке `openai,lmstudio` вернул `live_provider=openai` и `OK`.
- Изолированный runtime live-test с приоритетом `lmstudio,openai`, новой БД и
  timeout 15 секунд подтвердил настоящий failover:
  `answer=FAILOVER_RUNTIME_OK`, `active_provider=openai`, `failover_count=1`,
  `failed_providers=lmstudio`.
- Собран `deep_context_agent-0.10.0-py3-none-any.whl`, SHA-256
  `35A0766EF1CFE06D3599649CD1C9F646C077749EFDF8BFEE85731D2D627AA5E8`.
