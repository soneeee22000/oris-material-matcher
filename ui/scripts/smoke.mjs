import { readFile } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const UI_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO_DIR = resolve(UI_DIR, "..");
const BUILD_DIR = join(REPO_DIR, "src", "oris_matcher", "api", "static", "ui");
const PLAYWRIGHT_ENTRY = join(UI_DIR, "node_modules", "playwright", "index.mjs");
const ORIGIN = "http://ui.test";
const JOB_ID = "job-smoke-1";
const RUNNING_POLLS = 1;
const DESKTOP = { width: 1280, height: 900 };
const MOBILE = { width: 390, height: 844 };
const REVIEW_BORDER = "4px";
const WAIT_MS = 10000;
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};
const BOQ_FILE = {
  name: "boq.csv",
  mimeType: "text/csv",
  buffer: Buffer.from(["Item No.,Short Description", "1,Beton", ""].join("\n")),
};
const SHOTS_DIR = process.env.UI_SMOKE_SHOTS ?? "";
const XSS_TEXT = '<img src=x onerror="window.__xss=1">Collecteur';
const FAILURE_REASON = "LLM_FAILURE:replay_miss";
const FAILED_ITEM = "10.01.0010.";
const REPLAY_RUN = "20261008T023928Z-56f85fb8";
const LIBRARIES = {
  global: { name: "global", rows: 342, sha256_12: "a1b2c3d4e5f6" },
  fr: { name: "fr", rows: 120, sha256_12: "0f0e0d0c0b0a" },
};

/**
 * Builds one result row in the shape of docs/ui-spec.md §3.
 * @param {number} position Zero-based input position.
 * @param {string} itemNo Item number.
 * @param {string} decision Decision enum value.
 * @param {string} short Short description.
 * @returns {object} Row.
 */
function row(position, itemNo, decision, short) {
  const matched = decision === "matched";
  return {
    line_id: `L${position + 1}`,
    item_no: itemNo,
    short,
    long: `${short} long text, deuxième ligne`,
    unit: matched ? "m3" : "",
    qty: "1",
    level: decision === "not_a_material" ? 0 : 1,
    kind: decision === "not_a_material" ? "header" : "item",
    decision,
    material_type: matched ? "Concrete" : null,
    material_usage: matched ? "Structural" : null,
    material_subtype: matched ? "C30/37" : null,
    reason: decision === "needs_review" ? "LOW_SIGNAL:confidence" : "OK",
    model: "claude-haiku-4-5-20251001",
    prompt_version: "3f9a1c",
    latency_ms: 1140,
    cost_usd: 0.0011,
    library_row_id: matched ? "r12" : null,
    call_ids: ["c17", "c18"],
    transport_id: `L${position + 1}`,
    suggestions: [
      { material_type: "Concrete", material_usage: "Structural", material_subtype: "C30/37", library_row_id: "r12" },
      null,
    ],
    audit: {
      gate: "G3",
      evidence: short,
      element_or_application: "base course",
      top1: "T01.U02.S03",
      top2: null,
      confidence: 62,
      candidate_gap: "narrow",
      raw_line_response: { id: `L${position + 1}`, top1: "r12" },
      error: null,
    },
  };
}

const ROWS = [
  row(0, "10.01.", "not_a_material", "Section béton"),
  row(1, "10.01.0020.", "needs_review", XSS_TEXT),
  row(2, "2.01.0010.", "matched", "Béton de propreté"),
  row(3, "10.01.0010.", "needs_review", "Couche de base"),
  row(4, "1.01.", "matched", "Grave ciment"),
];
const EXPECTED_ORDER = ROWS.map((item) => item.item_no);
const REVIEW_COUNT = ROWS.filter((item) => item.decision === "needs_review").length;

/**
 * Returns the result rows; with failures on, one needs_review line carries a model failure.
 * @param {{errors: number}} state Mock switches.
 * @returns {object[]} Rows.
 */
function resultRows(state) {
  if (!state.errors) return ROWS;
  return ROWS.map((item) =>
    item.item_no === FAILED_ITEM
      ? { ...item, reason: FAILURE_REASON, call_ids: [], audit: { ...item.audit, error: FAILURE_REASON, raw_line_response: [] } }
      : item,
  );
}

/**
 * Returns the serving fields the mocked server reports.
 * @param {{mode: string}} state Mock switches.
 * @returns {object} Serving fields.
 */
function servingFields(state) {
  return { mode: state.mode, replay_run: state.mode === "replay" ? REPLAY_RUN : null, concurrency: 4 };
}

/**
 * Returns the JSON body for one mocked API path.
 * @param {string} method HTTP method.
 * @param {string} path URL path.
 * @param {{polls: number, queueFull: boolean, token: string, errors: number}} state Mock switches.
 * @param {string | null} authorization The request's Authorization header.
 * @returns {{status: number, body: unknown} | null} Mocked response, or null when unknown.
 */
function apiResponse(method, path, state, authorization) {
  if (state.token && authorization !== `Bearer ${state.token}`) {
    return { status: 401, body: { detail: "missing or wrong bearer token" } };
  }
  if (method === "GET" && path === "/v1/libraries") {
    const ids = state.mode === "replay" ? ["global"] : ["fr", "global"];
    return { status: 200, body: ids.map((id) => ({ id, rows: LIBRARIES[id].rows, sha256_12: LIBRARIES[id].sha256_12 })) };
  }
  if (method === "GET" && path === "/v1/serving") {
    return { status: 200, body: { ...servingFields(state), libraries: state.mode === "replay" ? ["global"] : ["fr", "global"] } };
  }
  if (method === "POST" && path === "/v1/jobs" && state.queueFull) {
    return { status: 429, body: { error: "queue_full" } };
  }
  if (method === "POST" && path === "/v1/jobs" && state.uploadError) {
    return { status: 400, body: { error: "missing_columns", detail: ["BoQ Qty"] } };
  }
  if (method === "POST" && path === "/v1/jobs") {
    const library = LIBRARIES[state.library];
    return { status: 202, body: { job_id: JOB_ID, filename: "boq.csv", total_lines: ROWS.length, library, policy_resolution: "dev_selection", encoding: "utf-8", warnings: [], ...servingFields(state) } };
  }
  if (method === "GET" && path === `/v1/jobs/${JOB_ID}`) {
    state.polls += 1;
    const running = state.polls <= RUNNING_POLLS;
    const done = running ? 2 : ROWS.length;
    const library = LIBRARIES[state.library];
    return { status: 200, body: { status: running ? "running" : "done", filename: "shared.csv", library, done, total: ROWS.length, errors: 0, cost_usd: 0.0042, elapsed_s: 3.2, eta_s: running ? 4 : 0, expires_at: null, ...servingFields(state) } };
  }
  if (method === "GET" && path === `/v1/jobs/${JOB_ID}/result`) {
    const summary = { matched: 2, needs_review: 2, not_a_material: 1, errors: state.errors, cost_usd: 0.0042, wall_clock_s_per_line: 0.4, mean_attributed_latency_ms: 1140, p95_attributed_latency_ms: 1500, served_models: ["claude-haiku-4-5-20251001"], policy_resolution: "dev_selection", mode: state.mode, library: LIBRARIES[state.library] };
    return { status: 200, body: { summary, rows: resultRows(state) } };
  }
  return null;
}

/**
 * Serves a built UI file for a /ui path, refusing traversal.
 * @param {string} path URL path.
 * @returns {Promise<{body: Buffer, contentType: string} | null>} File, or null.
 */
async function staticFile(path) {
  const relative = path.replace(/^\/ui\/?/, "") || "index.html";
  const target = normalize(join(BUILD_DIR, relative));
  if (!target.startsWith(BUILD_DIR + sep)) return null;
  const body = await readFile(target).catch(() => null);
  return body ? { body, contentType: MIME[extname(target)] ?? "application/octet-stream" } : null;
}

/**
 * Installs route interception that serves the build and mocks the API.
 * @param {import("playwright").Page} page Page.
 * @param {{polls: number, csvRequests: number}} state Counters.
 */
async function installRoutes(page, state) {
  await page.route(`${ORIGIN}/**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === `/v1/jobs/${JOB_ID}/result.csv`) {
      state.csvRequests += 1;
      if (state.csvExpired) return route.fulfill({ status: 404, contentType: "application/json", body: '{"error":"job_expired","detail":"results are kept 1 h after a job ends"}' });
      return route.fulfill({ status: 200, contentType: "text/csv", body: "Item No.,Decision\n" });
    }
    if (request.method() === "POST" && path === "/v1/jobs") state.posts.push(request.postDataBuffer()?.toString("utf8") ?? "");
    const api = apiResponse(request.method(), path, state, request.headers()["authorization"] ?? null);
    if (api) return route.fulfill({ status: api.status, contentType: "application/json", body: JSON.stringify(api.body) });
    const file = await staticFile(path);
    if (file) return route.fulfill({ status: 200, contentType: file.contentType, body: file.body });
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"error":"not_found"}' });
  });
}

/**
 * Uploads a BoQ and waits for the results table.
 * @param {import("playwright").Page} page Page.
 */
async function runJob(page) {
  await page.goto(`${ORIGIN}/ui/`);
  await page.setInputFiles("#boq-file", { name: "boq.csv", mimeType: "text/csv", buffer: Buffer.from("Item No.\n1\n") });
  await shot(page, "desktop-idle");
  await page.getByRole("button", { name: "Run matching" }).click();
  await page.waitForSelector("section.progress", { timeout: WAIT_MS });
  await shot(page, "desktop-running");
  await page.waitForSelector("table.results tbody tr", { timeout: WAIT_MS });
}

/**
 * Reads the item numbers of the visible result rows.
 * @param {import("playwright").Page} page Page.
 * @returns {Promise<string[]>} Item numbers in DOM order.
 */
function visibleItemNumbers(page) {
  return page.$$eval("table.results tbody tr:not([hidden])", (rows) => rows.map((tr) => tr.dataset.itemNo ?? ""));
}

/**
 * Asserts rows render in input order and needs_review rows carry the amber marker.
 * @param {import("playwright").Page} page Page.
 */
async function checkOrderAndMarkers(page) {
  assert.deepEqual(await visibleItemNumbers(page), EXPECTED_ORDER, "rows are in input order");
  const markers = await page.$$eval("table.results tbody tr", (rows) =>
    rows.map((tr) => ({
      decision: tr.dataset.decision,
      review: tr.classList.contains("row--review"),
      border: getComputedStyle(tr.cells[0]).borderLeftWidth,
    })),
  );
  for (const marker of markers) {
    const isReview = marker.decision === "needs_review";
    assert.equal(marker.review, isReview, `row--review class on ${marker.decision}`);
    if (isReview) assert.equal(marker.border, REVIEW_BORDER, "needs_review rows have a 4px left border");
  }
  assert.equal(await page.evaluate(() => window.__xss), undefined, "BoQ text is never parsed as HTML");
  assert.equal(await page.locator("table.results img").count(), 0, "no injected elements");
}

/**
 * Asserts the needs_review filter shows only needs_review rows and is kept in the hash.
 * @param {import("playwright").Page} page Page.
 */
async function checkReviewFilter(page) {
  const chip = page.locator('[data-filter="needs_review"]');
  assert.match((await chip.textContent()) ?? "", new RegExp(String(REVIEW_COUNT)), "chip shows the count");
  await chip.click();
  assert.equal(await chip.getAttribute("aria-pressed"), "true");
  const decisions = await page.$$eval("table.results tbody tr:not([hidden])", (rows) => rows.map((tr) => tr.dataset.decision));
  assert.equal(decisions.length, REVIEW_COUNT, "only needs_review rows are visible");
  assert.ok(decisions.every((value) => value === "needs_review"));
  assert.match(await page.evaluate(() => location.hash), /filter=needs_review/);
  await page.locator('[data-filter="all"]').click();
}

/**
 * Asserts accent-insensitive search, the audit drawer and the CSV download.
 * @param {import("playwright").Page} page Page.
 * @param {{csvRequests: number}} state Counters.
 */
async function checkSearchDrawerDownload(page, state) {
  await page.fill("#search", "beton");
  await page.waitForFunction(() => document.querySelectorAll("table.results tbody tr:not([hidden])").length === 2);
  await page.fill("#search", "");
  await page.waitForFunction(() => document.querySelectorAll("table.results tbody tr:not([hidden])").length === 5);
  await page.locator("table.results tbody tr").nth(3).focus();
  await page.keyboard.press("Enter");
  const drawer = page.locator("dialog.drawer[open]");
  await drawer.waitFor({ timeout: WAIT_MS });
  await shot(page, "desktop-drawer");
  assert.match((await drawer.textContent()) ?? "", /model self-report, uncalibrated/);
  assert.match((await drawer.textContent()) ?? "", /Concrete · Structural · C30\/37 \(T01\.U02\.S03\)/, "top-1 shows labels and code");
  await page.keyboard.press("Escape");
  await page.locator("dialog.drawer[open]").waitFor({ state: "detached", timeout: WAIT_MS }).catch(() => undefined);
  assert.equal(await page.locator("dialog.drawer[open]").count(), 0, "Esc closes the drawer");
  const download = page.waitForEvent("download", { timeout: WAIT_MS });
  await page.getByRole("button", { name: "Download CSV" }).click();
  assert.match((await download).suggestedFilename(), /^boq_matched_global_\d{8}-\d{4}\.csv$/);
  assert.equal(state.csvRequests, 1, "the CSV comes from the server");
}

/**
 * Asserts a refresh resumes the job from the hash and keeps the filter.
 * @param {import("playwright").Page} page Page.
 */
async function checkResume(page) {
  await page.locator('[data-filter="needs_review"]').click();
  await page.reload();
  await page.waitForSelector("table.results tbody tr:not([hidden])", { timeout: WAIT_MS });
  const shown = await page.$$eval("table.results tbody tr:not([hidden])", (rows) => rows.length);
  assert.equal(shown, REVIEW_COUNT, "the filter survives a refresh");
  assert.equal(await page.locator('[data-filter="needs_review"]').getAttribute("aria-pressed"), "true");
  await page.locator('[data-filter="all"]').click();
}

/**
 * Asserts a full queue (429) shows the error card with a plain cause, then Start over returns to idle.
 * @param {import("playwright").Page} page Page.
 * @param {{queueFull: boolean}} state Queue switch.
 */
async function checkQueueFull(page, state) {
  state.queueFull = true;
  await page.getByRole("button", { name: "New file" }).click();
  await page.setInputFiles("#boq-file", BOQ_FILE);
  await page.getByRole("button", { name: "Run matching" }).click();
  const card = page.locator("section.error-card");
  await card.waitFor({ timeout: WAIT_MS });
  assert.match((await card.textContent()) ?? "", /already queued/);
  await shot(page, "desktop-error");
  await page.getByRole("button", { name: "Start over" }).click();
  await page.locator("#boq-file").waitFor({ state: "attached", timeout: WAIT_MS });
  state.queueFull = false;
}

/**
 * Asserts a 401 shows the token field, the token is sent on retry, and a partial run shows its banner.
 * @param {import("playwright").Page} page Page.
 * @param {{polls: number, token: string, errors: number}} state Mock switches.
 */
async function checkTokenAndPartial(page, state) {
  Object.assign(state, { token: "secret-token", errors: 1, polls: 0 });
  await page.setInputFiles("#boq-file", BOQ_FILE);
  await page.getByRole("button", { name: "Run matching" }).click();
  await page.locator("#api-token").waitFor({ timeout: WAIT_MS });
  await page.fill("#api-token", state.token);
  await page.getByRole("button", { name: "Use token" }).click();
  await page.waitForSelector("table.results tbody tr", { timeout: WAIT_MS });
  const banner = (await page.locator(".done .banner").first().textContent()) ?? "";
  assert.match(banner, /1 line could not be processed/, "partial runs show the failure banner");
  assert.match(banner, new RegExp(`1 × ${FAILURE_REASON}`), "the banner names the reason code verbatim");
  await shot(page, "desktop-partial");
}

/**
 * Asserts "Show these lines" filters to the failed lines, and "Retry failed lines" posts only
 * them with their section header.
 * @param {import("playwright").Page} page Page.
 * @param {{posts: string[], polls: number}} state Mock switches and captured uploads.
 */
async function checkFailedLines(page, state) {
  await page.getByRole("button", { name: "Show these lines" }).click();
  assert.deepEqual(await visibleItemNumbers(page), [FAILED_ITEM], "only the failed line is shown");
  assert.match(await page.evaluate(() => location.hash), /filter=failed/);
  state.posts.length = 0;
  state.polls = 0;
  await page.getByRole("button", { name: "Retry failed lines" }).click();
  await page.waitForSelector("table.results tbody tr", { timeout: WAIT_MS });
  const posted = state.posts[0] ?? "";
  assert.match(posted, /filename="boq_retry_failed\.csv"/, "the retry uploads a new file");
  assert.match(posted, /"10\.01\.0010\."/, "the failed line is sent");
  assert.match(posted, /"10\.01\."/, "its section header is sent");
  assert.doesNotMatch(posted, /"2\.01\.0010\."/, "a matched line is not sent");
}

/**
 * Asserts a download whose result expired explains the 1 h expiry instead of doing nothing.
 * @param {import("playwright").Page} page Page.
 * @param {{csvExpired: boolean}} state Mock switches.
 */
async function checkExpiredDownload(page, state) {
  state.csvExpired = true;
  await page.getByRole("button", { name: "Download CSV" }).click();
  const card = page.locator("section.error-card");
  await card.waitFor({ timeout: WAIT_MS });
  assert.match((await card.textContent()) ?? "", /expired/, "an expired result is explained");
  state.csvExpired = false;
  await page.getByRole("button", { name: "Start over" }).click();
}

/**
 * Asserts a refused file (400 missing_columns) is explained under the drop zone, not as a dead end.
 * @param {import("playwright").Page} page Page.
 * @param {{uploadError: boolean}} state Mock switches.
 */
async function checkInlineUploadError(page, state) {
  state.uploadError = true;
  await page.setInputFiles("#boq-file", BOQ_FILE);
  await page.getByRole("button", { name: "Run matching" }).click();
  const info = page.locator(".file-info--problem");
  await info.waitFor({ timeout: WAIT_MS });
  assert.match((await info.textContent()) ?? "", /Missing column: BoQ Qty/);
  assert.equal(await page.locator("section.error-card").count(), 0, "no error card for a fixable file");
  assert.equal(await page.getByRole("button", { name: "Run matching" }).isDisabled(), true);
  state.uploadError = false;
}

/**
 * Asserts a job opened from a shared link in a fresh context names the library that produced it.
 * @param {import("playwright").Browser} browser Browser.
 */
async function checkSharedLink(browser) {
  const context = await browser.newContext({ viewport: DESKTOP, acceptDownloads: true });
  const page = await context.newPage();
  const state = newState({ library: "fr" });
  await installRoutes(page, state);
  await page.goto(`${ORIGIN}/ui/#job=${JOB_ID}`);
  await page.waitForSelector("table.results tbody tr", { timeout: WAIT_MS });
  const badge = (await page.locator(".library-badge").textContent()) ?? "";
  assert.match(badge, /Library: FR \(120 rows\)/, "the shared link names the job's library");
  assert.match(badge, /0f0e0d0c0b0a/, "and its hash");
  const download = page.waitForEvent("download", { timeout: WAIT_MS });
  await page.getByRole("button", { name: "Download CSV" }).click();
  assert.match((await download).suggestedFilename(), /^shared_matched_fr_\d{8}-\d{4}\.csv$/);
  await context.close();
}

/**
 * Asserts a replay server shows its banner, offers only its recorded library and labels timings.
 * @param {import("playwright").Browser} browser Browser.
 */
async function checkReplayLabels(browser) {
  const context = await browser.newContext({ viewport: DESKTOP });
  const page = await context.newPage();
  await installRoutes(page, newState({ mode: "replay" }));
  await page.goto(`${ORIGIN}/ui/`);
  const banner = page.locator(".mode-banner");
  await banner.waitFor({ timeout: WAIT_MS });
  assert.match((await banner.textContent()) ?? "", new RegExp(`Replay of recorded answers \\(run ${REPLAY_RUN}\\)`));
  assert.equal(await page.locator('[data-library="fr"]').isHidden(), true, "a replay offers only its library");
  await page.setInputFiles("#boq-file", BOQ_FILE);
  await page.getByRole("button", { name: "Run matching" }).click();
  await page.waitForSelector("table.results tbody tr", { timeout: WAIT_MS });
  const summary = (await page.locator("dl.summary").textContent()) ?? "";
  assert.match(summary, /Replay wall-clock \/ line \(no model called\)/);
  assert.match(summary, /recorded run, attributed/);
  assert.match((await page.locator(".run-line").textContent()) ?? "", /recorded answers of/);
  await context.close();
}

/**
 * Returns fresh mock switches.
 * @param {object} overrides Switches to change.
 * @returns {object} State.
 */
function newState(overrides = {}) {
  return { polls: 0, csvRequests: 0, queueFull: false, token: "", errors: 0, mode: "live", library: "global", csvExpired: false, uploadError: false, posts: [], ...overrides };
}

/**
 * Captures the light theme after the toggle.
 * @param {import("playwright").Page} page Page.
 */
async function checkThemeToggle(page) {
  await page.locator(".theme-toggle").click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "light");
  await shot(page, "desktop-light");
}

/**
 * Asserts the mobile layout never scrolls sideways.
 * @param {import("playwright").Page} page Page.
 */
async function checkMobile(page) {
  await page.setViewportSize(MOBILE);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `no horizontal scroll at ${MOBILE.width}px (overflow ${overflow})`);
  await shot(page, "mobile-done");
}

/**
 * Saves a screenshot when UI_SMOKE_SHOTS names a folder; otherwise does nothing.
 * @param page Page.
 * @param name File name without extension.
 */
async function shot(page, name) {
  if (!SHOTS_DIR) return;
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getTiming().iterations !== Infinity)
        .map((animation) => animation.finished),
    ),
  );
  if (SHOTS_DIR) await page.screenshot({ path: join(SHOTS_DIR, `${name}.png`), fullPage: true });
}

/** Runs the smoke against the committed build with a mocked API. */
async function main() {
  const { chromium } = await import(pathToFileURL(PLAYWRIGHT_ENTRY).href);
  const browser = await chromium.launch();
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: DESKTOP, acceptDownloads: true });
    page.on("pageerror", (error) => errors.push(error.message));
    const state = newState();
    await installRoutes(page, state);
    await runJob(page);
    await shot(page, "desktop-done");
    await checkOrderAndMarkers(page);
    await checkReviewFilter(page);
    await checkSearchDrawerDownload(page, state);
    await checkResume(page);
    await checkThemeToggle(page);
    await checkMobile(page);
    await checkQueueFull(page, state);
    await checkTokenAndPartial(page, state);
    await checkFailedLines(page, state);
    await checkExpiredDownload(page, state);
    await checkInlineUploadError(page, state);
    await checkSharedLink(browser);
    await checkReplayLabels(browser);
    assert.deepEqual(errors, [], "no page errors");
  } finally {
    await browser.close();
  }
  console.log("ui smoke: ok (input order, needs_review marker, filter, search, drawer, download, resume, theme, mobile, queue_full, token, partial reason codes, failed-lines filter and retry, expired download, inline upload error, shared link library, replay labels)");
}

await main();
