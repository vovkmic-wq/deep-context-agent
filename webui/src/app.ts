"use strict";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Payload = Record<string, Json>;

const pageTitles: Record<string, string> = {
  overview: "Обзор",
  chat: "Чат",
  context: "Контекст",
  files: "Файлы",
  providers: "Провайдеры",
  settings: "Настройки",
};

let csrfToken = "";
const activeChatTasks = new Set<string>();
let activeChatJob = "";
let currentThread = "web";
let archivedContextTokens = 0;
let contextTokenLimit = 80_000;
let currentFilePath = "";
let currentFileSha = "";
let currentDirectory = "/workspace";
const directoryHistory: string[] = [];
let providerCatalog: Payload[] = [];
let activeProviders: string[] = [];
let modelCatalogRequest = 0;
let providerRenderGeneration = 0;
let indexCursor = "";
let indexedPath = "";

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

function updateCancelButton(): void {
  element<HTMLButtonElement>("cancel-chat").disabled = activeChatTasks.size === 0;
}

function updateContextMeter(extraCharacters = 0): void {
  const estimated = archivedContextTokens + Math.ceil(extraCharacters / 4);
  const percent = Math.min(100, (estimated / Math.max(1, contextTokenLimit)) * 100);
  const meter = element<HTMLElement>("context-meter");
  meter.style.setProperty("--context-angle", `${percent * 3.6}deg`);
  meter.setAttribute("aria-valuenow", percent.toFixed(1));
  meter.classList.toggle("warning", percent >= 75 && percent < 90);
  meter.classList.toggle("critical", percent >= 90);
  element("context-meter-label").textContent = `${Math.round(percent)}%`;
  meter.title = `${Math.round(estimated).toLocaleString()} / ${contextTokenLimit.toLocaleString()} токенов (оценка). При приближении к пределу Deep Agents автоматически суммаризует старую переписку; полная SQLite-история сохраняется.`;
}

function applyContextUsage(data: Payload | undefined): void {
  if (data) {
    archivedContextTokens = Number(data.estimated_tokens || 0);
    contextTokenLimit = Number(data.limit_tokens || 80_000);
  }
  updateContextMeter(value("chat-query").length);
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
    "execution",
    "message",
    "audit_progress",
    "job_progress",
    "job_replanned",
    "job_verification",
    "job_heartbeat",
    "job_deadline",
    "scan_progress",
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
  element("main").classList.toggle("chat-view", panelId === "chat");
  element("page-title").textContent = pageTitles[panelId] || panelId;
  window.history.replaceState(null, "", `#${panelId}`);
  if (panelId === "chat") window.scrollTo({ top: 0, behavior: "auto" });
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
  if (!["human", "user"].includes(role) || !content.startsWith("Режим работы:")) {
    return content;
  }
  const separator = content.indexOf("\n\n");
  return separator >= 0 ? content.slice(separator + 2) : content;
}

async function loadThread(threadId: string): Promise<void> {
  currentThread = threadId;
  element("current-thread-label").textContent = threadId;
  const output = element("chat-output");
  output.replaceChildren();
  const result = await api<{
    items: Payload[];
    context_usage: Payload;
    request_id: string;
  }>(
    `/api/threads/${encodeURIComponent(threadId)}/messages?limit=200`,
  );
  applyContextUsage(result.context_usage);
  for (const item of result.items) {
    const role = text(item.role);
    appendMessage(
      ["human", "user"].includes(role) ? "user" : "agent",
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
  await Promise.all([
    refreshThreads(),
    refreshChatJobs(),
    loadThreadModelPreference(threadId),
  ]);
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
  const workMode = value("chat-mode");
  if (!query) return;
  if (activeChatTasks.size && workMode !== "multitask") {
    showToast(
      "Дождитесь активной задачи либо выберите Multitask для параллельного запуска.",
    );
    return;
  }
  appendMessage("user", query);
  const pending = appendMessage(
    "agent",
    workMode === "multitask"
      ? "Независимый worker запускается…"
      : "Агент анализирует задачу…",
    true,
  );
  element<HTMLTextAreaElement>("chat-query").value = "";
  updateContextMeter();
  let taskId = "";
  try {
    const result = await api<Payload>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        query,
        thread_id: currentThread,
        auto_context: checked("auto-context"),
        mode: workMode,
        allow_write: checked("chat-write"),
        execution_mode: value("chat-execution"),
        provider: value("chat-provider"),
        model: value("chat-model"),
      }),
    });
    taskId = text(result.task_id);
    activeChatTasks.add(taskId);
    updateCancelButton();
    const initialRouting =
      result.routing && typeof result.routing === "object"
        ? (result.routing as Payload)
        : {};
    if (Object.keys(initialRouting).length) {
      const summary = formatRoutingDecision(initialRouting);
      pending.textContent = summary;
      const status = element("chat-job-status");
      status.hidden = false;
      setOperationStatus("chat-job-status", summary);
    }
    streamTask(taskId, (name, data) => {
      if (name === "execution") {
        const routing =
          data.routing && typeof data.routing === "object"
            ? (data.routing as Payload)
            : {};
        const summary = formatRoutingDecision(routing);
        pending.textContent =
          data.mode === "autopilot"
            ? `${summary} Подготавливается сохраняемая рабочая единица…`
            : summary;
        const status = element("chat-job-status");
        status.hidden = false;
        setOperationStatus("chat-job-status", summary);
      }
      if (
        [
          "job_progress",
          "job_replanned",
          "job_verification",
          "job_heartbeat",
          "job_deadline",
        ].includes(name)
      ) {
        activeChatJob = text(data.job_id) || activeChatJob;
        const progress = formatJobProgress(data, name);
        pending.textContent = progress;
        const status = element("chat-job-status");
        status.hidden = false;
        setOperationStatus("chat-job-status", progress);
      }
      if (name === "message") {
        pending.textContent = text(data.text);
        pending.parentElement?.classList.remove("pending");
        activeChatJob = text(data.job_id) || activeChatJob;
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
        activeChatTasks.delete(taskId);
        updateCancelButton();
        void Promise.all([refreshThreads(), refreshChatJobs()]);
        void api<{
          items: Payload[];
          context_usage: Payload;
          request_id: string;
        }>(`/api/threads/${encodeURIComponent(currentThread)}/messages?limit=1`)
          .then((usage) => applyContextUsage(usage.context_usage))
          .catch(() => undefined);
      }
    });
  } catch (error) {
    pending.textContent = error instanceof Error ? error.message : "Ошибка чата";
    pending.parentElement?.classList.remove("pending");
    if (taskId) activeChatTasks.delete(taskId);
    updateCancelButton();
  }
}

const workflowLabels: Record<string, string> = {
  answer: "Ответ / Answer",
  "log-analysis": "Анализ журнала / Log analysis",
  "targeted-review": "Проверка файла / Targeted review",
  "targeted-change": "Изменение файла / Targeted change",
  "project-audit": "Аудит проекта / Project audit",
  "project-change": "Изменение проекта / Project change",
  "project-test": "Тестирование проекта / Project testing",
  plan: "Планирование / Planning",
  debug: "Отладка / Debugging",
};

function formatRoutingDecision(routing: Payload): string {
  const workflow = text(routing.workflow) || "answer";
  const execution =
    text(routing.execution) === "persistent"
      ? "длительная задача"
      : "один ход";
  const scope = text(routing.scope) || "message";
  return `Маршрут: ${workflowLabels[workflow] || workflow} · ${execution} · область ${scope}.`;
}

function formatJobProgress(data: Payload, eventName: string): string {
  const audit =
    data.audit && typeof data.audit === "object" ? (data.audit as Payload) : {};
  const prefix =
    eventName === "job_replanned"
      ? "Autopilot перепланирует"
      : eventName === "job_verification"
        ? "Autopilot проверяет"
        : eventName === "job_heartbeat"
          ? "Autopilot: модель работает"
          : eventName === "job_deadline"
            ? "Autopilot: soft deadline, завершается текущая единица"
            : "Autopilot выполняет";
  const reviewed = text(audit.reviewed);
  const total = text(audit.total);
  const pending = text(audit.pending);
  const heartbeatSeconds = Number(data.last_heartbeat_at || 0);
  const heartbeat = heartbeatSeconds
    ? new Date(heartbeatSeconds * 1000).toLocaleTimeString()
    : "—";
  const workflow = text(data.workflow) || "project-audit";
  const fileProgress = total
    ? `, файлы ${reviewed || "0"}/${total}, ожидают ${pending || "0"}`
    : "";
  return `${prefix}: ${workflowLabels[workflow] || workflow}, фаза ${text(data.phase)}, generation ${text(data.lease_generation)}, связь ${heartbeat}${fileProgress}, units ${text(data.completed_units)}/${text(data.attempts)}, interrupted ${text(data.interrupted_units)}, replans ${text(data.replans)}.`;
}

function showJobSummary(data: Payload): void {
  const job =
    data.job && typeof data.job === "object" ? (data.job as Payload) : data;
  activeChatJob = text(job.id) || activeChatJob;
  const progress =
    job.progress && typeof job.progress === "object"
      ? (job.progress as Payload)
      : {};
  const status = element("chat-job-status");
  status.hidden = false;
  setOperationStatus(
    "chat-job-status",
    `Autopilot ${activeChatJob}: ${text(job.status)} · фаза ${text(job.phase)} · generation ${text(progress.lease_generation)} · units ${text(progress.completed_units)}/${text(progress.attempts)} · interrupted ${text(progress.interrupted_units)} · replans ${text(progress.replans)}.`,
    job.status === "complete" ? "success" : "normal",
  );
}

async function refreshChatJobs(): Promise<void> {
  const output = element("chat-job-list");
  output.replaceChildren();
  const result = await api<{ items: Payload[]; request_id: string }>(
    "/api/jobs",
  );
  const jobs = result.items
    .filter((item) => text(item.thread_id) === currentThread)
    .slice(0, 20);
  if (!jobs.length) {
    const empty = document.createElement("small");
    empty.textContent = "Для этого чата задач пока нет.";
    output.append(empty);
    return;
  }
  for (const item of jobs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "thread-item";
    button.textContent = `${text(item.status)} · ${text(item.id).slice(0, 8)}`;
    button.title = text(item.objective);
    button.addEventListener("click", () => {
      const jobId = text(item.id);
      void api<Payload>(`/api/jobs/${encodeURIComponent(jobId)}`)
        .then(showJobSummary)
        .catch((error: Error) => showToast(error.message));
    });
    output.append(button);
    if (text(item.id) === activeChatJob) {
      button.classList.add("active");
    }
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

function setProviderCatalogModel(provider: string, model: string): void {
  const item = providerCatalog.find(
    (candidate) => text(candidate.provider) === provider,
  );
  if (item) item.model = model;
  document
    .querySelectorAll<HTMLSelectElement>("[data-provider-model]")
    .forEach((select) => {
      if (select.dataset.providerModel === provider) select.value = model;
    });
}

async function loadProviderModelOptions(
  provider: string,
  select: HTMLSelectElement,
  configuredModel: string,
  generation: number,
): Promise<void> {
  try {
    const result = await api<{
      models: Json[];
      partial: boolean;
      message?: string;
      request_id: string;
    }>(`/api/providers/${encodeURIComponent(provider)}/models`);
    if (generation !== providerRenderGeneration || !select.isConnected) return;
    const models = result.models.map((model) => text(model));
    if (configuredModel && !models.includes(configuredModel)) {
      models.unshift(configuredModel);
    }
    select.replaceChildren();
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      select.append(option);
    }
    select.value = configuredModel || models[0] || "";
    select.disabled = models.length === 0;
    select.title = result.partial
      ? `Частичный список: ${text(result.message)}`
      : `Доступно моделей: ${models.length}`;
  } catch (error) {
    if (generation !== providerRenderGeneration || !select.isConnected) return;
    select.disabled = !configuredModel;
    select.title =
      `Каталог моделей недоступен: ${error instanceof Error ? error.message : "ошибка"}`;
    providerStatus(provider, select.title, false);
  }
}

async function saveProviderModel(
  provider: string,
  model: string,
  select: HTMLSelectElement,
): Promise<void> {
  select.disabled = true;
  providerStatus(provider, "Сохраняю модель…", true);
  try {
    await api<Payload>(`/api/providers/${encodeURIComponent(provider)}/model`, {
      method: "PUT",
      body: JSON.stringify({ model }),
    });
    setProviderCatalogModel(provider, model);
    if (value("chat-provider") === provider) {
      await loadChatModels(provider, model);
      element<HTMLSelectElement>("chat-model").value = model;
      await persistThreadModelPreference(provider, model);
      element("runtime-badge").textContent = `${provider}/${model}`;
    }
    providerStatus(provider, `Модель ${model} сохранена`, true);
    showToast(`Для ${provider} выбрана модель ${model}`);
  } catch (error) {
    const configured = providerCatalog.find(
      (item) => text(item.provider) === provider,
    );
    select.value = text(configured?.model);
    providerStatus(
      provider,
      error instanceof Error ? error.message : "Ошибка выбора модели",
      false,
    );
  } finally {
    select.disabled = false;
  }
}

function renderProviders(): void {
  const output = element("providers-output");
  output.replaceChildren();
  const generation = ++providerRenderGeneration;
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
    const location = document.createElement("small");
    location.textContent = item.local ? "локально, без платы API" : "удалённо";
    const modelControl = document.createElement("label");
    modelControl.className = "provider-model-control";
    const modelCaption = document.createElement("span");
    modelCaption.textContent = "Модель / Model";
    const modelSelect = document.createElement("select");
    modelSelect.dataset.providerModel = providerName;
    modelSelect.setAttribute("aria-label", `Модель провайдера ${providerName}`);
    const configuredModel = text(item.model);
    if (configuredModel) {
      const configuredOption = document.createElement("option");
      configuredOption.value = configuredModel;
      configuredOption.textContent = configuredModel;
      modelSelect.append(configuredOption);
    }
    modelSelect.value = configuredModel;
    modelSelect.disabled = true;
    modelSelect.addEventListener("change", () => {
      void saveProviderModel(providerName, modelSelect.value, modelSelect);
    });
    modelControl.append(modelCaption, modelSelect);
    const endpoint = document.createElement("div");
    endpoint.className = "provider-details";
    const url = document.createElement("small");
    url.textContent = text(item.base_url);
    const status = document.createElement("small");
    status.id = `provider-status-${providerName}`;
    status.className = "provider-status";
    status.textContent = "Не проверен";
    identity.append(heading, location);
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
    row.append(priority, identity, modelControl, endpoint, actions);
    output.append(row);
    void loadProviderModelOptions(
      providerName,
      modelSelect,
      configuredModel,
      generation,
    );
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
  const previousProvider = value("chat-provider");
  const previousModel = value("chat-model");
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
  await populateChatProviders(previousProvider, previousModel);
}

async function populateChatProviders(
  preferredProvider = "",
  preferredModel = "",
): Promise<void> {
  const select = element<HTMLSelectElement>("chat-provider");
  const previous = preferredProvider || select.value;
  select.replaceChildren();
  const configured = activeProviders
    .map((provider) =>
      providerCatalog.find((item) => text(item.provider) === provider),
    )
    .filter((item): item is Payload => Boolean(item?.configured));
  for (const provider of configured) {
    const option = document.createElement("option");
    option.value = text(provider.provider);
    option.textContent = text(provider.provider);
    select.append(option);
  }
  const fallback = activeProviders[0] || text(configured[0]?.provider);
  select.value = configured.some((item) => text(item.provider) === previous)
    ? previous
    : fallback;
  select.disabled = !configured.length;
  const selectedModel = select.value === previous ? preferredModel : "";
  await loadChatModels(select.value, selectedModel);
}

async function loadChatModels(
  provider: string,
  preferred = "",
): Promise<void> {
  const requestNumber = ++modelCatalogRequest;
  const select = element<HTMLSelectElement>("chat-model");
  select.disabled = true;
  element("chat-model-status").textContent =
    `Загружаю модели ${provider || "провайдера"}…`;
  if (!provider) {
    select.replaceChildren();
    element("chat-model-status").textContent = "Нет настроенного провайдера.";
    return;
  }
  try {
    const result = await api<{
      models: Json[];
      partial: boolean;
      message?: string;
      request_id: string;
    }>(`/api/providers/${encodeURIComponent(provider)}/models`);
    if (requestNumber !== modelCatalogRequest) return;
    const previous = preferred || select.value;
    select.replaceChildren();
    for (const model of result.models) {
      const option = document.createElement("option");
      option.value = text(model);
      option.textContent = text(model);
      select.append(option);
    }
    if (result.models.some((model) => text(model) === previous)) {
      select.value = previous;
    }
    select.disabled = !result.models.length;
    element("chat-model-status").textContent = result.partial
      ? `Частичный список: ${text(result.message)} Текущую модель можно использовать.`
      : `Доступно моделей: ${result.models.length}. Выбор сохранится для этой задачи.`;
  } catch (error) {
    if (requestNumber !== modelCatalogRequest) return;
    const providerItem = providerCatalog.find(
      (item) => text(item.provider) === provider,
    );
    const fallbackModel = preferred || text(providerItem?.model);
    select.replaceChildren();
    if (fallbackModel) {
      const option = document.createElement("option");
      option.value = fallbackModel;
      option.textContent = fallbackModel;
      select.append(option);
    }
    select.disabled = !fallbackModel;
    element("chat-model-status").textContent =
      `Каталог моделей недоступен: ${error instanceof Error ? error.message : "ошибка"}. Используется настроенная модель.`;
  }
}

async function persistThreadModelPreference(
  provider: string,
  model: string,
): Promise<void> {
  await api<Payload>(
    `/api/threads/${encodeURIComponent(currentThread)}/model-preference`,
    {
      method: "PUT",
      body: JSON.stringify({ provider, model }),
    },
  );
  setProviderCatalogModel(provider, model);
}

async function saveThreadModelPreference(): Promise<void> {
  const provider = value("chat-provider");
  const model = value("chat-model");
  if (!provider || !model) return;
  await persistThreadModelPreference(provider, model);
  element("runtime-badge").textContent = `${provider}/${model}`;
  element("chat-model-status").textContent =
    `Выбрано ${provider}/${model}. Следующий запрос получит неизменяемый снимок этой модели.`;
}

async function applyModelPreset(preset: string): Promise<void> {
  const configured = providerCatalog.filter((item) => Boolean(item.configured));
  if (!configured.length) return;
  const active = activeProviders[0] || text(configured[0]?.provider);
  const preferredProviders: Record<string, string[]> = {
    auto: [active],
    quality: ["openai", "zhipu", active],
    balanced: [active, "zhipu", "openai"],
    economy: [active, "openai", "zhipu"],
    local: ["lmstudio"],
  };
  const configuredNames = new Set(configured.map((item) => text(item.provider)));
  const provider = (preferredProviders[preset] || [active]).find((name) =>
    configuredNames.has(name),
  );
  if (!provider) {
    element("chat-model-status").textContent =
      "Для выбранного профиля нет настроенного провайдера.";
    return;
  }
  element<HTMLSelectElement>("chat-provider").value = provider;
  await loadChatModels(provider);
  const modelSelect = element<HTMLSelectElement>("chat-model");
  const patterns: Record<string, string[]> = {
    quality: ["gpt-5.6-sol", "glm-5.3", "pro", "max", "reason"],
    balanced: ["terra", "plus", "turbo", "glm-5.3"],
    economy: ["nano", "mini", "flash", "air", "lite"],
  };
  const candidates = Array.from(modelSelect.options).map((option) => option.value);
  const matchedPattern = (patterns[preset] || []).find((pattern) =>
    candidates.some((candidate) => candidate.toLowerCase().includes(pattern)),
  );
  if (matchedPattern) {
    const matching = candidates.find((candidate) =>
      candidate.toLowerCase().includes(matchedPattern),
    );
    if (matching) modelSelect.value = matching;
  }
  await saveThreadModelPreference();
}

async function loadThreadModelPreference(threadId: string): Promise<void> {
  const result = await api<{ preference: Payload; request_id: string }>(
    `/api/threads/${encodeURIComponent(threadId)}/model-preference`,
  );
  if (threadId !== currentThread) return;
  const provider = text(result.preference.provider);
  const model = text(result.preference.model);
  const selectedProvider = activeProviders.includes(provider)
    ? provider
    : activeProviders[0];
  const selectedModel =
    selectedProvider === provider
      ? model
      : text(
          providerCatalog.find(
            (item) => text(item.provider) === selectedProvider,
          )?.model,
        );
  await populateChatProviders(selectedProvider, selectedModel);
  if (selectedProvider && selectedModel) {
    await persistThreadModelPreference(selectedProvider, selectedModel);
    element("runtime-badge").textContent = `${selectedProvider}/${selectedModel}`;
  }
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
  const retrieval =
    runtime.retrieval && typeof runtime.retrieval === "object"
      ? (runtime.retrieval as Payload)
      : {};
  const metrics = element("overview-grid");
  metrics.replaceChildren();
  for (const [name, metricValue] of [
    ["Версия / Version", runtime.version],
    ["Аудит по умолчанию", runtime.audit_mode_default],
    ["Активные задачи", runtime.active_tasks],
    ["Провайдеров в цепочке", (runtime.provider_priority as Json[]).length],
    ["Память / Memory", retrieval.mode],
    ["Embedding", retrieval.embedding_model],
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
  await refreshProviders();
  await Promise.all([
    refreshChatJobs(),
    refreshThreads(),
    loadSettings(),
    loadDirectory("/workspace", false),
    loadThreadModelPreference(currentThread),
  ]);
  const requestedPanel = window.location.hash.slice(1);
  if (pageTitles[requestedPanel]) navigate(requestedPanel);
}

const modeHelp: Record<string, string> = {
  agent:
    "Agent выполняет задачу до результата всеми настроенными инструментами в пределах workspace и доверенных разрешений.",
  ask: "Ask работает только на чтение: изучает код и отвечает, не изменяя файлы.",
  plan: "Plan задаёт необходимые вопросы и готовит план. Для реализации одобренного плана переключитесь в Agent.",
  debug:
    "Debug проверяет гипотезу, добавляет только разрешённую обратимую диагностику и просит воспроизвести проблему.",
  multitask:
    "Multitask запускает до четырёх независимых workers. Для конкурентных правок используйте разные worktree.",
};

function applyChatMode(mode: string, resetDefaults = true): void {
  const write = element<HTMLInputElement>("chat-write");
  const execution = element<HTMLSelectElement>("chat-execution");
  const forcedReadOnly = ["ask", "plan"].includes(mode);
  const forcedSingleTurn = ["ask", "plan", "debug"].includes(mode);
  write.disabled = forcedReadOnly;
  if (resetDefaults) {
    write.checked = ["agent", "debug"].includes(mode);
    execution.value = forcedSingleTurn ? "single-turn" : "auto";
  }
  if (mode === "multitask" && !resetDefaults) write.checked = false;
  if (forcedReadOnly) write.checked = false;
  execution.disabled = forcedSingleTurn;
  if (forcedSingleTurn) execution.value = "single-turn";
  element("mode-help").textContent = modeHelp[mode] || modeHelp.agent;
  window.localStorage.setItem("dca_chat_mode", mode);
  window.localStorage.setItem("dca_chat_execution", execution.value);
}

document.querySelectorAll<HTMLButtonElement>("nav button").forEach((button) => {
  button.addEventListener("click", () => navigate(text(button.dataset.panel)));
});

document.querySelectorAll<HTMLButtonElement>(".mode-card").forEach((button) => {
  button.addEventListener("click", () => {
    const mode = text(button.dataset.mode);
    element<HTMLSelectElement>("chat-mode").value = mode;
    applyChatMode(mode);
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
  updateContextMeter(chatQuery.value.length);
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

const chatExecution = element<HTMLSelectElement>("chat-execution");
const storedExecution = window.localStorage.getItem("dca_chat_execution") || "auto";
chatExecution.value = ["auto", "autopilot", "single-turn"].includes(
  storedExecution,
)
  ? storedExecution
  : "auto";
chatExecution.addEventListener("change", () => {
  window.localStorage.setItem("dca_chat_execution", chatExecution.value);
});

const chatMode = element<HTMLSelectElement>("chat-mode");
const storedMode = window.localStorage.getItem("dca_chat_mode") || "agent";
chatMode.value = ["agent", "ask", "plan", "debug", "multitask"].includes(
  storedMode,
)
  ? storedMode
  : "agent";
applyChatMode(chatMode.value, false);
chatMode.addEventListener("change", () => applyChatMode(chatMode.value));

element<HTMLSelectElement>("chat-provider").addEventListener(
  "change",
  async () => {
    try {
      await loadChatModels(value("chat-provider"));
      await saveThreadModelPreference();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Ошибка выбора провайдера");
    }
  },
);

element<HTMLSelectElement>("chat-model").addEventListener("change", () => {
  void saveThreadModelPreference().catch((error: Error) =>
    showToast(error.message),
  );
});

element<HTMLSelectElement>("chat-model-preset").addEventListener(
  "change",
  () => {
    void applyModelPreset(value("chat-model-preset")).catch((error: Error) =>
      showToast(error.message),
    );
  },
);

element("cancel-chat").addEventListener("click", async () => {
  if (!activeChatTasks.size) return;
  try {
    await Promise.all(
      [...activeChatTasks].map((taskId) =>
        api(`/api/chat/${encodeURIComponent(taskId)}/cancel`, {
          method: "POST",
        }),
      ),
    );
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
  const requestedPath = normalizeWorkspacePath(value("index-path"));
  if (requestedPath !== indexedPath) indexCursor = "";
  indexedPath = requestedPath;
  button.disabled = true;
  setOperationStatus(
    "index-status",
    indexCursor ? "Продолжаю индексацию с сохранённого курсора…" : "Индексация запущена…",
  );
  try {
    const result = await api<Payload>("/api/context/index", {
      method: "POST",
      body: JSON.stringify({ path: requestedPath, cursor: indexCursor, page_size: 200 }),
    });
    streamTask(text(result.task_id), (name, data) => {
      if (name === "scan_progress") {
        setOperationStatus(
          "index-status",
          `Сканирование: просмотрено ${text(data.files_scanned) || "0"}, новых ${text(data.files_indexed) || "0"}, без изменений ${text(data.files_unchanged) || "0"}, пропущено ${text(data.files_skipped) || "0"}${data.partial ? " · частичный результат" : ""}.`,
        );
      }
      if (name === "result") {
        const report = data.result as Payload;
        indexCursor = text(report.next_cursor);
        const partial = Boolean(report.partial);
        button.textContent = partial ? "Продолжить индексацию" : "Индексировать";
        setOperationStatus(
          "index-status",
          `${partial ? "Частичный результат" : "Готово"}: просмотрено ${text(report.files_scanned)}, новых ${text(report.files_indexed)}, без изменений ${text(report.files_unchanged)}, пропущено ${text(report.files_skipped)}, фрагментов ${text(report.chunks_written)}.${partial ? " Нажмите «Продолжить индексацию»." : ""}`,
          partial ? "normal" : "success",
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
