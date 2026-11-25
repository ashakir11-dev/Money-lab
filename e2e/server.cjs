// Start `moneylab serve` on a free port with a fresh lab home, for browser checks.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");

const ROOT = path.resolve(__dirname, "..");

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

async function startServer({ home } = {}) {
  if (!fs.existsSync(path.join(ROOT, "frontend", "out", "index.html"))) {
    throw new Error("frontend/out is missing: run `make web` first");
  }
  const port = await freePort();
  const labHome = home || fs.mkdtempSync(path.join(os.tmpdir(), "moneylab-e2e-"));
  const proc = spawn(
    "uv",
    ["run", "moneylab", "serve", "--port", String(port), "--host", "127.0.0.1"],
    { cwd: ROOT, env: { ...process.env, MONEYLAB_HOME: labHome }, stdio: "inherit" },
  );
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 300; i++) {
    try {
      const r = await fetch(`${base}/api/health`);
      if (r.ok) return { base, home: labHome, stop: () => proc.kill("SIGTERM") };
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  proc.kill("SIGTERM");
  throw new Error("server did not start");
}

async function api(base, method, url, body) {
  const r = await fetch(base + url, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${method} ${url} -> ${r.status}: ${await r.text()}`);
  return r.json();
}

async function waitJob(base, job, timeoutMs = 300000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    job = await api(base, "GET", `/api/jobs/${job.id}`);
    if (job.status === "complete") return job;
    if (job.status === "failed") throw new Error(`job failed: ${job.error}`);
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error("job timed out");
}

module.exports = { ROOT, startServer, api, waitJob };
