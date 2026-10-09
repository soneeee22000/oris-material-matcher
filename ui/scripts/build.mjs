import { copyFile, mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const UI_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_DIR = join(UI_DIR, "src");
const OUT_DIR = resolve(UI_DIR, "..", "src", "oris_matcher", "api", "static", "ui");
const BROWSER_TARGET = ["es2020", "chrome100", "firefox100", "safari15"];

/** Bundles the script and the stylesheet, then copies the page, into the FastAPI static folder. */
async function main() {
  await mkdir(OUT_DIR, { recursive: true });
  await build({
    entryPoints: [join(SOURCE_DIR, "main.ts")],
    outfile: join(OUT_DIR, "app.js"),
    bundle: true,
    minify: true,
    format: "iife",
    target: BROWSER_TARGET,
    legalComments: "none",
    charset: "utf8",
  });
  await build({
    entryPoints: [join(SOURCE_DIR, "app.css")],
    outfile: join(OUT_DIR, "app.css"),
    bundle: true,
    minify: true,
    target: BROWSER_TARGET,
    charset: "utf8",
  });
  await copyFile(join(SOURCE_DIR, "index.html"), join(OUT_DIR, "index.html"));
  console.log(`built ui -> ${OUT_DIR}`);
}

await main();
