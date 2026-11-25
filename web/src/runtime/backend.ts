import type { AppState, ChatResponse, Meta } from "../types";
import { loadSettings } from "./settings";

export interface ToolResult {
  result?: Record<string, unknown>;
  error?: string;
  state?: AppState;
}

export interface Backend {
  start(onProgress: (text: string) => void): Promise<{ state: AppState; meta: Meta; products: number }>;
  chat(message: string): Promise<ChatResponse>;
  tool(name: string, args: Record<string, unknown>): Promise<ToolResult>;
  confirm(token: string): Promise<ToolResult>;
  reset(): Promise<{ state: AppState }>;
}

export function createBackend(): Backend {
  const worker = new Worker(new URL("./pyWorker.ts", import.meta.url), { type: "module" });
  const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  let nextId = 1;
  let onProgress: (text: string) => void = () => {};

  worker.onmessage = (event: MessageEvent) => {
    const msg = event.data;
    if (msg.type === "progress") return onProgress(msg.text);
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    if (msg.error) p.reject(new Error(msg.error));
    else p.resolve(msg.result);
  };

  const call = <T,>(method: string, ...args: unknown[]) =>
    new Promise<T>((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      worker.postMessage({ id, method, args });
    });

  return {
    start(progress) {
      onProgress = progress;
      return call("init");
    },
    async chat(message) {
      const res = await call<ChatResponse>("chat", message, loadSettings());
      if (res.error) throw new Error(res.error);
      return res;
    },
    tool: (name, args) => call("tool", name, args),
    confirm: (token) => call("confirm", token),
    reset: () => call("reset"),
  };
}
