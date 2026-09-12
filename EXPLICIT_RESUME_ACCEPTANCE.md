# Приёмка явного продолжения — 0.33.0

Дата: 2026-09-12. Область: локальный агент для доверенных проектов.
Промпт: EXPLICIT_RESUME_IMPLEMENTATION_PROMPT.md; ТЗ: EXPLICIT_RESUME_TECHNICAL_SPEC.md.

## Матрица R1–R6 / A01–A12

| Требование | Реализация и доказательство |
| --- | --- |
| R1 / A01–A03 | Typed resume не создаёт даже фабрику classifier. CAS, thread/workspace, owner, read-only сохраняются. `test_explicit_resume.py`, `test_explicit_resume_api.py`, `test_task_actions.py`. |
| R2 / A05–A08 | Новая связанная verification-only identity, явный manifest root, источник неизменен. Реальный HTTP FAIL → подтверждённый REPAIR → пять checks PASS. |
| R3 / A05 | Ограниченная read-only сверка checkpoint, execution link, generation и receipts. Quarantine не снимается, неизвестные записи не воспроизводятся. Повреждённые/слишком большие evidence отклоняются. |
| R4 / A10 | Отказы до model turn журналируются с action/revision/reason. Structured recovery и терминальная диагностика проверены API-тестами и live после restart. |
| R5 / A04, A09 | SQLite reservation связывает stable execution ID и payload; повтор с другим payload конфликтует. Restart, две вкладки, сбой между reserve/dispatch, восстановление child проверены без двойного worker. UI сохраняет ключ и черновик. |
| R6 / A11 | Повторные реальные GLM/HTTP прогоны, полная локальная регрессия, frontend, проверка пакета; точные результаты ниже. |
| A12 | В Git допускаются только код, тесты, документация и bundle. Ключи, env, SQLite и сырые пользовательские логи не включаются. Публикация/CI фиксируются после локального gate. |

## Автоматические проверки

Команды выполняются из корня Deep Context Agent его `.venv`:

```powershell
.\.venv\Scripts\python.exe -m ruff check --no-cache .
.\.venv\Scripts\python.exe -m ruff format --check --no-cache .
.\.venv\Scripts\python.exe -m pytest -ra
.\.venv\Scripts\python.exe -m mypy src/context_agent --ignore-missing-imports
.\.venv\Scripts\python.exe -m compileall -q src/context_agent
.\.venv\Scripts\python.exe -m pip check
```

Frontend: `pnpm run check`, `pnpm run build` в `webui`; TypeScript, bundle,
model choices, task actions — PASS. После финального review:
**582 passed, 3 platform skips, 86.99 с**. Ruff check/format, mypy (35 файлов),
compileall, pip check — PASS. Пропуски: один Windows symlink capability и два
обязательных для POSIX сценария; для Linux предусмотрен отдельный CI gate.
Clean install: новая полная изолированная `.venv` (`dca-clean-install-olyh1ek1`),
без system-site-packages; импорт из её site-packages вне checkout, version и
installed metadata 0.33.0. `pip check`, CLI help и Web startup/restart — PASS.
HTTP smoke на отдельном loopback-порту без model calls: два startup/restart PASS.
Финальный wheel повторно установлен после синхронизации prompt/static;
все 39 файлов пакета побайтно совпали с src и установленными файлами.
Wheel SHA-256: `8e9ce509049a76bce1079483fd45fa7ea2f546323bd4a1e0450cf75d05bc17d0`.
Git CI запускается публикацией проверенной рабочей ветки; результаты доступны
в [Verification acceptance](https://github.com/vovkmic-wq/deep-context-agent/actions/workflows/verification.yml).

## Реальные HTTP и LLM

Воспроизводимый opt-in сценарий: `scripts/live_explicit_resume.py`.
Он создаёт собственные TEMP workspace/data/venv и сервер на свободном loopback
порту. Используется существующая конфигурация provider; отправляется только
искусственный тестовый проект, не пользовательский Ozon. Проверки выполняются
на реальных subprocess; LLM исправляет реальный F401.

- Первый PASS: `dca-explicit-resume-live-i2xd94i9`, 109.06 с.
- Повтор PASS: `dca-explicit-resume-live-w69z6hph`, 86.84 с.
- Финальный hardened-dispatch PASS: `dca-explicit-resume-live-tjkagl2l`, 94.23 с;
  12 успешных provider attempts `zhipu / glm-5.3`.
- Источник: `ebabeb3a33dc4f65b871d19519526f46`, source unchanged.
- Связанная saved task: `778f0cf8ebf0461bb5a0e14997370571`.
- VERIFY job: `4c2a04d4a7c9ad1700b6bad6`, ожидаемый F401.
- Approved REPAIR job: `68dbca639cedf721e17109da`.
- Ruff check, Ruff format check, pytest, mypy, compileall: `passed`, exit code 0.
- Корень `/workspace/ozon_like`, собственная `.venv`, общий source SHA-256:
  `e3fedcc702727bf338e1c1e5047736d89e8a7d7c2c5682bf3f275ccc59a8eb44`.
- Повтор после restart возвращает тот же execution; в БД ровно два jobs
  (VERIFY и REPAIR). Durable rejection diagnostic сохранена.

После финальных исправлений legacy routing/quoted paths прогон повторён:
`dca-explicit-resume-live-zymbb_ho`, **PASS, 117.23 с**.
Source `6aec487fb5784706b53c9030d454b879` не изменён;
linked task `1362cf705dae4582996e7b758bd67b14`;
VERIFY `d759af56a7f826d8dd053056` → approved REPAIR `ab06aaa002a94722643d0fb9`.
Все пять checks вновь `passed/0`; restart replay, durable diagnostic и ровно
два jobs подтверждены. Это последний LLM acceptance данной реализации.

В промежуточном прогоне `tt3skw_2` harness получил transient SQLite I/O при
немедленном чтении после принудительной остановки Windows process tree.
Повторная проверка: integrity_check=ok, события сохранены. Исправлен harness:
штатное `uvicorn.Server.should_exit`, ожидание lifespan/закрытия БД;
принудительное завершение только как аварийный timeout fallback. Финальный
прогон выше выполнен после исправления. Старые доказательства не удалялись.

## Интерфейс

В изолированном сервере проверены reload, восстановление выбранного чата/задачи,
quarantine, недоступность Resume и доступность Reconcile/New verification.
Reconcile через кнопку вернул EXECUTION_LINK_UNVERIFIED, без изменения источника.
Нативный confirm New verification показывает source ID, root и read-only смысл.
Закреплённые header/composer видны при 1280×720; расширенные действия сворачиваются
и ограничены по высоте. Двойное нажатие, подтверждение/отмена, pending без execution,
восстановление child и сохранение черновика дополнительно проверены UI-тестами.

Ограничение браузерной автоматизации: после native confirm инструмент потерял
доступ к диалогу; полный submit этого клика не засчитывается как браузерный PASS.
Тот же серверный путь целиком проверен реальным HTTP и UI controller tests.
По CSS review исправлен перенос composer controls на узком экране; отдельная
регрессия проверяет mobile flex-wrap. Полная мобильная visual QA не заявляется.

## Дополнительные регрессии после review

- Legacy verification-only с устаревшими flags нормализуется в fixed checks без
  scan/записи, scope и сохранённый routing не меняются.
- Ask/Plan отклоняются до claim и через совместимый `/api/chat`; отказ журналируется.
- Quoted Unicode путь с пробелами не создаёт усечённый seed родительского root.
- Неизменность записи задачи и расхождение хешей файлов — отдельные поля:
  `source_changed` и `workspace_files_changed`.

## Границы результата

- Не предоставляется автоматическое восстановление потерянного плана разработки
  при недоказанной execution link; безопасный путь — новая связанная проверка.
- Crash с неизвестным исходом dispatch не разрешает автоматический повтор:
  возвращаются причина, существующий execution/child или явная сверка.
- Проверка не меняет код; REPAIR требует отдельного действующего approval.
- Не заявляется итоговый PASS реального проекта Ozon. Его код и рабочие БД,
  пользовательский сервер на 8765 не изменялись и не перезапускались.
