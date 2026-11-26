// Gate 2 in the browser: submit a batch of variants from the dashboard's page and time
// it until the comparison of variant 1 with variant 2 is visible.
// Usage: node e2e/batch_visible.cjs BASE_URL BODY_JSON [BATCHES]
const fs = require("node:fs");
const { chromium } = require("playwright");

async function main() {
  const [base, bodyPath, n = "3"] = process.argv.slice(2);
  const body = fs.readFileSync(bodyPath, "utf8");
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const out = [];
  for (let i = 0; i < Number(n); i++) {
    await page.goto(`${base}/`);
    await page.getByTestId("nav-compare").waitFor();
    const t0 = Date.now();
    let job = await page.evaluate(async (b) => {
      const r = await fetch("/api/batches", { method: "POST", headers: { "content-type": "application/json" }, body: b });
      return r.json();
    }, body);
    while (job.status !== "complete") {
      if (job.status === "failed") throw new Error(job.error);
      await new Promise((r) => setTimeout(r, 100));
      job = await (await fetch(`${base}/api/jobs/${job.id}`)).json();
    }
    const tSaved = Date.now();
    const [a, b] = job.run_ids;
    await page.goto(`${base}/compare?a=${a}&b=${b}`);
    await page.getByTestId("compare-metrics").waitFor({ timeout: 120000 });
    const t1 = Date.now();
    out.push({ batch: i + 1, runs: job.run_ids.length, saved_s: (tSaved - t0) / 1000, visible_s: (t1 - tSaved) / 1000, total_s: (t1 - t0) / 1000 });
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out) + "\n");
}

main().catch((e) => { console.error(e); process.exit(1); });
