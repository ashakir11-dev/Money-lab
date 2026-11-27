// Browser checks of the Next.js dashboard against the real API (gates 1 and 3).
// Needs a built dashboard (`make web`) and Playwright's Chromium. Run: `make e2e`.
const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
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
  // CHROME_PATH uses a system Chrome when Playwright's browser is not installed.
  browser = await chromium.launch(
    process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
  );
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

test("gate 3: duplicate, change the RSI threshold, run, and compare the two reports", async () => {
  const { page, errors } = await newPage();
  let clicks = 0;
  const click = async (loc) => { clicks += 1; await loc.click(); };

  await page.goto(`${server.base}/report?id=${original}`);
  await click(page.getByRole("button", { name: /duplicate and edit/i }).or(page.getByRole("link", { name: /duplicate and edit/i })).first());
  await page.getByTestId("run-backtest").waitFor();
  // Saved rules show as cards; one click opens the card to edit it.
  await click(page.getByTestId("condition-1"));
  const value = page.getByTestId("condition-1").getByTestId("threshold-value");
  await value.waitFor();
  assert.equal(Number(await value.inputValue()), 30);
  clicks += 1; // editing the threshold
  await value.fill("35");
  await click(page.getByTestId("run-backtest"));
  await click(page.getByTestId("compare-with-original"));

  await page.getByTestId("compare-metrics").waitFor({ timeout: 60000 });
  await page.getByTestId("config-diff").waitFor();
  const diff = await page.getByTestId("config-diff").innerText();
  assert.match(diff, /30/);
  assert.match(diff, /35/);
  const metrics = await page.getByTestId("compare-metrics").innerText();
  assert.match(metrics, /Return/);
  assert.match(metrics, /Versus the S&P/);
  assert.match(metrics, /SPY return/);
  assert.equal(await page.getByTestId("first-difference").count(), 0);
  assert.equal(await page.getByTestId("inspector").count(), 0);
  assert.ok(clicks <= 6, `took ${clicks} interactions`);
  assert.deepEqual(errors, []);
});

test("gate 3 walkthrough: drop DDD and change the threshold; compare shows the setting change", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/run?id=${original}`);
  await page.getByRole("button", { name: /duplicate and edit/i }).or(page.getByRole("link", { name: /duplicate and edit/i })).first().click();
  const uni = page.getByTestId("builder-universe");
  await uni.waitFor();
  await page.waitForFunction(() => /DDD/.test(document.querySelector('[data-testid="builder-universe"]')?.value || ""));
  await uni.fill("AAA, BBB, CCC");
  await page.getByTestId("condition-1").click();
  await page.getByTestId("condition-1").getByTestId("threshold-value").fill("35");
  await page.getByTestId("run-backtest").click();
  await page.getByTestId("compare-with-original").click();
  await page.getByTestId("compare-metrics").waitFor({ timeout: 60000 });
  const diff = await page.getByTestId("config-diff").innerText();
  assert.match(diff, /DDD/);
  assert.match(diff, /35/);
  assert.match(await page.getByTestId("compare-metrics").innerText(), /Versus the S&P/);
  assert.equal(await page.getByTestId("first-difference").count(), 0);
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
  await page.getByTestId("condition-0").click();
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
  await page.getByTestId("rank-block").click();
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
  await page.getByTestId("price-exit-stop_loss").click();
  await page.getByTestId("price-exit-stop_loss-pct").waitFor();
  assert.equal(Number(await page.getByTestId("price-exit-stop_loss-pct").inputValue()), 5);
  await page.getByTestId("rank-block").click();
  assert.equal(await page.getByTestId("rank-indicator").inputValue(), "rsi");
  assert.equal(await page.getByTestId("sizing-volatility").getAttribute("class"), "on");
  const restored = page.locator('[data-testid^="condition-"]').last();
  await restored.click();
  assert.equal(await restored.getByTestId("other-select").inputValue(), "sma");
  assert.deepEqual(errors, []);
});

test("report page: Export report downloads the trades and the summary", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/report?id=${original}`);
  const link = page.getByTestId("export-report");
  await link.waitFor();
  assert.equal((await link.innerText()).trim(), "Export report");
  assert.equal(await link.getAttribute("href"), `/api/runs/${encodeURIComponent(original)}/export`);

  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  assert.match(download.suggestedFilename(), /^moneylab-report-.+\.zip$/);
  const dest = path.join(os.tmpdir(), download.suggestedFilename());
  await download.saveAs(dest);
  const check = execFileSync(
    "python3",
    [
      "-c",
      [
        "import json, sys, zipfile",
        "archive = zipfile.ZipFile(sys.argv[1])",
        "assert archive.namelist() == ['summary.json', 'trades.csv']",
        "summary = json.loads(archive.read('summary.json'))",
        "assert summary['id'] == sys.argv[2]",
        "assert summary['status'] == 'completed'",
        "assert summary['name']",
        "lines = archive.read('trades.csv').decode().splitlines()",
        "assert lines[0].startswith('symbol,entry_session,exit_session,')",
        "assert len(lines) > 1",
      ].join("\n"),
      dest,
      original,
    ],
    { encoding: "utf8" },
  );
  assert.equal(check, "");
  assert.deepEqual(errors, []);
});

test("report page: trade quality, versus SPY, risk, and the detailed tables render", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/report?id=${original}`);
  for (const id of ["trade-quality", "versus-spy", "risk"]) await page.getByTestId(id).waitFor();
  const quality = await page.getByTestId("trade-quality").innerText();
  assert.match(quality, /win rate/i);
  assert.match(quality, /profit factor/i);
  assert.match(quality, /expectancy per trade/i);
  // The demo run has far fewer than 30 trades, so the warning shows.
  await page.getByTestId("low-sample").first().waitFor();
  assert.match(await page.getByTestId("versus-spy").innerText(), /edge per trade vs SPY/i);
  assert.match(await page.getByTestId("risk").innerText(), /longest time underwater/i);
  // Detailed mode adds the big tables, a real hold length and the benchmark edge per trade.
  await page.getByRole("button", { name: "Detailed" }).click();
  for (const id of ["by-year", "monthly-grid", "by-exit", "by-symbol", "by-regime", "costs", "streaks", "funnel", "rolling-return", "trade-table"]) {
    await page.getByTestId(id).waitFor({ timeout: 15000 });
  }
  const table = await page.getByTestId("trade-table").innerText();
  assert.match(table, /vs SPY/i);
  assert.match(table, /Held the full time/);
  // The holding-period exit is 10 sessions; the old column showed the order wait (1).
  assert.match(table, /\b10 sess\b/);
  assert.deepEqual(errors, []);
});

test("builder: idle cash held in SPY reaches the run, the report and a duplicate", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/builder?from=${original}`);
  await page.getByTestId("run-backtest").waitFor();
  assert.equal(await page.getByTestId("idle-cash-cash").getAttribute("class"), "on");
  await page.getByTestId("idle-cash-benchmark").click();
  assert.equal(await page.getByTestId("idle-cash-benchmark").getAttribute("class"), "on");
  await page.waitForFunction(
    () => /held in SPY/.test(document.querySelector('[data-testid="builder-summary"]')?.textContent ?? ""),
    null,
    { timeout: 10000 },
  );
  await page.getByTestId("run-backtest").click();
  await page.getByTestId("open-new-run").click({ timeout: 60000 });
  await page.getByTestId("run-facts").waitFor();
  await page.waitForFunction(
    () => /Idle cash held in SPY at the end/.test(document.querySelector('[data-testid="run-facts"]')?.textContent ?? ""),
    null,
    { timeout: 15000 },
  );
  await page.getByRole("link", { name: /duplicate and edit/i }).click();
  await page.getByTestId("idle-cash-benchmark").waitFor();
  assert.equal(await page.getByTestId("idle-cash-benchmark").getAttribute("class"), "on");
  assert.deepEqual(errors, []);
});

test("builder: support/resistance breakout with an ADX trend filter runs and opens its report", async () => {
  const { page, errors } = await newPage();
  await page.goto(`${server.base}/builder?from=${original}`);
  assert.equal(await page.getByTestId("builder-timeframe").inputValue(), "1d");
  const first = page.getByTestId("condition-0");
  await first.click(); // saved rules are cards; a click opens one for editing
  await first.getByTestId("kind-select").selectOption("price_cross");
  await first.getByTestId("indicator-select").selectOption("resistance");
  await first.getByTestId("param-min_move").fill("1.5");
  const second = page.getByTestId("condition-1");
  await second.click();
  await second.getByTestId("kind-select").selectOption("threshold");
  await second.getByTestId("indicator-select").selectOption("adx");
  await second.getByTestId("threshold-value").fill("25");
  await second.getByTestId("threshold-op").selectOption("above");
  await page.waitForFunction(() =>
    /ADX\(14\) above 25/.test(document.querySelector('[data-testid="builder-summary"]')?.textContent ?? ""),
  );
  assert.match(
    await page.getByTestId("builder-summary").innerText(),
    /close crosses above resistance \(1\.5 ATR turns\) and ADX\(14\) above 25/,
  );
  await page.getByTestId("run-backtest").click();
  const open = page.getByTestId("open-new-run");
  await open.waitFor({ timeout: 60000 });
  await open.click();
  await page.getByTestId("run-summary").waitFor();
  assert.match(await page.getByTestId("run-summary").innerText(), /resistance/);
  assert.equal(await page.getByTestId("run-timeframe").innerText(), "1d bars");
  const id = new URL(page.url()).searchParams.get("id");
  const run = await api(server.base, "GET", `/api/runs/${id}`);
  assert.deepEqual(run.resolved.entry.map((c) => c.indicator.id), ["resistance", "adx"]);
  assert.equal(run.resolved.timeframe, "1d");
  assert.deepEqual(errors.filter((e) => !/status of 422/.test(e)), []);
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
