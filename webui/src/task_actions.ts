/** Typed user actions are transport commands, never natural-language routing. */
export type TaskAction =
  | "continue"
  | "continue_verification"
  | "reconcile"
  | "create_verification";

export const TASK_ACTIONS: readonly TaskAction[] = [
  "continue", "continue_verification", "reconcile", "create_verification",
];

export function availableTaskActions(value: unknown): TaskAction[] {
  if (!Array.isArray(value)) return [];
  return TASK_ACTIONS.filter((action) => value.includes(action));
}

export function taskActionEndpoint(taskId: string, action: TaskAction): string {
  const suffix = action === "reconcile" ? "reconcile"
    : action === "create_verification" ? "verification" : "resume";
  return `/api/tasks/${encodeURIComponent(taskId)}/${suffix}`;
}

export function isTerminalTaskStatus(status: unknown): boolean {
  return ["completed", "complete", "partial", "blocked", "cancelled", "failed"].includes(String(status));
}

interface KeyStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** Keep uncertain retries identical, including across a page reload. */
export class TaskActionRequests {
  private readonly inFlight = new Set<string>();
  private keys: Record<string, string> = {};
  private readonly storageKey = "dca_explicit_action_keys_v1";

  constructor(private readonly storage: KeyStorage, private readonly newKey: () => string) {
    try {
      const parsed: unknown = JSON.parse(storage.getItem(this.storageKey) || "{}");
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        this.keys = Object.fromEntries(Object.entries(parsed)
          .filter(([signature, key]) => signature.length < 2000 && typeof key === "string")
          .slice(-128)) as Record<string, string>;
      }
    } catch { /* Missing or corrupt browser storage never grants authority. */ }
  }

  begin(signature: string): string | null {
    if (this.inFlight.has(signature)) return null;
    this.inFlight.add(signature);
    if (!Object.prototype.hasOwnProperty.call(this.keys, signature)) {
      this.keys[signature] = this.newKey();
      this.keys = Object.fromEntries(Object.entries(this.keys).slice(-128));
      try { this.storage.setItem(this.storageKey, JSON.stringify(this.keys)); }
      catch { /* In-memory retry protection still applies if storage is full. */ }
    }
    return this.keys[signature];
  }

  finish(signature: string): void {
    this.inFlight.delete(signature);
  }
}
