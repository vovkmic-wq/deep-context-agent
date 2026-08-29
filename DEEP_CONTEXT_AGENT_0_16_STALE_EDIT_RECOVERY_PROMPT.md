# Production-промпт Deep Context Agent 0.16.0

## Цель

Устранить причину сбоя, при котором `edit_file` получает устаревший
`old_string`, возвращает `String not found in file`, а обычный лимит
чтений не позволяет агенту получить свежее состояние. Исправление
обязано быть атомарным, ограниченным и не ослаблять workspace/security
политики.

## Обязательная реализация

1. В runtime классифицируй только exact-match ошибки `edit_file`:
   - `String not found in file`;
   - несовпадение trailing newline в `old_string`;
   - неоднозначное множественное совпадение.
2. Не ослабляй exact replacement и не применяй fuzzy/silent edit. Первая
   match-ошибка для точного path/version выдаёт доверенный
   `stale_edit_conflict` и одноразово разрешает целевой `read_file` того же
   файла, даже если normal read budget исчерпан.
3. Recovery-read можно использовать ровно один раз и только для
   conflict path. После него модель обязана построить новый уникальный
   `old_string` только из свежего ToolMessage и один раз повторить
   `edit_file`.
4. Второй match-conflict того же path/version не открывает новый
   budget. Runtime возвращает `stop_and_report_conflict`; агент прекращает
   чтения/мутации этого пути и сообщает о конфликте.
5. Не возвращай модели или Web UI исходный failed `old_string`: он может
   содержать секреты и раздувать контекст. Возвращай bounded JSON с
   operation, virtual path, status, error type, recovery и safe message.
6. Неуспешный edit не считается мутацией, не меняет path version и
   не даёт ложный success. Успешный revised edit меняет version и закрывает
   recovery state.
7. Сбрасывай recovery ledger между user turns. Не переноси conflict authority
   в другой thread/path и не расширяй write permission.

## Обязательные тесты

1. Unit/integration: два успешных чтения, внешнее изменение файла,
   stale edit, третье recovery-read, revised edit, точное итоговое
   содержимое и audit statuses `success, success, error, success, success`.
2. Negative: второй stale edit, затем ещё один read; второй edit даёт
   `stop_and_report_conflict`, read имеет `denied`, файл не изменён.
3. Regression: третье обычное чтение без stale conflict по-прежнему
   `denied`; duplicate mutation, read-only audit, exact path и secret policies не меняются.
4. State-machine: revised edit до свежего recovery-read отклоняется;
   после чтения ровно один revised edit может дойти до backend.
5. Live deterministic: тот же реальный `AgentRuntime`/FilesystemBackend с
   внешней мутацией между чтением и edit обязан завершиться
   recovery success.
6. Live provider: реальный LM Studio или настроенный remote LLM должен
   получить structured conflict, выполнить recovery-read/revised edit и
   оставить точные tool evidence.
7. Полный contour: Ruff lint/format, mypy, pytest, compileall, pip check,
   TypeScript/bundle, wheel build/install/inspection, secret scan.
8. Live regression: workspace path не может быть source для
   `read_context_window`; invalid radius возвращает safe error вместо
   падения graph, а hard per-turn budget скрывает runaway tool.

## Документация и релиз

Обнови `IMPLEMENTATION_PROMPT.md`, `TECHNICAL_SPEC.md`, runtime system prompt,
README, changelog, implementation status, package/Web version. Публикуй только
после фактического PASS всех обязательных проверок. Перед commit/tag/push
проверь diff и секреты; не включай несвязанные untracked files.
