// Edit a watchlist from the Data page. Needs a built dashboard (`make web`) and Playwright.
const test = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { startServer, api } = require("./server.cjs");

let server, browser;

test.before(async () => {
  server = await startServer();
  browser = await chromium.launch();
});

test.after(async () => {
  await browser?.close();
  server?.stop();
});

test("data page edits a watchlist in a dialog", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  await api(server.base, "POST", "/api/watchlists", {
    name: "core",
    symbols: ["AAA", "BBB"],
    label: "names I follow",
  });
  await api(server.base, "POST", "/api/watchlists", { name: "other", symbols: ["CCC"], label: "" });

  await page.goto(`${server.base}/data`);
  const table = page.getByTestId("watchlists-table");
  await table.waitFor();
  await page.locator('[data-testid="edit-watchlist"][data-name="core"]').click();

  const dialog = page.getByTestId("watchlist-edit-dialog");
  await dialog.waitFor();
  assert.match(await dialog.getByRole("heading").innerText(), /Edit core v1/);
  assert.equal(await page.getByTestId("edit-wl-name").inputValue(), "core");
  assert.equal(await page.getByTestId("edit-wl-label").inputValue(), "names I follow");
  assert.equal(await page.getByTestId("edit-wl-symbols").inputValue(), "AAA BBB");

  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.match(await table.innerText(), /v1/);
  assert.doesNotMatch(await page.locator("tr", { hasText: "core" }).innerText(), /v2/);

  await page.locator('[data-testid="edit-watchlist"][data-name="core"]').click();
  await page.getByTestId("edit-wl-symbols").fill("");
  assert.equal(await page.getByTestId("edit-wl-save").isDisabled(), true);
  await page.getByTestId("edit-wl-symbols").fill("AAA MSFT");
  await page.getByTestId("edit-wl-save").click();
  await dialog.waitFor({ state: "hidden" });

  const core = page.locator('[data-testid="watchlists-table"] tr', { hasText: "core" });
  await core.getByText("v2").waitFor();
  assert.match(await core.innerText(), /MSFT/);

  await page.locator('[data-testid="edit-watchlist"][data-name="core"]').click();
  await page.getByTestId("edit-wl-name").fill("fresh");
  await page.getByTestId("edit-wl-save").click();
  await dialog.waitFor({ state: "hidden" });

  const fresh = page.locator('[data-testid="watchlists-table"] tr', { hasText: "fresh" });
  await fresh.waitFor();
  assert.match(await fresh.innerText(), /v1/);
  assert.match(await fresh.innerText(), /MSFT/);
  await page.getByTestId("watchlist-form").waitFor();
  assert.deepEqual(errors, []);
  await page.close();
});
