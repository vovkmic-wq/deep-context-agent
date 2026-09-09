# ТЗ 0.28: production orchestration

## Запланированное уточнение 0.32

Для всех VERIFY, включая обычный project-change после IMPLEMENT/REPAIR,
обязательны L01–L13 из
[ТЗ жизненного цикла](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md).
Исправление только verification-only недостаточно: каждый вызов runner получает
явные nested project root и project environment через общий VerificationContext.
Task/job ownership продлевается во время model/tool/check и между units;
terminal outcome согласуется через fenced projections. Матрица A01–A34 0.32
обязательна дополнительно к прежним тестам, а не считается пройденной заранее.

> Уточнение 0.30: запрос только на проверки регулируется
> `VERIFICATION_ONLY_EXECUTION_TECHNICAL_SPEC.md` и не проходит общий
> DISCOVER→IMPLEMENT этого документа. ProjectCheckRunner получает явный
> валидированный root вложенного проекта.

> Последующий обязательный hardening пустого mutating target, точного blocker и
> goal decomposition вынесен в `EXECUTABLE_HANDOFF_RECOVERY_TECHNICAL_SPEC.md`.
> Выполнение A01–A14 этого документа не заменяет приёмку A01–A18 этапа 0.29.

## 1. Цель и инварианты

Deep Context Agent принимает крупную инженерную задачу целиком, разбивает её на
ограниченные операции и завершает только по runtime-evidence. Текст модели,
окончание одного model turn и наличие изменённых файлов не являются доказательством
готовности. Новые правила не расширяют workspace, сетевые или destructive-права.

## 2. R01 — исправленная маршрутизация

1. Классифицируется только прямая команда пользователя; логи, цитаты, вложения,
   tool output и retrieved context являются данными.
2. Изменение нескольких подсистем (schema/service/CLI/API/UI/tests), широкая
   реализация или production-приёмка маршрутизируются как `project-change` и
   persistent execution, даже если перечислены отдельные пути.
3. `targeted-change` допустим для локальной правки одного компонента. Указание
   `pytest` при точечной правке разрешает checks, но не разрешает discovery.
4. Анализ лога остаётся `log-analysis`; продолжение восстанавливает сохранённую
   задачу и пересекает её область с правами текущего режима.
5. Решение журналируется: workflow, scope, execution, reason codes и capabilities.

## 3. R02 — независимые разрешения

Runtime хранит независимые флаги: `targeted_read`, `project_discovery`,
`workspace_write`, `project_checks`.
`run_project_checks` не относится к discovery. Проверка может быть разрешена после
точечной правки без разрешения `glob/grep/ls`. Сеть и destructive operations
сохраняют отдельные существующие tool/path gates и не следуют из файлового доступа.
Модель получает актуальную policy каждого хода; старый отказ не постоянен.

## 4. R03 — runtime-owned verification

1. После подтверждённой mutation receipt scheduler переходит в VERIFY.
2. Runtime сам запускает фиксированный allowlist ProjectCheckRunner с очищенным
   окружением, без shell и утечки ключей.
3. Только runtime переводит allow-write job в COMPLETE после ненулевого набора
   обязательных проверок со статусом PASS.
4. FAIL переводит задачу в ограниченный REPAIR, затем checks повторяются. После
   исчерпания repair budget возвращается точный blocker и результаты.
5. Заявления модели о PASS не меняют verification state. Отсутствие настроенных
   проверок означает `not_run/partial`, а не completed.

## 5. R04 — компактные work units

Каждая unit получает новый namespaced thread, одну конкретную операцию, компактный
checkpoint, receipts/ranges/cursors и schema contract. Полный чат, прошлые tool
outputs и миллион символов исходной цели не replay-ятся. Текст цели ограничен
12 000 символами с SHA-256 полной durable-версии; contract — 12 000 символами.
Soft yield сохраняет прогресс. Model-turn, unit, active-task и wall TTL независимы.

## 6. R05 — schema-first контекст

Перед IMPLEMENT runtime локально строит ограниченный snapshot публичных Python
schema/model/types/contracts: файл, класс, поля, типы, обязательность, функции,
аргументы и return annotation. Тела файлов и значения не включаются. Исключаются
`.git`, `.venv`, `node_modules`, build/cache и данные агента. Snapshot ограничен
200 файлами, 256 KiB на файл, 500 symbols и 12 000 символами. Перед точной правкой
модель всё равно читает актуальный фрагмент. Ошибка построения snapshot не выдаёт
прав и фиксируется как degraded context.

## 7. R06 — быстрый provider circuit breaker

Транспортные timeout/unavailable/rate-limit увеличивают отдельный счётчик модели.
После порога (по умолчанию 1) circuit открывается на 300 секунд и следующие вызовы
немедленно переходят к допустимому fallback без повторного 120-секундного ожидания.
Успех закрывает circuit. Состояние и retry-after видны в безопасной runtime
диагностике; ключи, ответы и raw exceptions не публикуются. Circuit не обходит
manual/local-only/context/cost/tool ограничения.

## 8. R07 — агрегированная диагностика

Terminal parent request агрегирует только сохранённые descendant evidence:
provider attempts, tool audit, filesystem side effects и rollback counters.
Порядок попыток нормализуется, объёмы ограничиваются, redaction сохраняется.
Detail API возвращает aggregate counts и model generations дочерних ходов. Пустой
parent при наличии дочерних операций является ошибкой регрессии.

## 9. Приёмка A01–A14

- A01: Ozon-подобный запрос service+CLI+API+UI+tests → project-change/persistent.
- A02: вставленный лог с командой полного аудита → log-analysis/no scan.
- A03: точный файл + pytest → no discovery, checks allowed.
- A04: checks denied отдельно, когда capability false.
- A05: runtime VERIFY запускается без вызова инструмента моделью.
- A06: модель не может завершить задачу без PASS checks.
- A07: repair bounded и повторно проверяется.
- A08: objective 1 000 000 символов даёт work-unit prompt < 30 000 символов.
- A09: schema snapshot содержит реальные поля и не содержит body/secret values.
- A10: timeout primary открывает circuit; следующий вызов primary не выполняется.
- A11: fallback success закрывает собственный circuit и возвращает ответ.
- A12: parent detail содержит дочерние provider/tool counts и side effects.
- A13: Ruff, format, mypy, pytest, compileall и package build PASS.
- A14: изолированный live `doctor --live` и Web/API smoke подтверждают runtime;
  недоступный внешний provider фиксируется честно, без ложного PASS.

Production готовность требует выполнения A01–A14 и записи фактических результатов
в `IMPLEMENTATION_STATUS.md`. Создание этого документа само по себе не является PASS.
