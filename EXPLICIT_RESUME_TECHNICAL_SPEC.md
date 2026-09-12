# ТЗ: явное продолжение и проверяемое восстановление

Статус: реализовано; границы приёмки в EXPLICIT_RESUME_ACCEPTANCE.md.
Основа: DCS (https://habr.com/ru/articles/1064052/).
Текст описывает механизм, но не является разрешением обходить ограничения.

Целевой релиз: 0.33.0. Это дополнение, а не замена требований 0.30–0.32.
Дайджест https://habr.com/ru/articles/1055452/ используется как контекст
инженерной обвязки, не как доказательство реализации или безопасности.

## R1. Типизированное действие
UI/API передают task ID, ожидаемую ревизию и действие. Семантическая модель
не участвует в явном продолжении. Принадлежность диалогу/workspace, CAS,
lease и текущие разрешения обязательны. Обычный текст не повышает права.

Контракт: POST /api/tasks/{id}/resume с thread_id, expected_revision,
action=continue|continue_verification, allow_write и idempotency_key.
POST /api/chat с explicit_action обязан проходить тот же сервис действий.
Выбранная модель/текст не заменяют принадлежность задачи и разрешения.
Старые terminal cancelled/completed не возобновляются скрыто.

## R2. Верификация
continue_verification запускает VERIFY без IMPLEMENT и широкого аудита.
Проверки используют валидированный project root/environment и текущий код.
Read-only сохраняется; REPAIR требует отдельного подтверждения.
Для старой verification-only задачи typed действие нормализует только права
проверки: checks=true, scan=false, mutation=false при сохранённом scope.
Ask/Plan отклоняются до claim (ACTION_NOT_AVAILABLE_IN_MODE); они не должны
превращаться в Agent при прохождении совместимого /api/chat контракта.
Quoted virtual путь с пробелами/Unicode разбирается один раз целиком, без
усечённого дубликата, который мог бы выбрать родительский pyproject.toml.

Для development-задачи, смена которой на VERIFY изменила бы исходный workflow,
предусмотрен POST /api/tasks/{id}/verification. Он создаёт новую связанную
verification-only задачу; требует явный virtual project_root и ожидаемую ревизию.
Корень должен быть внутри workspace, содержать pyproject.toml, пройти текущие
проверки путей. Никакое разрешение из старых evidence не импортируется.
Связь, новый task ID и reservation сохраняются атомарно. Исходные objective,
status, revision, checkpoint и verification evidence не изменяются.

## R3. Восстановление
reconciliation_required не снимается автоматически. Сверяются execution link,
checkpoint, receipts и текущая ревизия. Неизвестные записи не повторяются.
При невозможности доказать связь предлагается новая связанная read-only задача
с явно заданным корнем проекта. Источник и его evidence сохраняются.

POST /api/tasks/{id}/reconcile выполняет ограниченную сверку без возобновления:
scope/thread, lease, projection, последняя execution link, task identity,
generation, состояние job, checkpoint и наличие receipts. Нет доказательства —
нет автоматического повторения. Сверка возвращает reason_code и доступные
действия. Даже найденная связь не даёт разрешение на повтор неизвестной записи.
Read-only связанная проверка — допустимый консервативный путь восстановления,
а не обещание восстановления потерянного плана разработки.
source_changed относится только к записи saved task и остаётся false;
workspace_files_changed отдельно показывает расхождение текущих файлов с receipts.

## R4. Диагностика
Отказ до LLM также получает долговечный request ID, action/task/revision,
причину и допустимый следующий шаг. Секреты не журналируются.

Каждый принятый API-контракт действия журналируется до reserve/dispatch.
Успех, повтор, stale revision, отказ ownership и неизвестная связь имеют
терминальную запись. CSRF/auth/невалидная схема остаются HTTP security boundary,
не создают model turn. Diagnostics off сохраняет действующий явный opt-out.
Оценки из истории чата не являются текущим статусом. UI получает snapshot
available_actions/status/revision из сервиса, не вычисляет права по тексту LLM.

## R5. UI
Отдельные действия продолжения, проверки и восстановления. Статус из runtime,
не из пересказа модели. Повторное нажатие не создаёт параллельное выполнение.

Идемпотентность долговечна в SQLite: тот же key+payload возвращает предыдущий
execution/result; другой payload с тем же key даёт конфликт. Только создатель
reservation может отправлять worker. После crash в промежутке reserve/dispatch
повтор не выполняется автоматически: возвращается pending/recovery information.
Две вкладки с разными ключами и старой ревизией не запускают два worker.
Обычный read-only отказ не исправляется автоматическим включением allow_write.
При создании новой проверки UI показывает её корень и требует подтверждения.

## R6. Приёмка
Автотесты: обход classifier только для explicit action; изоляция workspace/thread;
stale revision; двойной запуск; read-only; reconciliation; restart; development →
log analysis → verification. Live: изолированные БД и проект, фактический VERIFY,
ошибка проверки, явный REPAIR и повторные проверки. PASS только по evidence.
Публикация после Ruff/pytest/mypy/frontend и live-приёмки. Непройденные пункты
фиксируются явно, не заменяются количеством unit-тестов.

## Матрица обязательных доказательств

- A01: explicit resume не вызывает classifier, даже если тот падает.
- A02: text/log continuation не получает полномочия explicit action.
- A03: чужой thread/workspace, stale revision, active owner отклонены.
- A04: одинаковый запрос и restart возвращают тот же execution.
- A05: quarantine не снимается; source до/после сверки и создания child совпадает.
- A06: linked verification идёт сразу в VERIFY с собственным окружением проекта.
- A07: ошибка проверки даёт evidence, не скрытый IMPLEMENT.
- A08: явный repair использует существующий approval protocol и повторный PASS.
- A09: UI reload, выбор quarantine, доступные действия, двойное нажатие.
- A10: журнал принятия/отклонения не раскрывает ключи и сохраняет reason/action.
- A11: все настроенные проверки относятся к одной текущей версии исходников.
- A12: публикация только проверенного дерева, без env/БД/сырой live-диагностики.
