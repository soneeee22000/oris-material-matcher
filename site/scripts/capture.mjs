import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { copyFile, mkdir, mkdtemp, rm, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const SITE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MEDIA_DIR = resolve(SITE_DIR, "..", "docs", "media");
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
const SCROLL_WIDTHS = [390, 1280, 1440];
const SCROLL_CHECK_HEIGHT = 900;
const STATION_COUNT = 8;
const GIF_FPS = 12;
const GIF_WIDTH = 960;
const FRAMES_PER_STEP = 18;
const FRAMES_FOR_RESULT = 30;
const GIF_MAX_BYTES = 6 * 1024 * 1024;
const READY_TIMEOUT_MS = 60000;
const HTTP_NOT_FOUND = 404;
const HTTP_OK = 200;
const EXPECTED_FILES = ["hero.png", "results.png", "mobile-hero.png", "pipeline.gif"];
const HIDE_STICKY_NAV = ".nav { visibility: hidden !important; }";
const GL_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};

/**
 * Maps a request URL onto a file inside the site directory, refusing traversal.
 * @param {string} url Request URL.
 * @returns {string | null} Absolute file path, or null when outside the site.
 */
function resolveRequest(url) {
  const pathname = decodeURIComponent(new URL(url, "http://localhost").pathname);
  const relative = pathname.endsWith("/") ? `${pathname}index.html` : pathname;
  const target = normalize(join(SITE_DIR, relative));
  return target.startsWith(SITE_DIR + sep) ? target : null;
}

/**
 * Starts a static file server for the site on a free port.
 * @returns {Promise<{server: import("node:http").Server, origin: string}>}
 */
function startServer() {
  const server = createServer(async (request, response) => {
    const file = resolveRequest(request.url ?? "/");
    const info = file ? await stat(file).catch(() => null) : null;
    if (!file || !info?.isFile()) {
      response.writeHead(HTTP_NOT_FOUND).end("not found");
      return;
    }
    const type = MIME[extname(file)] ?? "application/octet-stream";
    response.writeHead(HTTP_OK, { "content-type": type });
    createReadStream(file).pipe(response);
  });
  return new Promise((done) => {
    server.listen(0, "127.0.0.1", () => {
      done({ server, origin: `http://127.0.0.1:${server.address().port}` });
    });
  });
}

/**
 * Opens the page at a viewport, records console errors and waits until it is ready.
 * @param {import("playwright").Browser} browser Browser.
 * @param {string} origin Server origin.
 * @param {{width: number, height: number}} viewport Viewport size.
 * @param {string[]} errors Collector for console and page errors.
 * @returns {Promise<import("playwright").Page>}
 */
async function openPage(browser, origin, viewport, errors) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  const label = `${viewport.width}x${viewport.height}`;
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`[${label}] console: ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`[${label}] pageerror: ${error.message}`));
  await page.goto(`${origin}/index.html`, { waitUntil: "load" });
  await page.waitForFunction(() => window.__pageReady === true, null, {
    timeout: READY_TIMEOUT_MS,
  });
  await page.evaluate(() => document.fonts.ready);
  await settle(page);
  return page;
}

/**
 * Waits for two animation frames so the latest state has been painted.
 * @param {import("playwright").Page} page Page.
 */
async function settle(page) {
  await page.evaluate(
    () =>
      new Promise((done) =>
        requestAnimationFrame(() => requestAnimationFrame(() => done(undefined))),
      ),
  );
}

/**
 * Reads the document scroll and client widths.
 * @param {import("playwright").Page} page Page.
 * @returns {Promise<{scrollWidth: number, clientWidth: number}>}
 */
function measureWidths(page) {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
}

/**
 * Checks for horizontal page scroll at each audited width.
 * @returns {Promise<string[]>} One line per width; failures are prefixed FAIL.
 */
async function checkScroll(browser, origin, errors) {
  const lines = [];
  for (const width of SCROLL_WIDTHS) {
    const page = await openPage(browser, origin, { width, height: SCROLL_CHECK_HEIGHT }, errors);
    const { scrollWidth, clientWidth } = await measureWidths(page);
    const verdict = scrollWidth <= clientWidth ? "ok" : "FAIL";
    lines.push(`${verdict} width ${width}: scrollWidth ${scrollWidth}, clientWidth ${clientWidth}`);
    await page.close();
  }
  return lines;
}

/**
 * Captures the desktop hero, the Results chapter and the mobile hero.
 */
async function captureStills(browser, origin, errors) {
  const desktop = await openPage(browser, origin, DESKTOP, errors);
  await desktop.screenshot({ path: join(MEDIA_DIR, "hero.png") });
  const results = desktop.locator("#results");
  await results.scrollIntoViewIfNeeded();
  await settle(desktop);
  await results.screenshot({ path: join(MEDIA_DIR, "results.png"), style: HIDE_STICKY_NAV });
  await desktop.close();
  const mobile = await openPage(browser, origin, MOBILE, errors);
  await mobile.screenshot({ path: join(MEDIA_DIR, "mobile-hero.png") });
  await mobile.close();
}

/**
 * Writes one screenshot of the pipeline stage, then duplicates it to hold the shot.
 * @returns {Promise<number>} Next free frame index.
 */
async function holdFrame(stage, framesDir, startIndex, count) {
  const first = join(framesDir, frameName(startIndex));
  await stage.screenshot({ path: first, style: HIDE_STICKY_NAV });
  for (let offset = 1; offset < count; offset += 1) {
    await copyFile(first, join(framesDir, frameName(startIndex + offset)));
  }
  return startIndex + count;
}

/**
 * @param {number} index Frame index.
 * @returns {string} Zero-padded frame file name.
 */
function frameName(index) {
  return `frame-${String(index).padStart(4, "0")}.png`;
}

/**
 * Steps the pipeline through every station, then the final result, saving PNG frames.
 */
async function capturePipelineFrames(browser, origin, errors, framesDir) {
  const page = await openPage(browser, origin, DESKTOP, errors);
  const stage = page.locator("#pipe");
  await stage.scrollIntoViewIfNeeded();
  let next = 0;
  for (let step = 0; step < STATION_COUNT; step += 1) {
    await page.evaluate((value) => window.__setPipelineStep(value), step);
    await settle(page);
    next = await holdFrame(stage, framesDir, next, FRAMES_PER_STEP);
  }
  await page.locator("#p-end").click();
  await settle(page);
  await holdFrame(stage, framesDir, next, FRAMES_FOR_RESULT);
  await page.close();
}

/**
 * Runs ffmpeg and rejects on a non-zero exit.
 * @param {string[]} args ffmpeg arguments.
 */
function runFfmpeg(args) {
  return new Promise((done, fail) => {
    const child = spawn("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], {
      stdio: ["ignore", "inherit", "inherit"],
    });
    child.on("error", fail);
    child.on("close", (code) => (code === 0 ? done(undefined) : fail(new Error(`ffmpeg exited ${code}`))));
  });
}

/**
 * Encodes the frames to pipeline.gif with a two-pass palette.
 * @param {string} framesDir Directory holding frame-NNNN.png.
 */
async function encodeGif(framesDir) {
  const input = ["-framerate", String(GIF_FPS), "-i", join(framesDir, "frame-%04d.png")];
  const scale = `fps=${GIF_FPS},scale=${GIF_WIDTH}:-1:flags=lanczos`;
  const palette = join(framesDir, "palette.png");
  await runFfmpeg([...input, "-vf", `${scale},palettegen=stats_mode=diff`, palette]);
  await runFfmpeg([
    ...input,
    "-i",
    palette,
    "-lavfi",
    `${scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle`,
    "-loop",
    "0",
    join(MEDIA_DIR, "pipeline.gif"),
  ]);
}

/**
 * Confirms every expected file exists and the GIF is within budget.
 * @returns {Promise<string[]>} One line per file with its size.
 */
async function verifyOutputs() {
  const lines = [];
  for (const name of EXPECTED_FILES) {
    const info = await stat(join(MEDIA_DIR, name)).catch(() => null);
    if (!info?.isFile() || info.size === 0) throw new Error(`missing output: ${name}`);
    if (name.endsWith(".gif") && info.size > GIF_MAX_BYTES) {
      throw new Error(`${name} is ${info.size} bytes, over ${GIF_MAX_BYTES}`);
    }
    lines.push(`${name}: ${info.size} bytes`);
  }
  return lines;
}

/**
 * Runs every capture, then fails on console errors, horizontal scroll or missing files.
 */
async function main() {
  await mkdir(MEDIA_DIR, { recursive: true });
  const framesDir = await mkdtemp(join(tmpdir(), "oris-frames-"));
  const { server, origin } = await startServer();
  const browser = await chromium.launch({ args: GL_ARGS });
  const errors = [];
  try {
    const scroll = await checkScroll(browser, origin, errors);
    await captureStills(browser, origin, errors);
    await capturePipelineFrames(browser, origin, errors, framesDir);
    await encodeGif(framesDir);
    const outputs = await verifyOutputs();
    report(scroll, outputs, errors);
  } finally {
    await browser.close();
    server.close();
    await rm(framesDir, { recursive: true, force: true });
  }
}

/**
 * Prints the checks and sets a failing exit code when any check failed.
 */
function report(scroll, outputs, errors) {
  console.log(["Scroll checks:", ...scroll, "Outputs:", ...outputs].join("\n"));
  console.log(errors.length ? ["Console errors:", ...errors].join("\n") : "Console errors: none");
  if (errors.length || scroll.some((line) => line.startsWith("FAIL"))) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
