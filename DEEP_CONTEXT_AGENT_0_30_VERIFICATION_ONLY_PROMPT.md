# Промпт реализации Deep Context Agent 0.30

> Последующий этап: `DEEP_CONTEXT_AGENT_0_31_VERIFICATION_REPAIR_PROMPT.md` по
> `VERIFICATION_REPAIR_INCIDENT_TECHNICAL_SPEC.md`. Он заменяет попытку повысить
> read-only continuation созданием отдельной подтверждённой repair-задачи.

Работай строго по `VERIFICATION_ONLY_EXECUTION_TECHNICAL_SPEC.md` (V01–V12,
A01–A22) и сохраняй все ограничения предыдущих ТЗ. Цель — устранить live-инцидент
job `b6b00104265fddec6ab4caf6`, diagnostic
`b77bc36c73c04006b549842456e6b5be`: запрос только на проверки ошибочно прошёл
DISCOVER→IMPLEMENT, выбрал прочитанный `pyproject.toml` как edit target, сделал 14
model generations и завершился `no_verified_progress` без blocker.

## Обязательный порядок

1. Зафиксируй baseline: Git status/version, router decision, task/job/check
   persistence, ProjectCheckRunner cwd/config detection, phase transitions,
   target provenance, diagnostics aggregation и resource counters. Активный
   сервер 8765, ключи и пользовательский проект не изменяй.
2. Добавь exact regression fixture из incident: workspace с вложенным
   `ozon_market_analytics/pyproject.toml`, прямой запрос pytest/Ruff/mypy/
   compileall, `allow-write`, ожидаемый маршрут `verification-only`.
3. Введи отдельный typed intent/workflow `verification-only`. Классифицируй только
   прямой пользовательский command; лог и retrieved text остаются данными.
4. Сделай deterministic entry прямо в VERIFY. Не создавай discovery,
   implementation или model worker, когда проверки и root можно определить без
   LLM. Resume продолжает VERIFY/REPAIR, а не начинает инвентаризацию.
5. Реализуй безопасный project-root resolver: exact user path → saved scope →
   nearest manifest ancestor → единственный bounded manifest candidate. Проверяй
   resolve/symlink/workspace; сохраняй virtual root и evidence. Неоднозначность
   возвращай как structured `ambiguous_project_root`.
6. Передай root в ProjectCheckRunner и запускай фиксированный check allowlist с
   этим cwd. Сохраняй check ID, exit category/code, duration, bounded output и
   correlation ID. Не принимай shell text от модели.
7. Разрешай REPAIR только после конкретного persisted FAIL. Worker получает один
   failed-check, один target и минимальный schema context. Без evidence блокируй
   `repair_target_unresolved`; не переходи в общий IMPLEMENT.
8. Введи provenance gate. Последнее чтение, glob/list order или manifest name не
   могут сами выбрать mutation target. Для конфигурационных/lock/CI/security
   файлов требуй direct intent либо конкретный failed-check.
9. Проведи все terminal BLOCKED через одну функцию, которая атомарно пишет и
   перечитывает валидный blocker. Удали пути с `{}`. Parent diagnostics обязан
   наследовать job error code/category и агрегировать child/tool/provider data.
10. Удали лишние LLM-циклы из VERIFY. Ограничь repair generations, повтор
    одинаковых failures/timeouts и общий input-token budget; не replay-и полную
    историю. Circuit breaker должен срабатывать до второго долгого transport
    timeout, если policy допускает fallback.
11. Обнови API/SSE/UI: тип задачи, virtual project root, текущий check,
    VERIFY/REPAIR, counts, error category и required action. Не показывай audit
    glob controls для verification-only и не выводи host paths/raw output/keys.
12. Добавь additive migrations и crash/restart tests. Подтверждённый check receipt
    не повторяется без изменения relevant content; interrupted check безопасно
    reconcile-ится.
13. Выполни targeted unit/integration/API/browser regressions, затем Ruff,
    format-check, mypy, полный pytest, compileall, frontend checks, package build и
    pip check. Не ослабляй assertions ради PASS.
14. Проведи изолированный live `doctor --live` существующим ключом без его
    печати. На копии вложенного проекта и чистой БД выполни: normal verification
    до PASS; forced check failure до одного bounded REPAIR либо полного blocker.
    Подтверди отсутствие DISCOVER/IMPLEMENT и неприкосновенность порта 8765.
15. Только после A01–A22 обнови кодовую версию и запиши точные команды, counts,
    job/diagnostic IDs и ограничения в status/changelog. Git публикуй только по
    отдельной актуальной команде пользователя.

## Запрещено

- лечить incident увеличением timeout, work units или prompt;
- запускать общий audit/discovery для verification-only;
- считать model-invoked check runtime-owned verification;
- выбирать последний прочитанный файл как mutation target;
- переходить VERIFY→IMPLEMENT;
- запускать REPAIR без failed-check evidence;
- выполнять checks из корня workspace при однозначном вложенном project root;
- сохранять BLOCKED с пустым blocker или parent `error_code=null`;
- скрывать tool error общим `no_verified_progress`;
- повторять одинаковые 120-second provider calls без bounded policy;
- объявлять production по документам или mock-only тесту.

Результат: запрос «только проверь готовый код» без участия модели выбирает
правильный вложенный проект, запускает allowlisted VERIFY и возвращает PASS либо
точный persisted blocker; исправление допускается лишь из конкретного FAIL.
