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

  await page.goto(`${server.base}/report?id=${original}`);
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
  // The observed RSI that passes 35 but not 30, and the account that day.
  await page.waitForFunction(
    () => /31\.68/.test(document.querySelector('[data-testid="inspector"]')?.innerText || "") &&
      !/Loading/.test(document.querySelector('[data-testid="inspector"]')?.innerText || ""),
    null,
    { timeout: 30000 },
  );
  const text = await inspector.innerText();
  assert.match(text, /31\.68/);
  assert.match(text, /100,303\.09/); // run A's cash at that close
  assert.ok(clicks <= 6, `took ${clicks} interactions`);
  assert.deepEqual(errors, []);
});

test("gate 3 walkthrough: drop DDD and change the threshold; first difference is DDD", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/run?id=${original}`);
  await page.getByRole("button", { name: /duplicate and edit/i }).or(page.getByRole("link", { name: /duplicate and edit/i })).first().click();
  const uni = page.getByTestId("builder-universe");
  await uni.waitFor();
  await page.waitForFunction(() => /DDD/.test(document.querySelector('[data-testid="builder-universe"]')?.value || ""));
  await uni.fill("AAA, BBB, CCC");
  await page.getByTestId("condition-1").getByTestId("threshold-value").fill("35");
  await page.getByTestId("run-backtest").click();
  await page.getByTestId("compare-with-original").click();
  const card = page.getByTestId("first-difference");
  await card.waitFor({ timeout: 60000 });
  assert.equal(await card.getAttribute("data-session"), "2023-03-07");
  assert.equal(await card.getAttribute("data-symbol"), "DDD");
  assert.match(await card.innerText(), /not in this run's universe/i);
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

test("builder: crossings offer only price-unit indicators; MACD fast >= slow is refused", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/builder?from=${original}`);
  const first = page.getByTestId("condition-0").getByTestId("indicator-select");
  await first.waitFor();
  const crossOpts = await first.locator("option").evaluateAll((os) => os.map((o) => o.value));
  assert.ok(!crossOpts.includes("breadth") && !crossOpts.includes("rsi"), crossOpts.join());
  await page.getByTestId("add-condition").click();
  const last = page.locator('[data-testid^="condition-"]').last();
  const opts = await last.getByTestId("indicator-select").locator("option").evaluateAll((os) => os.map((o) => o.value));
  assert.ok(opts.includes("breadth"), opts.join());
  await last.getByTestId("indicator-select").selectOption("macd");
  await last.getByTestId("param-fast").fill("30");
  await last.getByTestId("param-slow").fill("10");
  await page.getByText("Not valid yet").waitFor({ timeout: 10000 });
  assert.equal(await page.getByTestId("run-backtest").isDisabled(), true);
  // The refused validation is a 422 the browser logs; nothing else may fail.
  assert.deepEqual(errors.filter((e) => !/status of 422/.test(e)), []);
});

test("report page: benchmark purchase, open positions and watchlist selection date", async () => {
  const { page, errors } = await newPage();
  const wl = await api(server.base, "POST", "/api/watchlists", { name: "core", symbols: ["AAA", "BBB", "CCC", "DDD"], label: "t" });
  const job = await api(server.base, "POST", "/api/runs", { config: rsiStrategy(35), snapshot_id: snapshotId, watchlist: "core" });
  const id = (await waitJob(server.base, job)).run_ids[0];
  await page.goto(`${server.base}/run?id=${id}`);
  await page.waitForURL(/\/report\?id=/);
  await page.getByText(/SPY bought \d+ shares/).waitFor();
  const text = await page.locator("main").innerText();
  assert.match(text, /Open positions at the end: \d+/);
  assert.ok(text.includes(`Watchlist core v1 selected ${wl.created_at.slice(0, 10)}`), text);
  assert.deepEqual(errors, []);
});

test("builder: price exits, sizing, ranking and an indicator-vs-indicator rule reach the run", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/builder?from=${original}`);
  await page.getByTestId("run-backtest").waitFor();

  // Entry rule 3: EMA(20) above SMA. Non-price indicators may only pair with themselves.
  await page.getByTestId("add-condition").click();
  const rule = page.locator('[data-testid^="condition-"]').last();
  await rule.getByTestId("kind-select").selectOption("indicator_compare");
  await rule.getByTestId("indicator-select").selectOption("rsi");
  const rsiPartners = await rule.getByTestId("other-select").locator("option").evaluateAll((os) => os.map((o) => o.value));
  assert.deepEqual(rsiPartners, ["rsi"]);
  await rule.getByTestId("indicator-select").selectOption("ema");
  await rule.getByTestId("other-select").selectOption("sma");
  await rule.getByTestId("other-param-length").fill("50");
  assert.match(await rule.locator(".cond-sentence").innerText(), /EMA\(20\) above SMA\(50\)/);

  // An 8% stop loss next to the 10-session hold; percent shown, fraction sent.
  await page.getByTestId("add-price-exit").selectOption("stop_loss");
  const stop = page.getByTestId("price-exit-stop_loss-pct");
  assert.equal(Number(await stop.inputValue()), 8);
  await stop.fill("5");

  await page.getByTestId("sizing-volatility").click();
  await page.getByTestId("sizing-risk_fraction").waitFor();
  await page.getByTestId("rank-indicator").selectOption("rsi");
  await page.getByTestId("rank-ascending").click();

  const summary = page.getByTestId("builder-summary");
  await page.waitForFunction(
    () => /trailing|stop loss/.test(document.querySelector('[data-testid="builder-summary"]')?.textContent ?? ""),
    null,
    { timeout: 10000 },
  );
  const text = await summary.innerText();
  assert.match(text, /EMA\(20\) above SMA\(50\)/);
  assert.match(text, /5% below the buy price \(stop loss\)/);
  assert.match(text, /risk 1% of equity on a 2 x ATR\(14\) stop/);
  assert.match(text, /buys the lowest RSI\(14\) first/);

  await page.getByTestId("run-backtest").click();
  await page.getByTestId("open-new-run").click({ timeout: 60000 });
  await page.getByTestId("run-summary").waitFor();
  assert.match(await page.getByTestId("run-summary").innerText(), /5% below the buy price \(stop loss\)/);

  // Duplicating the new run brings every new setting back into the builder.
  await page.getByRole("link", { name: /duplicate and edit/i }).click();
  await page.getByTestId("price-exit-stop_loss-pct").waitFor();
  assert.equal(Number(await page.getByTestId("price-exit-stop_loss-pct").inputValue()), 5);
  assert.equal(await page.getByTestId("rank-indicator").inputValue(), "rsi");
  assert.equal(await page.getByTestId("sizing-volatility").getAttribute("class"), "on");
  assert.equal(await page.locator('[data-testid^="condition-"]').last().getByTestId("other-select").inputValue(), "sma");
  assert.deepEqual(errors, []);
});

test("paper trading page: start, deliver sessions (one twice), see the account, replay", async () => {
  const { page, errors } = await newPage();
  const lines = fs.readFileSync(FIXTURE, "utf8").trim().split("\n");
  const header = lines[0];
  const pick = (from, to) => [header, ...lines.slice(1).filter((l) => l.slice(0, 10) > from && l.slice(0, 10) <= to)].join("\n");
  await page.goto(`${server.base}/paper`);
  await page.getByTestId("paper-run").selectOption(original);
  await page.getByTestId("paper-start").fill("2023-03-31");
  await page.getByTestId("paper-create").click();
  await page.getByTestId("paper-status").waitFor();
  assert.equal(await page.getByTestId("paper-next").innerText(), "2023-04-03");

  await page.getByTestId("paper-csv").fill(pick("2023-03-31", "2023-04-06"));
  await page.getByTestId("paper-ingest").click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="paper-deliveries"] tr[data-status="accepted"]').length === 4);
  await page.getByTestId("paper-csv").fill(pick("2023-04-03", "2023-04-04"));
  await page.getByTestId("paper-ingest").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="paper-deliveries"] tr[data-status="duplicate"]'));
  await page.waitForFunction(() => document.querySelector('[data-testid="paper-latest"]')?.textContent === "2023-04-06");
  assert.equal(await page.getByTestId("paper-next").innerText(), "2023-04-10");

  await page.getByTestId("paper-replay").click();
  const rep = page.getByTestId("paper-replay-result");
  await rep.waitFor();
  assert.equal(await rep.getAttribute("data-match"), "true");
  assert.deepEqual(errors, []);
});

test("gates page lists the six acceptance gates", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/gates`);
  await page.getByTestId("gate-6").waitFor();
  assert.equal(await page.locator('[data-testid^="gate-"][data-passing]').count(), 6);
  assert.equal(await page.getByTestId("gate-3").getAttribute("data-passing"), "true");
  assert.deepEqual(errors, []);
});
