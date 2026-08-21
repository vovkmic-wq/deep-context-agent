# Deep Context Agent

CLI-агент на Python и Deep Agents с долговременным SQLite-контекстом,
поиском и безопасным чтением публичных веб-страниц, а также ограниченной
файловой системой. Поддерживаются LM Studio,
OpenAI, YandexGPT, DeepSeek и Qwen через OpenAI-compatible API.

## Как устроен большой контекст

Контекст размером в миллион строк нельзя целиком и надёжно разместить в окне
любой LLM. Проект хранит полный корпус в SQLite FTS5 и передаёт модели только
релевантные фрагменты:

1. Файлы читаются потоково, разбиваются на перекрывающиеся чанки и записываются
   пакетами. Объём RAM зависит от размера чанка, а не корпуса.
2. Все чанки, включая начало и конец каждого документа, сохраняются между
   запусками.
3. Перед каждым запросом выполняется автоматический BM25 retrieval.
4. Агент может повторно вызвать `search_context`, ограничить поиск одним
   источником, перечислить сотни источников и раскрыть соседние чанки через
   `read_context_window`.
5. Полные пользовательские запросы и ответы также индексируются как
   долговременная память.

FTS5 — лексический поиск: он не требует embedding API и хорошо масштабируется,
но для терминов без общих слов может понадобиться несколько формулировок.
Системный промпт явно требует от агента повторять поиск при необходимости.

## Установка

Требуется Python 3.11 или новее.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
```

Не помещайте реальные ключи в `.env.example`. Локальный `.env.local` исключён
из Git.

## Настройка провайдеров

Общий выбор:

```dotenv
AGENT_PROVIDER=lmstudio
AGENT_WORKSPACE=./agent_workspace
AGENT_CONTEXT_ROOT=./agent_workspace
AGENT_DATA_DIR=./.agent_data
```

### LM Studio

Запустите Local Server в LM Studio и загрузите модель с поддержкой tool calling.

```dotenv
LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_MODEL=<идентификатор загруженной модели>
LM_STUDIO_API_KEY=
```

Если локальная модель не умеет корректно возвращать OpenAI tool calls, обычный
чат может работать, а файловые операции и retrieval tools — нет.

### OpenAI

```dotenv
OPENAI_API_KEY=<секрет>
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=https://api.openai.com/v1
```

### Yandex AI Studio / YandexGPT

```dotenv
YANDEX_API_KEY=<секрет>
YANDEX_FOLDER_ID=<folder_id>
# Либо задайте полный URI:
YANDEX_MODEL_URI=gpt://<folder_id>/yandexgpt/latest
YANDEX_BASE_URL=https://ai.api.cloud.yandex.net/v1
```

API-ключу нужна область `yc.ai.languageModels.execute`. Модель должна
поддерживать function calling.

### DeepSeek

```dotenv
DEEPSEEK_API_KEY=<секрет>
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

По умолчанию thinking отключён, чтобы сделать агентный tool-calling цикл более
предсказуемым.

### Alibaba Model Studio / Qwen

```dotenv
DASHSCOPE_API_KEY=<секрет>
QWEN_MODEL=qwen3.7-plus
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

Для новых workspace-specific endpoint замените `QWEN_BASE_URL` значением из
консоли Model Studio. Thinking по умолчанию отключён для tool calling.

## Использование

```powershell
# Проверка конфигурации без вывода ключа
.\.venv\Scripts\context-agent.exe --provider openai doctor

# Небольшой реальный запрос к модели
.\.venv\Scripts\context-agent.exe --provider openai doctor --live

# Индексировать всю разрешённую директорию или отдельный файл
.\.venv\Scripts\context-agent.exe index .
.\.venv\Scripts\context-agent.exe index docs\manual.txt

# Проверить retrieval без LLM
.\.venv\Scripts\context-agent.exe search "условия договора"

# Одиночный запрос и интерактивный режим
.\.venv\Scripts\context-agent.exe --provider openai ask "Найди условия расторжения"
.\.venv\Scripts\context-agent.exe --provider lmstudio --thread work chat
```

Путь команды `index` обязан находиться внутри `AGENT_CONTEXT_ROOT`.

## Безопасность файлов

- Реальный диск доступен Deep Agent только по маршруту `/workspace/`.
- `FilesystemBackend` работает с `virtual_mode=True` и отдельным
  `AGENT_WORKSPACE`.
- `.env.local` и SQLite БД по умолчанию находятся вне этой директории.
- Дополнительные инструменты повторно проверяют пути через `Path.resolve()`;
  выход через `..` или symlink блокируется.
- Удаление корня рабочей директории запрещено.
- Shell execution агенту не предоставляется.
- Веб-загрузчик принимает только HTTP(S) на стандартных портах, блокирует
  loopback/private/non-public адреса, повторно проверяет перенаправления и читает
  не более 1 МБ текста за запрос.
- Веб-страницы, сниппеты и документы считаются недоверенными данными и не могут
  переопределять системный промпт.

Для production или обработки недоверенных пользователей вместо локального
filesystem backend используйте изолированный sandbox и human-in-the-loop.

## Проверка проекта

```powershell
.\.venv\Scripts\python.exe -m ruff check --no-cache .
.\.venv\Scripts\python.exe -m ruff format --check --no-cache .
.\.venv\Scripts\python.exe -m pytest
```

Обычные тесты не вызывают платные API. Реальный сетевой smoke-test запускается
только явно через `doctor --live`. Pytest использует временный каталог текущего
пользователя и не создаёт общий `.pytest_cache` в проекте. Это предотвращает
конфликт ACL, если проект по очереди проверяют Codex sandbox и обычный Windows-
пользователь. Ruff запускается с `--no-cache` по той же причине.

## Ограничения

- Качество вызова инструментов зависит от выбранной модели.
- DDGS может быть недоступен из некоторых сетей или временно ограничивать
  запросы.
- Некоторые сайты блокируют автоматическое чтение или формируют содержимое
  только JavaScript-кодом; агент обязан сообщить, что точная проверка не удалась.
- FTS5 ищет по словам, а не по embedding-сходству. Для сложных семантических
  запросов агент выполняет несколько уточняющих поисков.
- Изменение файлов агентом необратимо; используйте отдельную рабочую директорию
  и систему контроля версий.
