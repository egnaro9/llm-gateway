// Boots Pyodide, installs FastAPI + the llmgateway wheel, and drives the REAL
// ASGI app in-process. There's no HTTP server in a browser tab — so instead of
// faking one, we hand requests straight to the same ASGI application uvicorn
// would serve. Auth, rate limiting, caching, retries and cost accounting all
// execute exactly as they do in production.

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const statusText = $("statusText");
const setStatus = (t, s) => { statusText.textContent = t; statusEl.className = "status" + (s ? " " + s : ""); };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let py = null;
let reqCount = 0;

async function boot() {
  try {
    setStatus("Booting Python (WebAssembly)…");
    py = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.2/full/" });
    await py.loadPackage("micropip");
    const micropip = py.pyimport("micropip");

    setStatus("Installing FastAPI…");
    await micropip.install(["fastapi"]);

    setStatus("Installing the llmgateway wheel…");
    await micropip.install.callKwargs(window.WHEEL_URL || "./llmgateway-0.1.0-py3-none-any.whl", { deps: false });

    setStatus("Starting the gateway…");
    await py.runPythonAsync(`
import json, time

# Starlette/FastAPI run *sync* endpoints and *sync* dependencies (plain \`def\`)
# in a threadpool. WebAssembly has no threads. Every one of those paths bottoms
# out in anyio.to_thread.run_sync, so patching that one function covers all of
# them — endpoints, Depends(), the lot.
#
# This changes WHERE a handler runs, not WHAT it does: the gateway's routes are
# pure CPU against the mock provider, so there is nothing to block on. The
# library itself is untouched — this accommodation lives only in the demo.
import anyio.to_thread
async def _run_sync_inline(func, *args, **kwargs):
    return func(*args)
anyio.to_thread.run_sync = _run_sync_inline

from llmgateway.app import Config, create_app
import llmgateway

# A tight rate limit so the 429 path is reachable by hand in the demo.
CFG = Config(api_keys=frozenset({"dev-key"}), rate_capacity=8, rate_refill_per_sec=0.5)
APP = create_app(CFG)

# FastAPI's TestClient can't run here: it drives the app through anyio's
# blocking portal, which needs a thread, and WebAssembly has none. So speak
# ASGI to the app directly — which is what uvicorn does anyway, minus the
# socket. Same app object, same middleware stack, same handlers.
async def request_json(method, path, body, api_key):
    headers = [(b"content-type", b"application/json")]
    if api_key:
        headers.append((b"authorization", f"Bearer {api_key}".encode()))
    body_bytes = body.encode() if body else b""

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 0),
        "server": ("localhost", 80),
    }

    sent = False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    messages = []
    async def send(message):
        messages.append(message)

    t0 = time.perf_counter()
    await APP(scope, receive, send)
    elapsed = (time.perf_counter() - t0) * 1000

    status, resp_headers, chunks = None, {}, []
    for m in messages:
        if m["type"] == "http.response.start":
            status = m["status"]
            resp_headers = {k.decode().lower(): v.decode() for k, v in m.get("headers", [])}
        elif m["type"] == "http.response.body":
            chunks.append(m.get("body", b""))

    raw = b"".join(chunks)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"raw": raw.decode("utf-8", "replace")}

    return json.dumps({
        "status": status,
        "body": payload,
        "elapsed_ms": round(elapsed, 1),
        "retry_after": resp_headers.get("retry-after"),
    })

def version():
    return llmgateway.__version__
`);

    const v = await py.runPythonAsync("version()");
    setStatus(`Ready — llm-gateway ${v} serving requests in this tab`, "ready");
    document.querySelectorAll("button").forEach((b) => (b.disabled = false));
  } catch (err) {
    setStatus("Failed to boot: " + err, "err");
    console.error(err);
  }
}

async function send(method, path, body, key) {
  const r = JSON.parse(
    await py.runPythonAsync(
      `await request_json(${JSON.stringify(method)}, ${JSON.stringify(path)}, ${JSON.stringify(body || "")}, ${JSON.stringify(key || "")})`
    )
  );
  log(method, path, r);
  return r;
}

function statusClass(s) {
  return s < 300 ? "ok" : s === 429 ? "warn" : "bad";
}

function log(method, path, r) {
  reqCount++;
  const b = r.body;
  let summary = "";
  if (b.choices) {
    summary =
      `<div class="kv"><span>answer</span> ${esc(b.choices[0].message.content)}</div>` +
      `<div class="kv"><span>cached</span> <b class="${b.cached ? "teal" : "dim"}">${b.cached}</b>` +
      ` &nbsp;·&nbsp; <span>cost</span> <b>$${b.cost_usd}</b>` +
      ` &nbsp;·&nbsp; <span>tokens</span> ${b.usage.total_tokens}` +
      ` &nbsp;·&nbsp; <span>provider</span> ${esc(b.provider)}</div>`;
  } else if (b.detail) {
    summary = `<div class="kv"><span>detail</span> <b class="red">${esc(b.detail)}</b>${
      r.retry_after ? ` &nbsp;·&nbsp; <span>Retry-After</span> ${esc(r.retry_after)}s` : ""
    }</div>`;
  }
  const entry = document.createElement("div");
  entry.className = "req";
  entry.innerHTML = `
    <div class="reqhead">
      <span class="method">${method}</span> <code>${esc(path)}</code>
      <span class="code ${statusClass(r.status)}">${r.status}</span>
      <span class="ms">${r.elapsed_ms} ms</span>
    </div>
    ${summary}
    <details><summary>response JSON</summary><pre>${esc(JSON.stringify(b, null, 2))}</pre></details>`;
  const out = $("out");
  out.prepend(entry);
  $("reqCount").textContent = reqCount;
}

const CHAT = (content, model = "mock-1", extra = {}) =>
  JSON.stringify({ model, messages: [{ role: "user", content }], ...extra });

async function refreshMetrics() {
  const r = JSON.parse(await py.runPythonAsync(`await request_json("GET", "/metrics", "", "dev-key")`));
  const m = r.body;
  $("metrics").innerHTML = [
    ["Requests", m.requests, ""],
    ["Cache hits", m.cache_hits, "teal"],
    ["Hit rate", (m.cache_hit_rate * 100).toFixed(0) + "%", "teal"],
    ["Rate limited", m.rate_limited, m.rate_limited ? "red" : ""],
    ["Tokens", m.total_tokens, ""],
    ["Cost", "$" + m.total_cost_usd, "amber"],
  ]
    .map(([k, v, c]) => `<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`)
    .join("");
}

const actions = {
  chat: () => send("POST", "/v1/chat/completions", CHAT($("msg").value || "hello gateway"), "dev-key"),
  twice: async () => {
    await send("POST", "/v1/chat/completions", CHAT("cache me"), "dev-key");
    await send("POST", "/v1/chat/completions", CHAT("cache me"), "dev-key");
  },
  noauth: () => send("POST", "/v1/chat/completions", CHAT("hello"), ""),   // no Authorization header at all
  badkey: () => send("POST", "/v1/chat/completions", CHAT("hello"), "wrong-key"),
  priced: () => send("POST", "/v1/chat/completions", CHAT("what does this cost?", "mock-pro"), "dev-key"),
  flaky: () => send("POST", "/v1/chat/completions", CHAT("survive the flake", "mock-flaky"), "dev-key"),
  invalid: () => send("POST", "/v1/chat/completions", JSON.stringify({ model: "mock-1", messages: [] }), "dev-key"),
  flood: async () => {
    for (let i = 0; i < 10; i++) {
      await send("POST", "/v1/chat/completions", CHAT("flood " + i), "dev-key");
    }
  },
  models: () => send("GET", "/v1/models", "", "dev-key"),
  health: () => send("GET", "/health", "", "dev-key"),
};

document.querySelectorAll("[data-act]").forEach((b) =>
  b.addEventListener("click", async () => {
    document.querySelectorAll("button").forEach((x) => (x.disabled = true));
    try { await actions[b.dataset.act](); await refreshMetrics(); } catch (e) { console.error(e); setStatus("Request failed: " + e, "err"); } finally {
      document.querySelectorAll("button").forEach((x) => (x.disabled = false));
    }
  })
);
boot();
