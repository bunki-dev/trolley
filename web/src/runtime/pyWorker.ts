// Runs Trolley's Python agent in the browser with Pyodide, off the main thread.

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.8/full/";
const VERSION = import.meta.env.VITE_PY_VERSION ?? "dev";
const BASE = import.meta.env.BASE_URL;

type Method = "init" | "chat" | "reset" | "tool" | "confirm" | "describe";
type Request = { id: number; method: Method; args: unknown[] };

interface Pyodide {
  loadPackage(names: string[]): Promise<void>;
  unpackArchive(buffer: ArrayBuffer, format: string, options: { extractDir: string }): void;
  runPython(code: string): unknown;
  runPythonAsync(code: string): Promise<unknown>;
  globals: { set(name: string, value: unknown): void };
  FS: { writeFile(path: string, data: Uint8Array): void; mkdirTree(path: string): void };
}

const ctx = self as unknown as {
  postMessage(message: unknown): void;
  onmessage: ((event: MessageEvent<Request>) => void) | null;
};

const progress = (text: string) => ctx.postMessage({ type: "progress", text });

async function fetchBytes(path: string): Promise<Uint8Array> {
  const res = await fetch(`${BASE}${path}?v=${VERSION}`);
  if (!res.ok) throw new Error(`Couldn't download ${path} (${res.status}).`);
  return new Uint8Array(await res.arrayBuffer());
}

async function boot(): Promise<Pyodide> {
  progress("Downloading the Python runtime (first visit only)");
  const { loadPyodide } = await import(/* @vite-ignore */ `${PYODIDE_URL}pyodide.mjs`);
  const py: Pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  progress("Loading libraries");
  await py.loadPackage(["pydantic"]);
  progress("Loading the agent and 4,000+ products");
  const [bundle, catalogue, prices] = await Promise.all([
    fetchBytes("py/trolley.zip"),
    fetchBytes("py/data/catalogue.json.gz"),
    fetchBytes("py/data/prices.json"),
  ]);
  py.unpackArchive(bundle.buffer as ArrayBuffer, "zip", { extractDir: "/home/pyodide/app" });
  py.FS.mkdirTree("/home/pyodide/data");
  py.FS.writeFile("/home/pyodide/data/catalogue.json.gz", catalogue);
  py.FS.writeFile("/home/pyodide/data/prices.json", prices);
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide/app'); import trolley.browser as tb");
  return py;
}

const ready = boot();

ctx.onmessage = async (event: MessageEvent<Request>) => {
  const { id, method, args } = event.data;
  try {
    const py = await ready;
    let out: unknown;
    if (method === "init") {
      progress("Indexing products");
      out = py.runPython("tb.init('/home/pyodide/data')");
    } else if (method === "reset") {
      out = py.runPython("tb.reset()");
    } else if (method === "describe") {
      py.globals.set("_settings", JSON.stringify(args[0] ?? {}));
      out = py.runPython("tb.describe(_settings)");
    } else if (method === "tool") {
      py.globals.set("_name", args[0]);
      py.globals.set("_args", JSON.stringify(args[1] ?? {}));
      out = py.runPython("tb.tool(_name, _args)");
    } else if (method === "confirm") {
      py.globals.set("_token", args[0]);
      out = py.runPython("tb.confirm(_token)");
    } else {
      py.globals.set("_msg", args[0]);
      py.globals.set("_settings", JSON.stringify(args[1] ?? {}));
      out = await py.runPythonAsync("await tb.chat(_msg, _settings)");
    }
    ctx.postMessage({ type: "result", id, result: JSON.parse(String(out)) });
  } catch (e) {
    ctx.postMessage({ type: "result", id, error: (e as Error).message ?? String(e) });
  }
};
