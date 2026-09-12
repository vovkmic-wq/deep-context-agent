import assert from "node:assert/strict";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { createContext, runInContext } from "node:vm";
import { resolve } from "node:path";
import ts from "typescript";

const result = await build({
  entryPoints: [resolve(import.meta.dirname, "../src/task_actions.ts")],
  bundle: true, write: false, format: "iife", globalName: "Actions",
});
const context = createContext({});
runInContext(result.outputFiles[0].text, context);
const { availableTaskActions, taskActionEndpoint, isTerminalTaskStatus, TaskActionRequests } = context.Actions;

assert.deepEqual(Array.from(availableTaskActions(undefined)), []);
assert.deepEqual(Array.from(availableTaskActions("continue")), []);
assert.deepEqual(Array.from(availableTaskActions(["reconcile", "grant_write", "create_verification"])), ["reconcile", "create_verification"]);
assert.equal(taskActionEndpoint("task/with space", "continue"), "/api/tasks/task%2Fwith%20space/resume");
assert.equal(taskActionEndpoint("source", "continue_verification"), "/api/tasks/source/resume");
assert.equal(taskActionEndpoint("source", "reconcile"), "/api/tasks/source/reconcile");
assert.equal(taskActionEndpoint("source", "create_verification"), "/api/tasks/source/verification");
for (const state of ["completed", "partial", "blocked", "failed", "cancelled"]) {
  assert.equal(isTerminalTaskStatus(state), true);
}
for (const state of ["running", "reserved", "queued", undefined]) {
  assert.equal(isTerminalTaskStatus(state), false);
}

const stored = new Map();
const storage = { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) };
let keysGenerated = 0;
const newKey = () => `idempotency-${++keysGenerated}`;
let ledger = new TaskActionRequests(storage, newKey);
const signature = JSON.stringify(["task-a", "continue", { thread_id: "one", expected_revision: 3 }]);
const firstKey = ledger.begin(signature);
assert.equal(ledger.begin(signature), null, "Concurrent double click must not send a second request");
ledger.finish(signature);
assert.equal(ledger.begin(signature), firstKey, "A transport retry must retain the same key");
ledger.finish(signature);
ledger = new TaskActionRequests(storage, newKey);
assert.equal(ledger.begin(signature), firstKey, "Reload after uncertain delivery must retain the same key");
assert.notEqual(ledger.begin(JSON.stringify(["task-a", "continue", { thread_id: "one", expected_revision: 4 }])), firstKey);
assert.notEqual(ledger.begin(JSON.stringify(["task-a", "continue", { thread_id: "other", expected_revision: 3 }])), firstKey);
assert.notEqual(ledger.begin(JSON.stringify(["task-a", "create_verification", { project_root: "/workspace/other" }])), firstKey);
for (let index = 0; index < 140; index++) ledger.begin(`bounded-${index}`);
assert.equal(Object.keys(JSON.parse(stored.get("dca_explicit_action_keys_v1"))).length, 128);
const unavailable = { getItem: () => { throw new Error("disabled"); }, setItem: () => { throw new Error("full"); } };
ledger = new TaskActionRequests(unavailable, newKey);
assert.ok(ledger.begin("safe"));
assert.equal(ledger.begin("safe"), null);

const app = await readFile(resolve(import.meta.dirname, "../src/app.ts"), "utf8");
const html = await readFile(resolve(import.meta.dirname, "../../src/context_agent/static/index.html"), "utf8");
for (const id of ["chat-resume-explicit", "chat-verify-explicit", "chat-reconcile-explicit", "chat-create-verification", "chat-task-state", "chat-verification-root"]) {
  assert.ok(app.includes(`"${id}"`) && html.includes(`id="${id}"`), `Missing action control: ${id}`);
}
assert.ok(!app.includes('sendChatMessage("continue'), "Typed buttons must not route through natural language chat");
assert.ok(!app.includes("option.disabled = pending"), "A reconciliation-required task must remain selectable");
assert.ok(!app.includes('controls.push(["Продолжить", "resume"])'), "Legacy job resume must not bypass typed task action controls");
assert.ok(app.includes("taskActionEndpoint(savedTaskId, action)"));
assert.ok(app.includes("body.confirmed = true"));
assert.ok(app.includes("if (!executionId)"), "Reserved replies must not create empty SSE connections");

// Execute the actual UI action handler without booting unrelated app panels.
const parsedApp = ts.createSourceFile("app.ts", app, ts.ScriptTarget.Latest, true);
const handlerSource = parsedApp.statements.find(statement => ts.isFunctionDeclaration(statement)
  && statement.name?.text === "runExplicitTaskAction").getText(parsedApp);
const handlerScript = ts.transpileModule(handlerSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
}).outputText;
function uiHarness() {
  const values = { "chat-task": "source", "chat-mode": "agent", "chat-verification-root": "/workspace/ozon_market_analytics", "chat-query": "Unsent user draft" };
  const requests = [];
  const messages = [];
  const streams = [];
  const selectedTasks = [];
  const task = { task_id: "source", revision: 5, available_actions: ["continue", "continue_verification", "reconcile", "create_verification"] };
  const ui = createContext({
    ...context.Actions, currentThread: "thread-a", activeChatJob: "",
    taskActionLabels: { continue: "Resume", continue_verification: "Verify", reconcile: "Reconcile", create_verification: "New verification" },
    value: key => values[key], checked: () => true,
    savedChatTasks: new Map([["source", task]]), pendingTaskActions: new Set(), activeChatTasks: new Set(),
    taskActionRequests: new TaskActionRequests({ getItem: () => null, setItem: () => {} }, newKey),
    text: value => value == null ? "" : String(value), window: { confirm: () => true },
    updateTaskActionControls: () => {}, selectSavedTask: () => {}, showToast: () => {},
    refreshChatJobs: async taskId => { selectedTasks.push(taskId); }, showExecutionSummary: () => {},
    appendMessage: (_role, text) => { const node = { textContent: text, parentElement: { classList: { remove: () => {} } } }; messages.push(node); return node; },
    watchExplicitExecution: result => streams.push(result),
    api: async (path, options) => { requests.push({ path, body: options?.body ? JSON.parse(options.body) : null }); throw new Error("HTTP transport unavailable"); },
  });
  runInContext(handlerScript, ui);
  return { ui, values, requests, messages, streams, selectedTasks };
}
let harness = uiHarness();
const sourceTask = harness.ui.savedChatTasks.get("source");
sourceTask.available_actions = ["reconcile"];
await harness.ui.runExplicitTaskAction("continue");
assert.equal(harness.requests.length, 0, "Runtime disallowed actions must not be sent");
sourceTask.available_actions = ["continue", "continue_verification", "reconcile", "create_verification"];
await harness.ui.runExplicitTaskAction("continue");
await harness.ui.runExplicitTaskAction("continue");
assert.equal(harness.values["chat-query"], "Unsent user draft", "Failed explicit action must not erase composer text");
assert.equal(harness.requests[0].path, "/api/tasks/source/resume");
assert.equal(harness.requests[0].body.idempotency_key, harness.requests[1].body.idempotency_key);
assert.equal(harness.requests[0].body.expected_revision, 5);
assert.equal(harness.requests[0].body.thread_id, "thread-a");
assert.ok(!Object.hasOwn(harness.requests[0].body, "query"), "Draft/log text must not reach the explicit-action router");

harness = uiHarness();
let finishRequest;
harness.ui.api = async (path, options) => {
  harness.requests.push({ path, body: JSON.parse(options.body) });
  return new Promise(resolveRequest => { finishRequest = resolveRequest; });
};
const firstClick = harness.ui.runExplicitTaskAction("continue_verification");
await harness.ui.runExplicitTaskAction("continue_verification");
assert.equal(harness.requests.length, 1);
assert.equal(harness.requests[0].body.allow_write, false, "VERIFY continuation never requests source mutation");
finishRequest({ task_id: "execution", active_task_id: "source" });
await firstClick;
assert.equal(harness.streams.length, 1);

harness = uiHarness();
harness.ui.window.confirm = () => false;
await harness.ui.runExplicitTaskAction("create_verification");
assert.equal(harness.requests.length, 0, "Linked task needs explicit confirmation");
harness.ui.window.confirm = () => true;
await harness.ui.runExplicitTaskAction("create_verification");
assert.equal(harness.requests[0].path, "/api/tasks/source/verification");
assert.equal(harness.requests[0].body.project_root, "/workspace/ozon_market_analytics");
assert.equal(harness.requests[0].body.confirmed, true);

harness = uiHarness();
harness.ui.api = async () => ({ reused: true, action_status: "reserved", reason_code: "ACTION_DISPATCH_UNCONFIRMED", available_actions: ["reconcile"], recovery_message: "Сверьте состояние. Повторная отправка запрещена." });
await harness.ui.runExplicitTaskAction("continue");
assert.equal(harness.streams.length, 0, "A reserved response without execution ID must not open SSE");
assert.match(harness.messages.at(-1).textContent, /ACTION_DISPATCH_UNCONFIRMED/);
assert.match(harness.messages.at(-1).textContent, /Повторная отправка запрещена/);
assert.deepEqual(Array.from(harness.ui.savedChatTasks.get("source").available_actions), ["reconcile"]);
harness.ui.savedChatTasks.get("source").available_actions = ["continue"];
harness.ui.api = async () => ({ reused: true, action_status: "failed", reason_code: "WORKER_START_FAILED", available_actions: ["reconcile"] });
await harness.ui.runExplicitTaskAction("continue");
assert.match(harness.messages.at(-1).textContent, /WORKER_START_FAILED/);
assert.equal(harness.streams.length, 0);
harness.ui.savedChatTasks.get("source").available_actions = ["continue"];
harness.ui.api = async path => path.endsWith("/resume")
  ? { reused: true, task_id: "execution", active_task_id: "source" }
  : { status: "blocked", terminal: {} };
await harness.ui.runExplicitTaskAction("continue");
assert.equal(harness.streams.length, 0, "A terminal replay must show status, not open SSE");

harness = uiHarness();
const child = { task_id: "saved-child", revision: 1, available_actions: ["continue", "continue_verification", "reconcile"] };
harness.ui.savedChatTasks.set("saved-child", child);
harness.ui.api = async () => ({
  reused: true, action_status: "reserved", active_task_id: "saved-child",
  reason_code: "ACTION_DISPATCH_UNCONFIRMED", available_actions: ["reconcile"],
  recovery_message: "Связанная проверка сохранена. Выберите её и нажмите Продолжить проверки.",
});
await harness.ui.runExplicitTaskAction("create_verification");
assert.deepEqual(harness.selectedTasks, ["saved-child"], "An undispatched saved child must be selected after refreshing tasks");
assert.ok(child.available_actions.includes("continue_verification"), "Source action blockers must not overwrite the child's runtime actions");
assert.equal(harness.streams.length, 0, "Selecting a child must not execute it automatically");
assert.match(harness.messages.at(-1).textContent, /Связанная проверка сохранена/);
console.log("task_actions_ok");
