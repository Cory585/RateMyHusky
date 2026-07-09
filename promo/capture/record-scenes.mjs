// Records one WebM per scene off the live site with the fake cursor
// injected, transcodes to H.264 MP4 for Remotion, and writes trim
// markers to ../src/manifest.json.
// Usage (from promo/): node capture/record-scenes.mjs [scene ...]
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

export const BASE = 'https://www.ratemyhusky.com';
const RAW = 'capture/raw';
const CLIPS = 'public/clips';
const MANIFEST = fileURLToPath(new URL('../src/manifest.json', import.meta.url));
const cursorSrc = readFileSync(
  fileURLToPath(new URL('./cursor.js', import.meta.url)),
  'utf8'
);

export async function newScene(browser) {
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: RAW, size: { width: 1920, height: 1080 } },
  });
  await context.addInitScript(cursorSrc);
  await context.addInitScript(() => {
    localStorage.setItem('prof_course_tip_dismissed', '1');
  });
  const page = await context.newPage();
  return { context, page, rec0: Date.now() };
}

// Seconds since the recording (page) started — used for trim markers.
export const mark = (scene) => (Date.now() - scene.rec0) / 1000;

export async function glideClick(page, selector, ms = 700) {
  const el = page.locator(selector).first();
  await el.waitFor({ state: 'visible' });
  const box = await el.boundingBox();
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.evaluate(
    ([x, y, ms]) => window.__cursor.moveTo(x, y, ms),
    [x, y, ms]
  );
  await page.evaluate(() => window.__cursor.pulse());
  await page.mouse.click(x, y);
}

export function probeDuration(file) {
  return parseFloat(
    execFileSync('ffprobe', [
      '-v', 'error',
      '-show_entries', 'format=duration',
      '-of', 'csv=p=0',
      file,
    ]).toString()
  );
}

function transcode(src, dst) {
  execFileSync(
    'ffmpeg',
    ['-y', '-i', src, '-r', '30', '-c:v', 'libx264', '-crf', '18',
     '-pix_fmt', 'yuv420p', '-an', dst],
    { stdio: 'inherit' }
  );
}

export async function saveScene(scene, name, markers = {}) {
  const video = scene.page.video();
  await scene.context.close(); // flushes the recording to disk
  await video.saveAs(`${RAW}/${name}.webm`);
  transcode(`${RAW}/${name}.webm`, `${CLIPS}/${name}.mp4`);
  return { file: `clips/${name}.mp4`, markers };
}

/* ---------------- scenes ---------------- */

async function sceneSmoke(browser) {
  const s = await newScene(browser);
  const { page } = s;
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.evaluate(() => window.__cursor.show());
  await page.waitForTimeout(500);
  await page.evaluate(() => window.__cursor.moveTo(400, 300, 900));
  await page.evaluate(() => window.__cursor.moveTo(1400, 700, 900));
  await page.evaluate(() => window.__cursor.scrollTo(600, 1000));
  await page.waitForTimeout(500);
  return saveScene(s, 'smoke');
}

export const SCENE_FNS = {
  smoke: sceneSmoke,
};

/* ---------------- CLI ---------------- */

async function main() {
  const names = process.argv.slice(2).length
    ? process.argv.slice(2)
    : Object.keys(SCENE_FNS);
  for (const n of names) {
    if (!SCENE_FNS[n]) throw new Error(`unknown scene: ${n}`);
  }
  mkdirSync(RAW, { recursive: true });
  mkdirSync(CLIPS, { recursive: true });
  const browser = await chromium.launch();
  // Warm up the backend so the first scene doesn't record a cold start.
  const warm = await browser.newPage();
  await warm.goto(`${BASE}/api/stats`, { timeout: 60000 });
  await warm.close();
  let manifest = { scenes: {} };
  try {
    manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
  } catch {}
  for (const name of names) {
    console.log(`recording scene: ${name}`);
    const entry = await SCENE_FNS[name](browser);
    entry.durationSec = probeDuration(`${CLIPS}/${name}.mp4`);
    manifest.scenes[name] = entry;
    writeFileSync(MANIFEST, JSON.stringify(manifest, null, 2));
    console.log(`  -> ${entry.file} (${entry.durationSec.toFixed(1)}s)`);
  }
  await browser.close();
}

main();
