# Приёмка завершения 0.32

Состояние на 2026-09-12: **production PASS отсутствует**. Изменения локальные,
ветка codex/task-lifecycle-0.32, исходный HEAD 9edcece; package version 0.31.0.
R1–R5 реализованы и проверены на Windows; R6 имеет реальные live и packaging
evidence. C18 остаётся BLOCKED до фактического A23 Unix/symlink acceptance.
Для этого кандидат отправляется только в codex/task-lifecycle-0.32: GitHub Actions
запускает Ubuntu 24.04 (Python 3.11/3.12) и Windows 2022 (Python 3.12).
Workflow не получает API-ключи, не создаёт релиз и не изменяет main. Два теста
test_unix_verification_acceptance.py требуют настоящие POSIX symlinks и не могут
считаться пройденными по Windows skip. Результат CI будет записан после завершения.

## C01–C18

| ID | Статус | Доказательство / границы |
| --- | --- | --- |
| C01 | PASS | Typed schema 2: test_verification_provenance.py, context store, frozen repair transfer; два расширенных live проверяют task/job attribution и parent context после restart |
| C02 | PASS | test_verification_environment.py: реальные отдельные venv, правильный/чужой .pth, regular/src namespace и custom setuptools lib. Не blanket support произвольных build backends |
| C03 | PASS | test_project_checks.py, test_verification_context_store.py: source/config/lock/dependency/environment drift делает evidence stale |
| C04 | PASS | test_verification_check_plan.py: default, explicit, mypy.ini/setup.cfg; missing tools — unavailable |
| C05 | PASS | test_verification_gate.py: subset/mixed/duplicate/legacy receipts не project PASS; в обоих live 5 checks одного context |
| C06 | PASS | test_checked_process.py: реальный subprocess, descendant late-write veto, timeout, preflight cancel, original cancellation при cleanup error; source drift tests. Границы остановки ниже |
| C07 | PASS | Task/job SQLite write-lock: bounded retry success и fail-closed; test_task_lease_lifecycle.py, test_release_recovery.py |
| C08 | PASS | Два независимых heartbeat, очередь одного worker, handoff/soft yield; test_task_lifecycle_acceptance.py, test_web.py, test_autopilot.py |
| C09 | PASS | 5 записей при page size 2, три открытия БД: нет starvation; persistent keyset cursor |
| C10 | PASS | Legacy expired без достоверной связи → reconciliation_required; живой owner не захватывается. SQLite backup копия + повтор миграции сохраняет историю |
| C11 | PASS | Два isolated live: real implementation → read-only FAIL → explicit separate REPAIR → 5 checks PASS. Source task/test file неизменны |
| C12 | PASS | Реальные crash-processes, fencing takeover, cancel, recovery receipt без повторной мутации; повтор approval terminal repair не переочередяет job |
| C13 | PASS | Браузер на 8774: две вкладки, reload/restart/offline; отправка verification из чата. Исправлены потеря выбора и чужой/stale result card |
| C14 | PASS | test_execution_view.py, API scope/CSRF tests: default job DTO без prompts, raw logs, owner/interpreter host paths; durable SSE сохраняет allowlisted execution metadata |
| C15 | PASS | live_release_completion.py --runs 2, два чистых workspace/БД/venv; реальные zhipu/glm-5.3 и разрешённый openai fallback |
| C16 | PASS | test_http_fallback.py: настоящий loopback HTTP + controlled model adapter, timeout/fallback, manual configured chain, local-only veto, bounded attempts. Не тест реальной LLM или OpenAI SDK loopback transport |
| C17 | PASS | wheel/sdist build; clean_install_smoke.py: fresh venv вне checkout, pip check, CLI help, import из site-packages, Web startup/health/static twice; additive copied DB test |
| C18 | BLOCKED | R1 typed schema/provenance, namespace и A17 paging закрыты. A23 Unix/symlink venv не выполнен: WSL без дистрибутива, Docker отсутствует, Windows symlink privilege недоступна. Версию/релиз не повышать |

## Дополнительная итерация R1 — 2026-09-12

Schema 2 хранит canonical immutable контекст с IDs, runtime capability snapshot,
кодом/config/lock, версиями dependencies/tools, origins и launch/prefix. Public
receipt не содержит private paths. Новый run создаёт новый ID; schema 1 может
указывать прежний root, но не стать новым PASS без текущих проверок. Saved root
выбирается до discovery; явный конфликт отвергается. Permission snapshot не
используется для восстановления/повышения прав.

tests/test_root_discovery_paging.py проверяет restart, scopes, смену дерева,
отмену, отсутствие блокировки heartbeat writer и 2 000 файлов (половина в .venv).
Обход не принимает первый найденный manifest до завершения страниц; с двумя
реальными проектами возвращает ambiguity. Все 7 tests PASS (1.40 s).
Общий scanner использует artifact policy, durable frontier/offset/directory signature
и CAS. Portable replay внутри одной директории ограничен временем, без обещания
O(1) seek; исчерпание бюджета или изменение дерева возвращает точный blocker.

Первый повтор с новой schema 2 до дополнительных provenance assertions:

| Root basename | Source → repair | Время | Итог |
| --- | --- | --- | --- |
| dca-release-live-r6maa2q9 | c76cfa6e3adf1aaf94580363 → cc6fbf0e898e7b3f5b0942cc | 154.95 s | PASS |
| dca-release-live-tbh4sk7c | 146e7027d093436d470b0eff → b067d643c520e616ffcacea5 | 85.56 s | PASS |

Расширенные повторные live на финальном runtime/context contract:

| Root basename | Source → repair | Время | Итог |
| --- | --- | --- | --- |
| dca-release-live-d_yctnxc | 97c2ef48273f01291c352273 → 378ebcc06e23f78e83991ec4 | 143.80 s | PASS |
| dca-release-live-f0ceol6v | 4ac5735ff04199dc22d564d5 → f426714a93590b1b68661645 | 121.64 s | PASS |

В последних двух acceptance.json зафиксирован context_contract schema2 PASS:
task/job IDs, parent_context_id, saved_context root, dependency/config metadata,
новый source hash и checks_allowed без write_allowed. Пять checks PASS;
source task и тесты не изменены. Использована GLM-5.3; во втором расширенном
прогоне создание fixture использовало разрешённый OpenAI fallback gpt-5.6-sol.

Web cancellation повторён на текущем коде: dca-web-cancel-pslsxkoo, PASS 0.313 s,
pytest process exited=true, late_write=false. Job d7a23481151c15ff7fd2f5b1,
saved task 1f4d790362d848019cb891d54c0abf24, Web 7f1ba8a78b954b3db0097292032b8ed9.
Это настоящий subprocess/Web endpoint без LLM, не симуляция provider response.

Wheel/sdist повторно собраны. Последний wheel переустановлен в прежний isolated
clean-venv dca-clean-install-ylfj_u0a вне checkout: pip check, CLI, Web ×2 PASS.
Frontend TypeScript, bundle (78 541 bytes) и model_choices — PASS.
Полная регрессия перед добавлением wide-tree test: 514 passed / 1 skipped (68.62 s),
Ruff/format (125 files), mypy (34 source files), compileall PASS. Финальный повтор
после wide-tree test: **515 passed / 1 skipped (69.79 s)**, Ruff/format PASS.
Повтор lifecycle/SQLite/process/paging subset: **45 passed (15.42 s)**.
Пользовательский сервер/Ozon не менялись.

## Реальные live-артефакты

Корневые имена находятся в %TEMP%; секреты и полные локальные diagnostics
не включаются в git. Для каждой папки сохранены acceptance.json и SSE-файлы.

| Run | Root basename | Source job → repair job | Время |
| --- | --- | --- | --- |
| 1 | dca-release-live-hpqvfslu | beb3907a2c380e7a976e32b1 → 14d0c1138e19dd5024d65eb0 | 126.20 s |
| 2 | dca-release-live-o3pa2lsq | dbe927095dff1c188e5ffcb6 → 36a9d5d092c05505e356f04e | 103.53 s |

Вложенный /workspace/ozon_like, отдельный .venv, Ruff/pytest/mypy установлены
только в fixture. LLM создаёт намеренно неиспользуемый import в calc.py;
Ruff фиксирует F401; подтверждённая repair-задача изменяет этот файл и проходит
ruff_check, ruff_format_check, pytest, mypy, compileall. Хэши тестов не изменены.
Это доказательство сценария с однозначным target, не способности исправить любую
ошибку любого проекта. Неоднозначный traceback требует отдельного подтверждения
подходящих файлов, а не скрытого расширения write scope.

Выявлены и исправлены по live evidence:

- REPAIR подменял подтверждённый file target корнем из текста objective;
- unit не получала frozen verification evidence и искала внутренний proposal ID;
- повтор approval мог переочередить terminal job;
- UI терял чат/result после reload и показывал старую зелёную карточку в новом чате;
- SQLite retry budget exhaustion ошибочно классифицировался как lease expired;
- отмена внутри VERIFY оставляла job running: добавлена fenced финализация
  escaping exception и active units, с сохранением исходной причины.

Браузерные read-only jobs: 5ba8bf3f0d79e17b4d9ddbe6,
77cc88c31228cb0a7b21e852; обе 5 checks PASS. До браузерных запусков после
двух вкладок/reload/restart в fixture оставались 2 jobs и 5 tool receipts:
никакого повторного запуска или мутации от просмотра.
После двух явно отправленных browser проверок — 4 jobs / 7 tool receipts;
повторный reload счётчики не меняет.

Дополнительный live без LLM: scripts/live_web_cancel.py запускает настоящий
pytest с 30-секундным ожиданием в новом nested venv, затем вызывает тот же
cancel endpoint, что и UI. Первый запуск выявил job=running после Web cancel;
исправлен abort_execution с fencing. Повторные результаты:

- dca-web-cancel-g2ct5ezu: PASS, 0.343 s после отмены;
- dca-web-cancel-13a8o9pa: PASS, 0.312 s, PID pytest завершён, поздней записи нет.

Во втором прогоне Web c09be5715c724c46a9db24682604e614,
job 948a5efb30512ab9bfb37c3e, saved task 24fa00f4bf884e309012ce9d2c3027c1
имеют cancelled. Fenced abort не может перезаписать нового owner (unit regression).
Воспроизводимые acceptance.json/events.txt лежат в соответствующих TEMP-папках.

Clean installation: %TEMP%/dca-clean-install-o29vforl/acceptance.json и
%TEMP%/dca-clean-install-ylfj_u0a/acceptance.json — PASS,
два запуска Web на одной тестовой БД. Первый пробный smoke имел ошибку assert
имени HTML-id в harness; исправлен на chat-job-status, повтор выполнен в новом
venv. Не считать первый прогон PASS.

## Команды и результаты

Из корня проекта, интерпретатор .venv/Scripts/python.exe:

- -m ruff check --no-cache .
- -m ruff format --check --no-cache .
- -m pytest -ra
- -m mypy src/context_agent --ignore-missing-imports
- -m compileall -q src/context_agent
- -m pytest tests/test_release_recovery.py tests/test_task_lease_lifecycle.py tests/test_task_lifecycle_acceptance.py tests/test_checked_process.py -ra
- -m build --outdir .pytest-tmp/release-build
- scripts/clean_install_smoke.py --wheel .pytest-tmp/release-build/deep_context_agent-0.31.0-py3-none-any.whl

Полная регрессия: 496 passed / 1 skipped (67.16 s). Ruff/format (122 files),
mypy (33 source files), compileall, TypeScript, frontend bundle/model-choice tests — PASS.
Lifecycle/SQLite/cancel subset до двух дополнительных preflight/cleanup tests:
36 passed twice (13.09 s / 13.25 s); новые subprocess tests отдельно 6 passed.
Регрессии initial heartbeat failure и abort/cancel подтверждают, что cleanup
не маскирует причину ошибки и не перезаписывает нового владельца.
Опциональный платный live harness использует существующие ключи; запускать только
с разрешением: python scripts/live_release_completion.py --runs 2.

## A01–A34: трассировка без подмены evidence

| Исходные IDs | Evidence / незакрытая часть |
| --- | --- |
| A01–A05 | dual heartbeat, real silent process, CAS, takeover, handoff, queue и soft-yield tests |
| A06–A09 | cancellation/late-worker tests, terminal outbox, отмена и superseded projection |
| A10–A14 | restart/outbox/crash, keyset fairness, additive migrations, SQLite contention, bounded provider attempts |
| A15–A16 | nested root/ambiguous/unsafe seed tests; browser/live используют /workspace/ozon_like |
| A17 | PASS: persisted frontier, restart/cancel/CAS, pruning, wide-tree ambiguity, entries/time/checkpoint caps; test_root_discovery_paging.py |
| A18–A20 | runtime verification-only/development tests, frozen repair root/approval и два live restart |
| A21–A24 | own venv/.pth/wrong origin/missing tools tests; Windows Unicode paths live. A23 Unix symlink venv здесь НЕ выполнен |
| A25–A27 | drift/plan/gate/failure fingerprint regression |
| A28–A29 | redacted bounded returned output, scope veto, source drift; subprocess — не OS sandbox для недоверенного pytest |
| A30–A31 | browser/API/SSE, terminal projection/restart и SQLite backup migration |
| A32–A33 | Ozon-like FAIL→REPAIR→PASS ×2 + отдельно controlled timeouts, cancellation и crash tests |
| A34 | Quality/build/install выполнены; общий release BLOCKED из-за непроведённого A23 Unix/symlink acceptance |

## Эксплуатационные ограничения

Отмена проверяет authority каждые 100 ms и завершает запущенный runner-процесс
и его обычное дерево (Windows taskkill /T, POSIX process group), включая preflight.
Это не обещание нулевой задержки: ОС/cleanup ограничены отдельными таймаутами.
Windows detached/reparented процессы вне контролируемого дерева не обеспечены
Job Object sandbox; adversarial checks нельзя считать изолированными.
Symlink test пропущен из-за отсутствия Windows privilege. Полный security PASS
для symlink/junction и межплатформенный certification не заявляются.
Рабочий сервер пользователя, реальный Ozon, пользовательские БД и ключи не менялись.
