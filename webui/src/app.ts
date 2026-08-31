"use strict";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Payload = Record<string, Json>;

const pageTitles: Record<string, string> = {
  overview: "Обзор",
  chat: "Чат",
  context: "Контекст",
  audits: "Автопилот",
  files: "Файлы",
  providers: "Провайдеры",
  settings: "Настройки",
};

let csrfToken = "";
let activeChatTask = "";
let currentThread = "web";
let currentFilePath = "";
let currentFileSha = "";
let currentDirectory = "/workspace";
const directoryHistory: string[] = [];
let providerCatalog: Payload[] = [];
let activeProviders: string[] = [];

function element<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing UI element: ${id}`);
  return node as T;
}

function value(id: string): string {
  return element<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
    id,
  ).value;
}

function checked(id: string): boolean {
  return element<HTMLInputElement>(id).checked;
}

function text(data: Json | undefined): string {
  return data === undefined || data === null ? "" : String(data);
}

function showToast(message: string): void {
  const node = element<HTMLDivElement>("toast");
  node.textContent = message;
  node.classList.add("visible");
  window.setTimeout(() => node.classList.remove("visible"), 4_500);
}

function setOperationStatus(
  id: string,
  message: string,
  state: "normal" | "success" | "error" = "normal",
): void {
  const node = element(id);
  node.textContent = message;
  node.classList.toggle("success", state === "success");
  node.classList.toggle("error", state === "error");
}

async function api<T extends Payload>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("content-type", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("x-csrf-token", csrfToken);
  }
  const response = await fetch(path, { ...options, headers });
  const body = (await response.json().catch(() => ({}))) as T & {
    error?: { message?: string };
  };
  if (!response.ok) {
    throw new Error(body.error?.message || `HTTP ${response.status}`);
  }
  return body;
}

function streamTask(
  taskId: string,
  handler: (name: string, data: Payload) => void,
): EventSource {
  const stream = new EventSource(`/api/events/${encodeURIComponent(taskId)}`);
  const terminal = new Set(["completed", "cancelled", "failed"]);
  let terminalSeen = false;
  let recoveryPending = false;
  for (const name of [
    "started",
    "message",
    "audit_progress",
    "job_progress",
    "job_replanned",
    "job_verification",
    "result",
    "completed",
    "cancelled",
    "failed",
  ]) {
    stream.addEventListener(name, (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      const data = JSON.parse(event.data) as Payload;
      handler(name, data);
      if (terminal.has(name)) {
        terminalSeen = true;
        stream.close();
      }
    });
  }
  stream.onerror = () => {
    if (terminalSeen || recoveryPending) return;
    recoveryPending = true;
    void api<Payload>(`/api/tasks/${encodeURIComponent(taskId)}`)
      .then((status) => {
        const statusName = text(status.status);
        if (terminal.has(statusName)) {
          const terminalData =
            status.terminal && typeof status.terminal === "object"
              ? (status.terminal as Payload)
              : {};
          terminalSeen = true;
          handler(statusName, terminalData);
          stream.close();
          return;
        }
        showToast("Поток прерван; выполняется автоматическое переподключение");
      })
      .catch(() => {
        showToast("Не удалось проверить состояние задачи; поток переподключается");
      })
      .finally(() => {
        recoveryPending = false;
      });
  };
  return stream;
}

function navigate(panelId: string): void {
  document.querySelectorAll<HTMLButtonElement>("nav button").forEach((button) => {
    if (button.dataset.panel === panelId) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll<HTMLElement>(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === panelId);
  });
  element("page-title").textContent = pageTitles[panelId] || panelId;
  window.history.replaceState(null, "", `#${panelId}`);
}

function card(title: string, body: string): HTMLElement {
  const node = document.createElement("article");
  node.className = "card";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const content = document.createElement("p");
  content.textContent = body;
  node.append(heading, content);
  return node;
}

function appendMessage(
  role: "user" | "agent",
  content: string,
  pending = false,
): HTMLElement {
  const output = element("chat-output");
  output.querySelector(".empty-state")?.remove();
  const message = document.createElement("article");
  message.className = `message ${role}${pending ? " pending" : ""}`;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "Вы" : "Deep Context Agent";
  const body = document.createElement("div");
  body.className = "message-content";
  body.textContent = content;
  message.append(label, body);
  output.append(message);
  output.scrollTop = output.scrollHeight;
  return body;
}

function visibleArchivedMessage(role: string, content: string): string {
  if (role !== "human" || !content.startsWith("Режим работы:")) return content;
  const separator = content.indexOf("\n\n");
  return separator >= 0 ? content.slice(separator + 2) : content;
}

async function loadThread(threadId: string): Promise<void> {
  currentThread = threadId;
  element("current-thread-label").textContent = threadId;
  const output = element("chat-output");
  output.replaceChildren();
  const result = await api<{ items: Payload[]; request_id: string }>(
    `/api/threads/${encodeURIComponent(threadId)}/messages?limit=200`,
  );
  for (const item of result.items) {
    const role = text(item.role);
    appendMessage(
      role === "human" ? "user" : "agent",
      visibleArchivedMessage(role, text(item.content)),
    );
  }
  if (!result.items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const heading = document.createElement("strong");
    heading.textContent = "Новая задача";
    const note = document.createElement("span");
    note.textContent = "История появится здесь после первого сообщения.";
    empty.append(heading, note);
    output.append(empty);
  }
  await refreshThreads();
}

async function refreshThreads(): Promise<void> {
  const result = await api<{ items: Payload[]; request_id: string }>(
    "/api/threads?limit=100",
  );
  const threadIds = result.items.map((item) => text(item.thread_id));
  if (!threadIds.includes(currentThread)) threadIds.unshift(currentThread);
  const output = element("thread-list");
  output.replaceChildren();
  for (const threadId of threadIds) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `thread-item${threadId === currentThread ? " active" : ""}`;
    button.textContent = threadId;
    button.title = threadId;
    button.addEventListener("click", () => {
      void loadThread(threadId).catch((error: Error) => showToast(error.message));
    });
    output.append(button);
  }
}

async function sendChatMessage(): Promise<void> {
  const query = value("chat-query").trim();
  if (!query || activeChatTask) return;
  appendMessage("user", query);
  const pending = appendMessage("agent", "Агент анализирует задачу…", true);
  element<HTMLTextAreaElement>("chat-query").value = "";
  element<HTMLButtonElement>("cancel-chat").disabled = false;
  try {
    const result = await api<Payload>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        query,
        thread_id: currentThread,
      auto_context: checked("auto-context"),
      mode: value("chat-mode"),
      allow_write: checked("chat-write"),
      }),
    });
    activeChatTask = text(result.task_id);
    streamTask(activeChatTask, (name, data) => {
      if (name === "message") {
        pending.textContent = text(data.text);
        pending.parentElement?.classList.remove("pending");
      }
      if (name === "failed") {
        pending.textContent = text(data.message);
        pending.parentElement?.classList.remove("pending");
      }
      if (name === "cancelled") {
        pending.textContent = "Операция отменена.";
        pending.parentElement?.classList.remove("pending");
      }
      if (["completed", "cancelled", "failed"].includes(name)) {
        activeChatTask = "";
        element<HTMLButtonElement>("cancel-chat").disabled = true;
        void refreshThreads();
      }
    });
  } catch (error) {
    pending.textContent = error instanceof Error ? error.message : "Ошибка чата";
    pending.parentElement?.classList.remove("pending");
    activeChatTask = "";
    element<HTMLButtonElement>("cancel-chat").disabled = true;
  }
}

async function refreshAudits(): Promise<void> {
  const rows = element<HTMLTableSectionElement>("audit-rows");
  rows.replaceChildren();
  const result = await api<{ items: Payload[]; request_id: string }>(
    "/api/jobs",
  );
  for (const item of result.items) {
    const row = document.createElement("tr");
    const updated = new Date(Number(item.updated_at) * 1_000).toLocaleString();
    for (const entry of [item.id, item.mode, item.status, updated]) {
      const cell = document.createElement("td");
      cell.textContent = text(entry);
      row.append(cell);
    }
    rows.append(row);
  }
}

function normalizeWorkspacePath(path: string): string {
  const normalized = path.trim().replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalized || normalized === "/" || normalized === "/workspace") {
    return "/workspace";
  }
  if (normalized.startsWith("/workspace/")) return normalized;
  return `/workspace/${normalized.replace(/^\/+/, "")}`;
}

function fileEndpoint(path: string): string {
  const relative = normalizeWorkspacePath(path).replace(/^\/workspace\/?/, "");
  return (
    "/api/files/" +
    relative
      .split("/")
      .filter(Boolean)
      .map((part) => encodeURIComponent(part))
      .join("/")
  );
}

function updateDirectoryControls(): void {
  element<HTMLButtonElement>("files-back").disabled = !directoryHistory.length;
  element<HTMLButtonElement>("files-up").disabled = currentDirectory === "/workspace";
}

async function loadDirectory(
  requestedPath: string,
  addToHistory = true,
): Promise<void> {
  const path = normalizeWorkspacePath(requestedPath);
  const openButton = element<HTMLButtonElement>("open-directory");
  openButton.disabled = true;
  setOperationStatus("files-status", `Открываю ${path}…`);
  try {
    const result = await api<{ items: Payload[]; request_id: string }>(
      `/api/files?path=${encodeURIComponent(path)}&limit=500`,
    );
    if (addToHistory && path !== currentDirectory) {
      directoryHistory.push(currentDirectory);
      if (directoryHistory.length > 100) directoryHistory.shift();
    }
    currentDirectory = path;
    element<HTMLInputElement>("files-path").value = path;
    updateDirectoryControls();
    const output = element("files-output");
    output.replaceChildren();
    if (!result.items.length) {
      output.append(
        card("Пустой каталог", "В этом каталоге нет доступных файлов."),
      );
    }
    for (const item of result.items) {
      const itemPath = text(item.path);
      const isDirectory = item.type === "directory";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "file-row";
      const icon = document.createElement("span");
      icon.className = "file-kind";
      icon.textContent = isDirectory ? "▱" : "≡";
      const name = document.createElement("span");
      name.className = "file-path";
      name.textContent = text(item.name);
      const meta = document.createElement("small");
      meta.textContent = isDirectory ? "Каталог" : `${text(item.size)} байт`;
      button.append(icon, name, meta);
      button.addEventListener("click", () => {
        const action = isDirectory ? loadDirectory(itemPath) : openFile(itemPath);
        void action.catch((error: Error) => showToast(error.message));
      });
      output.append(button);
    }
    setOperationStatus(
      "files-status",
      `Открыт ${path} · объектов: ${result.items.length}`,
      "success",
    );
  } catch (error) {
    setOperationStatus(
      "files-status",
      error instanceof Error ? error.message : "Ошибка открытия каталога",
      "error",
    );
    throw error;
  } finally {
    openButton.disabled = false;
  }
}

async function openFile(path: string): Promise<void> {
  const result = await api<Payload>(`${fileEndpoint(path)}?limit=1000`);
  currentFilePath = text(result.path);
  currentFileSha = text(result.sha256);
  element("file-editor").hidden = false;
  element("file-name").textContent = currentFilePath;
  const truncated = result.next_offset !== null;
  element("file-meta").textContent = truncated
    ? `${text(result.total_lines)} строк · показан фрагмент только для чтения`
    : `${text(result.total_lines)} строк · SHA-256 ${currentFileSha.slice(0, 12)}…`;
  element<HTMLTextAreaElement>("file-content").value = text(result.content);
  element<HTMLTextAreaElement>("file-content").readOnly = truncated;
  element<HTMLButtonElement>("save-file").disabled = truncated;
}

async function saveFile(): Promise<void> {
  if (!currentFilePath || !currentFileSha) return;
  try {
    const result = await api<Payload>(fileEndpoint(currentFilePath), {
      method: "PUT",
      body: JSON.stringify({
        content: value("file-content"),
        expected_sha256: currentFileSha,
      }),
    });
    currentFileSha = text(result.sha256);
    element("file-meta").textContent =
      `Сохранено · SHA-256 ${currentFileSha.slice(0, 12)}…`;
    showToast("Файл сохранён");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка сохранения");
  }
}

async function saveProviderPriority(): Promise<void> {
  await api("/api/providers/priority", {
    method: "PUT",
    body: JSON.stringify({ providers: activeProviders }),
  });
  await refreshProviders();
}

function providerStatus(provider: string, message: string, ok: boolean): void {
  const status = document.getElementById(`provider-status-${provider}`);
  if (!status) return;
  status.textContent = message;
  status.className = `provider-status ${ok ? "success" : "error"}`;
}

async function checkProvider(provider: string): Promise<void> {
  const catalogItem = providerCatalog.find(
    (item) => text(item.provider) === provider,
  );
  const local = Boolean(catalogItem?.local);
  if (
    !local &&
    !window.confirm(
      `Проверить ${provider} реальным API-запросом? ` +
        "Удалённый провайдер может взимать плату.",
    )
  ) {
    return;
  }
  providerStatus(
    provider,
    local ? "Локальная проверка · без платы за API…" : "Проверка…",
    true,
  );
  try {
    const result = await api<Payload>(
      `/api/providers/${encodeURIComponent(provider)}/doctor`,
      { method: "POST", body: JSON.stringify({ live: true }) },
    );
    streamTask(text(result.task_id), (name, data) => {
      if (name === "result") {
        const checkedResult = data.result as Payload;
        void refreshProviders().then(() => {
          providerStatus(
            provider,
            `Доступен · ${text(checkedResult.model)} · ответ ${text(checkedResult.response)}`,
            true,
          );
        });
      }
      if (name === "failed") providerStatus(provider, text(data.message), false);
    });
  } catch (error) {
    providerStatus(
      provider,
      error instanceof Error ? error.message : "Ошибка проверки",
      false,
    );
  }
}

async function createCustomProvider(): Promise<void> {
  const result = await api<Payload>("/api/providers", {
    method: "POST",
    body: JSON.stringify({
      name: value("custom-provider-name").trim().toLowerCase(),
      model: value("custom-provider-model").trim(),
      base_url: value("custom-provider-url").trim(),
    }),
  });
  const provider = result.provider as Payload;
  const providerName = text(provider.provider);
  await refreshProviders();
  if (providerName && !activeProviders.includes(providerName)) {
    activeProviders.push(providerName);
    await saveProviderPriority();
  }
  element<HTMLFormElement>("custom-provider-form").reset();
  showToast(`Провайдер ${providerName} добавлен`);
}

function renderProviders(): void {
  const output = element("providers-output");
  output.replaceChildren();
  for (const [index, providerName] of activeProviders.entries()) {
    const item =
      providerCatalog.find((candidate) => text(candidate.provider) === providerName) ||
      {};
    const row = document.createElement("article");
    row.className = "provider-row";
    const priority = document.createElement("span");
    priority.className = "provider-priority";
    priority.textContent = String(index + 1);
    const identity = document.createElement("div");
    identity.className = "provider-details";
    const heading = document.createElement("strong");
    heading.textContent = providerName;
    const model = document.createElement("small");
    model.textContent = `${text(item.model)} · ${
      item.local ? "локально, без платы API" : "удалённо"
    }`;
    const endpoint = document.createElement("div");
    endpoint.className = "provider-details";
    const url = document.createElement("small");
    url.textContent = text(item.base_url);
    const status = document.createElement("small");
    status.id = `provider-status-${providerName}`;
    status.className = "provider-status";
    status.textContent = "Не проверен";
    identity.append(heading, model);
    endpoint.append(url, status);
    const actions = document.createElement("div");
    actions.className = "provider-actions";
    const actionDefinitions: Array<[string, string, () => void, boolean]> = [
      ["↑", "Повысить приоритет", () => moveProvider(index, -1), index === 0],
      [
        "↓",
        "Понизить приоритет",
        () => moveProvider(index, 1),
        index === activeProviders.length - 1,
      ],
      ["Проверить", "Live-проверка", () => void checkProvider(providerName), false],
      [
        "Убрать",
        "Убрать из цепочки",
        () => removeProvider(index),
        activeProviders.length === 1,
      ],
    ];
    for (const [caption, title, handler, disabled] of actionDefinitions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = caption;
      button.title = title;
      button.disabled = disabled;
      button.addEventListener("click", handler);
      actions.append(button);
    }
    row.append(priority, identity, endpoint, actions);
    output.append(row);
  }

  const select = element<HTMLSelectElement>("provider-select");
  select.replaceChildren();
  const available = providerCatalog.filter(
    (item) =>
      Boolean(item.configured) && !activeProviders.includes(text(item.provider)),
  );
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = available.length
    ? "Выберите провайдера"
    : "Нет других настроенных провайдеров";
  select.append(placeholder);
  for (const item of available) {
    const option = document.createElement("option");
    option.value = text(item.provider);
    option.textContent = `${text(item.provider)} · ${text(item.model)}`;
    select.append(option);
  }
  element<HTMLButtonElement>("add-provider").disabled = !available.length;
}

function moveProvider(index: number, direction: number): void {
  const target = index + direction;
  if (target < 0 || target >= activeProviders.length) return;
  [activeProviders[index], activeProviders[target]] = [
    activeProviders[target],
    activeProviders[index],
  ];
  void saveProviderPriority().catch((error: Error) => {
    showToast(error.message);
    void refreshProviders();
  });
}

function removeProvider(index: number): void {
  if (activeProviders.length === 1) return;
  activeProviders.splice(index, 1);
  void saveProviderPriority().catch((error: Error) => {
    showToast(error.message);
    void refreshProviders();
  });
}

async function refreshProviders(): Promise<void> {
  const result = await api<{
    items: Payload[];
    active: Json[];
    request_id: string;
  }>("/api/providers");
  providerCatalog = result.items;
  activeProviders = result.active.map((item) => text(item));
  const primary = providerCatalog.find(
    (item) => text(item.provider) === activeProviders[0],
  );
  if (primary) {
    element("runtime-badge").textContent =
      `${text(primary.provider)}/${text(primary.model)}`;
  }
  renderProviders();
}

async function loadSettings(): Promise<void> {
  const result = await api<{ items: Payload[]; request_id: string }>(
    "/api/settings",
  );
  const output = element("settings-output");
  output.replaceChildren();
  for (const item of result.items) {
    const row = document.createElement("label");
    row.className = "setting-row";
    const name = document.createElement("span");
    name.className = "setting-name";
    const label = document.createElement("strong");
    label.textContent = text(item.label);
    const environment = document.createElement("code");
    environment.textContent = text(item.environment);
    name.append(label, environment);
    const input = document.createElement("input");
    input.type = "number";
    input.value = text(item.value);
    input.min = text(item.minimum);
    input.max = text(item.maximum);
    input.dataset.setting = text(item.name);
    const comment = document.createElement("span");
    comment.className = "setting-comment";
    comment.textContent = text(item.comment);
    row.append(name, input, comment);
    output.append(row);
  }
}

async function bootstrap(): Promise<void> {
  const runtime = await api<Payload>("/api/runtime");
  csrfToken = text(runtime.csrf_token);
  element("runtime-badge").textContent =
    `${text(runtime.provider)}/${text(runtime.model)}`;
  element("workspace-badge").textContent = text(runtime.workspace);
  const metrics = element("overview-grid");
  metrics.replaceChildren();
  for (const [name, metricValue] of [
    ["Версия / Version", runtime.version],
    ["Аудит по умолчанию", runtime.audit_mode_default],
    ["Активные задачи", runtime.active_tasks],
    ["Провайдеров в цепочке", (runtime.provider_priority as Json[]).length],
  ] as Array<[string, Json]>) {
    const node = document.createElement("div");
    node.className = "metric";
    const strong = document.createElement("strong");
    strong.textContent = text(metricValue);
    node.append(strong, document.createTextNode(name));
    metrics.append(node);
  }
  const health = await api<Payload>("/api/health");
  element("health").textContent = JSON.stringify(health, null, 2);
  await Promise.all([
    refreshAudits(),
    refreshThreads(),
    refreshProviders(),
    loadSettings(),
    loadDirectory("/workspace", false),
  ]);
  const requestedPanel = window.location.hash.slice(1);
  if (pageTitles[requestedPanel]) navigate(requestedPanel);
}

document.querySelectorAll<HTMLButtonElement>("nav button").forEach((button) => {
  button.addEventListener("click", () => navigate(text(button.dataset.panel)));
});

document.querySelectorAll<HTMLButtonElement>(".mode-card").forEach((button) => {
  button.addEventListener("click", () => {
    element<HTMLSelectElement>("chat-mode").value = text(button.dataset.mode);
    navigate("chat");
    element<HTMLTextAreaElement>("chat-query").focus();
  });
});

element("new-thread").addEventListener("click", () => {
  const threadId = `web-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`;
  void loadThread(threadId).catch((error: Error) => showToast(error.message));
});

element<HTMLFormElement>("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void sendChatMessage();
});

const chatQuery = element<HTMLTextAreaElement>("chat-query");
chatQuery.addEventListener("input", () => {
  chatQuery.style.height = "auto";
  chatQuery.style.height = `${Math.min(chatQuery.scrollHeight, 220)}px`;
});
chatQuery.addEventListener("keydown", (event) => {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing &&
    checked("enter-send")
  ) {
    event.preventDefault();
    void sendChatMessage();
  }
});

const enterSend = element<HTMLInputElement>("enter-send");
enterSend.checked = window.localStorage.getItem("dca_enter_send") === "true";
enterSend.addEventListener("change", () => {
  window.localStorage.setItem("dca_enter_send", String(enterSend.checked));
});

element("cancel-chat").addEventListener("click", async () => {
  if (!activeChatTask) return;
  try {
    await api(`/api/chat/${encodeURIComponent(activeChatTask)}/cancel`, {
      method: "POST",
    });
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка отмены");
  }
});

element<HTMLFormElement>("context-form").addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();
    const output = element("context-output");
    output.replaceChildren();
    try {
      const query = encodeURIComponent(value("context-query"));
      const limit = Number(value("context-limit"));
      const result = await api<{ items: Payload[]; request_id: string }>(
        `/api/context/search?query=${query}&limit=${limit}`,
      );
      for (const item of result.items) {
        output.append(
          card(
            `${text(item.source)} · фрагмент ${text(item.chunk_index)}`,
            text(item.content),
          ),
        );
      }
      if (!result.items.length) {
        output.append(card("Нет результатов", "Попробуйте более точный запрос."));
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Ошибка поиска");
    }
  },
);

element("index-workspace").addEventListener("click", async () => {
  const button = element<HTMLButtonElement>("index-workspace");
  button.disabled = true;
  setOperationStatus("index-status", "Индексация запущена…");
  try {
    const result = await api<Payload>("/api/context/index", {
      method: "POST",
      body: JSON.stringify({ path: value("index-path") }),
    });
    streamTask(text(result.task_id), (name, data) => {
      if (name === "result") {
        const report = data.result as Payload;
        setOperationStatus(
          "index-status",
          `Готово: новых ${text(report.files_indexed)}, без изменений ${text(report.files_unchanged)}, пропущено ${text(report.files_skipped)}, фрагментов ${text(report.chunks_written)}.`,
          "success",
        );
      }
      if (name === "failed") {
        setOperationStatus("index-status", text(data.message), "error");
      }
      if (["completed", "cancelled", "failed"].includes(name)) {
        button.disabled = false;
      }
    });
  } catch (error) {
    button.disabled = false;
    setOperationStatus(
      "index-status",
      error instanceof Error ? error.message : "Ошибка индексации",
      "error",
    );
  }
});

element<HTMLFormElement>("audit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api<Payload>("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        objective: value("audit-objective"),
        thread_id: value("audit-thread"),
        allow_write: checked("audit-write"),
        include_patterns: value("audit-include")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        exclude_patterns: value("audit-exclude")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      }),
    });
    setOperationStatus(
      "audit-progress",
      `Автопилот запущен. Job ID: ${text(result.job_id)}`,
    );
    streamTask(text(result.task_id), (name, data) => {
      if (["job_progress", "job_replanned", "job_verification"].includes(name)) {
        const audit =
          data.audit && typeof data.audit === "object"
            ? (data.audit as Payload)
            : {};
        const prefix =
          name === "job_replanned"
            ? "Перепланирование"
            : name === "job_verification"
              ? "Проверка"
              : "Выполнение";
        setOperationStatus(
          "audit-progress",
          `${prefix}: фаза ${text(data.phase)}, файлы ${text(audit.reviewed)}/${text(audit.total)}, ожидают ${text(audit.pending)}, units ${text(data.completed_units)}/${text(data.attempts)}, replans ${text(data.replans)}.`,
        );
      }
      if (name === "result") {
        setOperationStatus("audit-progress", text(data.result), "success");
      }
      if (name === "completed") void refreshAudits();
      if (name === "failed") {
        setOperationStatus("audit-progress", text(data.message), "error");
      }
    });
  } catch (error) {
    setOperationStatus(
      "audit-progress",
      error instanceof Error ? error.message : "Ошибка автопилота",
      "error",
    );
  }
});

element("refresh-audits").addEventListener("click", () => {
  void refreshAudits().catch((error: Error) => showToast(error.message));
});

element<HTMLFormElement>("files-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void loadDirectory(value("files-path")).catch((error: Error) =>
    showToast(error.message),
  );
});

element("files-back").addEventListener("click", () => {
  const previous = directoryHistory.pop();
  if (!previous) return;
  void loadDirectory(previous, false).catch((error: Error) => {
    directoryHistory.push(previous);
    updateDirectoryControls();
    showToast(error.message);
  });
});

element("files-up").addEventListener("click", () => {
  const current = currentDirectory;
  const segments = current.replace(/^\/workspace\/?/, "").split("/").filter(Boolean);
  segments.pop();
  const parent = segments.length ? `/workspace/${segments.join("/")}` : "/workspace";
  void loadDirectory(parent).catch((error: Error) => showToast(error.message));
});

element("close-file").addEventListener("click", () => {
  element("file-editor").hidden = true;
  currentFilePath = "";
  currentFileSha = "";
});
element("reload-file").addEventListener("click", () => {
  if (currentFilePath) {
    void openFile(currentFilePath).catch((error: Error) => showToast(error.message));
  }
});
element("save-file").addEventListener("click", () => void saveFile());

element("load-providers").addEventListener("click", () => {
  void refreshProviders().catch((error: Error) => showToast(error.message));
});
element("add-provider").addEventListener("click", () => {
  const provider = value("provider-select");
  if (!provider || activeProviders.includes(provider)) return;
  activeProviders.push(provider);
  void saveProviderPriority().catch((error: Error) => {
    showToast(error.message);
    void refreshProviders();
  });
});
element<HTMLFormElement>("custom-provider-form").addEventListener(
  "submit",
  (event) => {
    event.preventDefault();
    void createCustomProvider().catch((error: Error) => showToast(error.message));
  },
);

element<HTMLFormElement>("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const values: Record<string, number> = {};
  document
    .querySelectorAll<HTMLInputElement>("[data-setting]")
    .forEach((input) => {
      values[text(input.dataset.setting)] = Number(input.value);
    });
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    showToast("Настройки применены к текущему Web-процессу");
    await loadSettings();
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка настроек");
  }
});

void bootstrap().catch((error: Error) => {
  element("runtime-badge").textContent = "Ошибка";
  showToast(error.message);
});
