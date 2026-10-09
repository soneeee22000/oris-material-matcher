import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const SITE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MEDIA_DIR = resolve(SITE_DIR, "..", "docs", "media");
const TRANSCRIPT_DIR = join(MEDIA_DIR, "transcripts");
const TRANSCRIPTS = ["oris-demo-en.txt", "oris-explain-03.01.0020.txt"];
const OUTPUT_GIF = join(MEDIA_DIR, "terminal-replay.gif");
const CAPTION = "Replay of a real local run - 2026-10-09 - $0, no API key";
const GIF_WIDTH = 960;
const GIF_FPS = 12;
const GIF_MAX_BYTES = 6 * 1024 * 1024;
const MIN_VIEWPORT_HEIGHT = 360;
const CHARS_PER_FRAME = 3;
const FRAMES_PER_LINE = 2;
const FRAMES_BEFORE_OUTPUT = 6;
const FRAMES_HOLD_SCREEN = 60;
const FRAMES_HOLD_FINAL = 84;
const FONT_TIMEOUT_MS = 15000;
const FONT_FAMILY = "IBM Plex Mono";
const FONT_CSS =
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=block";
const KEEP_FRAMES_DIR = process.env.REPLAY_KEEP_FRAMES ?? "";

const PAGE_STYLE = `
:root { --bg: #0e1013; --bar: #181b20; --fg: #d7dbe0; --dim: #7d8692; --prompt: #8fd19e; --cmd: #f2f4f6; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: var(--bg); width: ${GIF_WIDTH}px; }
body { font-family: "${FONT_FAMILY}", Consolas, monospace; color: var(--fg); }
.bar { display: flex; align-items: center; gap: 12px; padding: 10px 18px; background: var(--bar);
  color: var(--dim); font-size: 13px; letter-spacing: 0.01em; border-bottom: 1px solid #262a31; }
.dots { display: flex; gap: 6px; } .dots i { width: 10px; height: 10px; border-radius: 50%; background: #3a3f47; }
.term { padding: 16px 20px 20px; font-size: 14px; line-height: 1.5; white-space: pre-wrap;
  overflow-wrap: anywhere; }
.line { min-height: 1.5em; } .cmd { color: var(--cmd); font-weight: 600; } .ps { color: var(--prompt); }
.comment { color: var(--dim); }
.cursor { display: inline-block; width: 0.6em; height: 1.1em; vertical-align: -0.15em; background: var(--fg); }
`;

const PAGE_SCRIPT = `
/**
 * Renders one replay step into the terminal element.
 * @param {{screen:number, typed:number, shown:number}} step Step to render.
 */
function renderStep(step) {
  const screen = window.__replayScreens[step.screen];
  const term = document.getElementById("term");
  term.replaceChildren();
  const command = document.createElement("div");
  command.className = "line cmd";
  const prompt = document.createElement("span");
  prompt.className = "ps";
  prompt.textContent = "$ ";
  command.append(prompt, screen.command.slice(0, step.typed));
  term.append(command);
  screen.output.slice(0, step.shown).forEach((text) => {
    const row = document.createElement("div");
    row.className = text.startsWith("# ") ? "line comment" : "line";
    row.textContent = text;
    term.append(row);
  });
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  term.lastChild.append(cursor);
}

/**
 * Shows the replay at a fraction of its length, so every frame is deterministic.
 * @param {number} progress Value from 0 to 1.
 */
window.__setReplayProgress = (progress) => {
  const steps = window.__replaySteps;
  const clamped = Math.min(1, Math.max(0, progress));
  renderStep(steps[Math.round(clamped * (steps.length - 1))]);
};
`;

/**
 * Splits a transcript into the typed command and the lines printed after it.
 * @param {string} text Transcript starting with "$ <command>".
 * @returns {{command: string, output: string[]}} Parsed screen.
 */
function parseTranscript(text) {
  const lines = text.replace(/\r/g, "").replace(/\n+$/, "").split("\n");
  if (!lines[0].startsWith("$ ")) throw new Error("transcript must start with '$ <command>'");
  return { command: lines[0].slice(2), output: lines.slice(1) };
}

/**
 * @returns {Promise<{command: string, output: string[]}[]>} Every transcript, in replay order.
 */
async function loadScreens() {
  const texts = await Promise.all(
    TRANSCRIPTS.map((name) => readFile(join(TRANSCRIPT_DIR, name), "utf8")),
  );
  return texts.map(parseTranscript);
}

/**
 * Repeats a step a number of times.
 * @returns {object[]} The repeated steps.
 */
function hold(step, count) {
  return Array.from({ length: count }, () => ({ ...step }));
}

/**
 * Builds the frame-by-frame steps of one screen: type the command, then print its lines.
 * @param {{command: string, output: string[]}} screen Screen to play.
 * @param {number} index Screen index.
 * @param {number} holdFrames Frames to hold the finished screen.
 * @returns {object[]} One step per frame.
 */
function screenSteps(screen, index, holdFrames) {
  const steps = [];
  for (let typed = 0; typed < screen.command.length; typed += CHARS_PER_FRAME) {
    steps.push({ screen: index, typed, shown: 0 });
  }
  const typedAll = screen.command.length;
  steps.push(...hold({ screen: index, typed: typedAll, shown: 0 }, FRAMES_BEFORE_OUTPUT));
  for (let shown = 1; shown <= screen.output.length; shown += 1) {
    steps.push(...hold({ screen: index, typed: typedAll, shown }, FRAMES_PER_LINE));
  }
  const last = { screen: index, typed: typedAll, shown: screen.output.length };
  return [...steps, ...hold(last, holdFrames)];
}

/**
 * @returns {object[]} Steps for every screen, holding the last one longer.
 */
function buildSteps(screens) {
  return screens.flatMap((screen, index) =>
    screenSteps(screen, index, index === screens.length - 1 ? FRAMES_HOLD_FINAL : FRAMES_HOLD_SCREEN),
  );
}

/**
 * @returns {string} The terminal page, with the caption bar and an empty terminal.
 */
function pageHtml() {
  return `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="${FONT_CSS}"><style>${PAGE_STYLE}</style></head>
<body><div class="bar"><span class="dots"><i></i><i></i><i></i></span><span>${CAPTION}</span></div>
<div class="term" id="term"></div></body></html>`;
}

/**
 * Loads the terminal page and installs the replay data and setter.
 * @returns {Promise<{page: import("playwright").Page, fontLoaded: boolean}>} Ready page.
 */
async function openTerminal(browser, screens, steps) {
  const page = await browser.newPage({ viewport: { width: GIF_WIDTH, height: MIN_VIEWPORT_HEIGHT } });
  await page.setContent(pageHtml(), { waitUntil: "load", timeout: FONT_TIMEOUT_MS }).catch(() => null);
  await page.evaluate(
    ({ screensData, stepsData }) => {
      window.__replayScreens = screensData;
      window.__replaySteps = stepsData;
    },
    { screensData: screens, stepsData: steps },
  );
  await page.addScriptTag({ content: PAGE_SCRIPT });
  const fontLoaded = await page.evaluate(async (family) => {
    await document.fonts.ready;
    return document.fonts.check(`14px "${family}"`);
  }, FONT_FAMILY);
  return { page, fontLoaded };
}

/**
 * Sizes the viewport to the tallest finished screen, so no frame clips text.
 * @returns {Promise<number>} Viewport height in pixels.
 */
async function fitViewport(page, steps) {
  const lastIndexes = steps.flatMap((step, index) =>
    index === steps.length - 1 || steps[index + 1].screen !== step.screen ? [index] : [],
  );
  let height = MIN_VIEWPORT_HEIGHT;
  for (const index of lastIndexes) {
    await page.evaluate((value) => window.__setReplayProgress(value), index / (steps.length - 1));
    height = Math.max(height, await page.evaluate(() => document.body.scrollHeight));
  }
  await page.setViewportSize({ width: GIF_WIDTH, height });
  return height;
}

/**
 * @param {number} index Frame index.
 * @returns {string} Zero-padded frame file name.
 */
function frameName(index) {
  return `frame-${String(index).padStart(4, "0")}.png`;
}

/**
 * Screenshots one frame per step.
 * @param {import("playwright").Page} page Terminal page.
 * @param {number} count Number of steps.
 * @param {string} framesDir Output directory.
 */
async function captureFrames(page, count, framesDir) {
  for (let index = 0; index < count; index += 1) {
    await page.evaluate((value) => window.__setReplayProgress(value), index / (count - 1));
    await page.screenshot({ path: join(framesDir, frameName(index)) });
  }
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
 * Encodes the frames to terminal-replay.gif with a two-pass palette.
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
    `${scale}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle`,
    "-loop",
    "0",
    OUTPUT_GIF,
  ]);
}

/**
 * Confirms the GIF exists and is within budget.
 * @returns {Promise<number>} GIF size in bytes.
 */
async function verifyGif() {
  const info = await stat(OUTPUT_GIF).catch(() => null);
  if (!info?.isFile() || info.size === 0) throw new Error(`missing output: ${OUTPUT_GIF}`);
  if (info.size > GIF_MAX_BYTES) throw new Error(`GIF is ${info.size} bytes, over ${GIF_MAX_BYTES}`);
  return info.size;
}

/**
 * Builds the replay, captures its frames, encodes and checks the GIF.
 */
async function main() {
  const screens = await loadScreens();
  const steps = buildSteps(screens);
  const framesDir = KEEP_FRAMES_DIR || (await mkdtemp(join(tmpdir(), "oris-replay-")));
  await mkdir(framesDir, { recursive: true });
  const browser = await chromium.launch();
  try {
    const { page, fontLoaded } = await openTerminal(browser, screens, steps);
    const height = await fitViewport(page, steps);
    await captureFrames(page, steps.length, framesDir);
    await encodeGif(framesDir);
    const size = await verifyGif();
    console.log(`terminal-replay.gif: ${size} bytes, ${steps.length} frames, ${GIF_WIDTH}x${height}`);
    console.log(`font: ${fontLoaded ? FONT_FAMILY : "fallback monospace"}`);
  } finally {
    await browser.close();
    if (!KEEP_FRAMES_DIR) await rm(framesDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
