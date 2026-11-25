// Browser checks of the Next.js dashboard against the real API (gates 1 and 3).
// Needs a built dashboard (`make web`) and Playwright's Chromium. Run: `make e2e`.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { ROOT, startServer, api, waitJob } = require("./server.cjs");

const FIXTURE = path.join(ROOT, "fixtures", "data", "rsi_divergence.csv");

function rsiStrategy(threshold) {
  // Same strategy as moneylab.demo.rsi_strategy.
  return {
    name: `SMA20 cross + RSI<${threshold}`,
    universe: ["AAA", "BBB", "CCC", "DDD"],
    benchmark: "SPY",
    entry: [
      { kind: "price_cross", indicator: { id: "sma", params: { length: 20 } }, params: { direction: "above" } },
      { kind: "threshold", indicator: { id: "rsi", params: { length: 14 } }, params: { op: "below", value: threshold } },
    ],
    exit: { kind: "holding_period", params: { sessions: 10 } },
  };
}

let server, browser, snapshotId, original;

test.before(async () => {
  server = await startServer();
  const snap = await api(server.base, "POST", "/api/snapshots/csv", {
    csv: fs.readFileSync(FIXTURE, "utf8"),
    filename: "rsi_divergence.csv",
    label: "demo",
  });
  snapshotId = snap.id;
  const job = await api(server.base, "POST", "/api/runs", { config: rsiStrategy(30), snapshot_id: snapshotId });
  original = (await waitJob(server.base, job)).run_ids[0];
  browser = await chromium.launch();
});

test.after(async () => {
  await browser?.close();
  server?.stop();
});

async function newPage() {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  return { page, errors };
}

test("gate 3: duplicate, change the RSI threshold, run, and reach the first difference", async () => {
  const { page, errors } = await newPage();
  let clicks = 0;
  const click = async (loc) => { clicks += 1; await loc.click(); };

  await page.goto(`${server.base}/run?id=${original}`);
  await click(page.getByRole("button", { name: /duplicate and edit/i }).or(page.getByRole("link", { name: /duplicate and edit/i })).first());
  await page.getByTestId("run-backtest").waitFor();
  const value = page.getByTestId("condition-1").getByTestId("threshold-value");
  await value.waitFor();
  assert.equal(Number(await value.inputValue()), 30);
  clicks += 1; // editing the threshold
  await value.fill("35");
  await click(page.getByTestId("run-backtest"));
  await click(page.getByTestId("compare-with-original"));

  const card = page.getByTestId("first-difference");
  await card.waitFor({ timeout: 60000 });
  assert.equal(await card.getAttribute("data-session"), "2023-04-04");
  assert.equal(await card.getAttribute("data-symbol"), "AAA");
  await page.getByTestId("config-diff").waitFor();
  // One more click opens the inspector with the values behind the decision.
  await click(page.getByTestId("inspect-first-difference"));
  const inspector = page.getByTestId("inspector");
  await inspector.waitFor();
  const text = await inspector.innerText();
  assert.match(text, /31\.68/); // the observed RSI that passes 35 but not 30
  assert.ok(clicks <= 6, `took ${clicks} interactions`);
  assert.deepEqual(errors, []);
});

test("gate 1: builder controls come from the registry schema (MACD, no UI code for it)", async () => {
  const { page, errors } = await newPage();
  const comps = await api(server.base, "GET", "/api/components");
  const macd = comps.find((c) => c.id === "macd");
  assert.ok(macd, "macd is registered");
  await page.goto(`${server.base}/builder?from=${original}`);
  await page.getByTestId("add-condition").click();
  const last = page.locator('[data-testid^="condition-"]').last();
  await last.getByTestId("indicator-select").selectOption("macd");
  for (const p of macd.params) {
    const input = last.getByTestId(`param-${p.name}`);
    await input.waitFor();
    assert.equal(String(await input.inputValue()), String(p.default));
  }
  assert.deepEqual(errors, []);
});

test("compare opens on the newest run against its parent", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/compare`);
  await page.getByTestId("compare-b").waitFor();
  const runs = await api(server.base, "GET", "/api/runs");
  const newest = runs[0];
  assert.ok(newest.parent_id, "the gate 3 run is a child of the original");
  await page.waitForFunction(
    ([a, b]) => {
      const sa = document.querySelector('[data-testid="compare-a"]');
      const sb = document.querySelector('[data-testid="compare-b"]');
      return sa && sb && sa.value === a && sb.value === b;
    },
    [newest.parent_id, newest.id],
  );
  assert.deepEqual(errors, []);
});
