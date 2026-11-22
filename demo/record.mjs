// Records a short, real demo of VINsight (no mocked UI), one video per scene:
//   1 vin      Inspector: a VIN check streaming into a cited verdict
//   2 listing  Inspector: a scam listing, X-rayed, VIN mismatch caught
//   3 scout    Scout: which model years to avoid, backed by NHTSA data
//   4 coach    Coach: an answer streaming in, then Stop
//   5 advisor  Advisor: a voice call (the caller types via the call's Type control: headless
//              browsers can't capture audio here, but Sam's voice, captions and the car brief are real)
//   6 tour     model picker, API keys, light mode
// Scenes are recorded separately with unrecorded pauses between them, so the free tier's
// per-minute token budget resets. Needs the app running (backend :8000, frontend :3000).
// Usage: pnpm record [scene ...]   then   ./make-gif.sh
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const URL = process.env.DEMO_URL ?? "http://localhost:3000";
const SIZE = { width: 1280, height: 800 };
const PAUSE_MS = Number(process.env.DEMO_PAUSE_MS ?? 65_000);
const only = process.argv.slice(2);
mkdirSync("out", { recursive: true });

async function scene(browser, name, run, extra = {}) {
  const context = await browser.newContext({ viewport: SIZE, colorScheme: "dark", recordVideo: { dir: "out/raw", size: SIZE }, ...extra });
  await context.addInitScript(() => {
    try {
      localStorage.setItem("vinsight.theme", "dark");
      localStorage.setItem("vinsight.voice", "groq");
    } catch {}
    // Hide the Next.js dev-mode badge from the recording.
    const style = document.createElement("style");
    style.textContent = "nextjs-portal{display:none!important}";
    document.addEventListener("DOMContentLoaded", () => document.head.appendChild(style));
  });
  const page = await context.newPage();
  page.on("console", (m) => m.type() === "error" && console.log(`  [${name} console] ${m.text().slice(0, 200)}`));
  page.on("pageerror", (e) => console.log(`  [${name} page error] ${String(e).slice(0, 200)}`));
  try {
    await page.goto(URL);
    await page.waitForTimeout(900);
    await run(page);
  } catch (err) {
    await context.close();
    throw err;
  }
  const video = page.video();
  await context.close(); // finishes writing the video
  await video.saveAs(`out/${name}.webm`);
  console.log(`recorded out/${name}.webm`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** One retry per scene: a free-tier hiccup shouldn't throw away the whole run. */
async function sceneWithRetry(browser, name, run, extra) {
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      return await scene(browser, name, run, extra);
    } catch (err) {
      console.log(`scene ${name} failed (attempt ${attempt}): ${String(err).split("\n")[0]}`);
      if (attempt === 2) return console.log(`skipping scene ${name}`);
      await sleep(PAUSE_MS);
    }
  }
}

const stopButton = (page) => page.getByRole("button", { name: "Stop" });

// DEMO_MODEL picks a model in the composer's picker first (e.g. "Qwen 3.8 27B" when other quotas are spent).
async function pickModel(page) {
  const label = process.env.DEMO_MODEL;
  if (!label) return;
  await page.getByRole("button", { name: /GPT-OSS 120B|Gemini|Qwen|GPT-OSS 20B/ }).first().click();
  await page.getByRole("option", { name: new RegExp(label) }).click();
  await page.waitForTimeout(300);
}

async function type(page, text) {
  await pickModel(page);
  const box = page.getByRole("textbox").first();
  await box.click();
  await box.pressSequentially(text, { delay: 22 });
  await page.waitForTimeout(250);
  await box.press("Enter");
  await stopButton(page).waitFor({ state: "visible" });
}

async function ask(page, text) {
  await type(page, text);
  // The Stop button is shown exactly while an answer is being produced.
  await stopButton(page).waitFor({ state: "detached", timeout: 150_000 });
}

async function linger(page, selector, ms = 2000) {
  const target = page.locator(selector).last();
  if (await target.count()) await target.evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "center" }));
  await page.waitForTimeout(ms);
}

const mode = (page, name) => page.getByRole("navigation", { name: "Modes" }).getByRole("button", { name });

const SCENES = {
  vin: async (page) => {
    await ask(page, "Is this car worth a look? VIN 1HGCM82633A004352");
    await linger(page, "text=/^(BUY|CAUTION|AVOID)$/");
  },
  listing: async (page) => {
    await page.locator('input[type="file"]').setInputFiles("listing.txt");
    await page.getByText(/Read ·/).first().waitFor();
    await page.waitForTimeout(400);
    await ask(page, "Is this a good deal?");
    await linger(page, "text=/^(BUY|CAUTION|AVOID)$/");
  },
  scout: async (page) => {
    await mode(page, "Scout").click();
    await page.waitForTimeout(500);
    await ask(page, "Which years of the Honda Civic should I avoid? Keep it short.");
    await page.waitForTimeout(1800);
  },
  coach: async (page) => {
    await mode(page, "Coach").click();
    await page.waitForTimeout(500);
    await type(page, "What's the difference between a clean, salvage and rebuilt title?");
    // Let the answer stream for a moment, then show the Stop button working (Groq can finish a
    // short answer in about a second, so only click if it's still going).
    await page.locator(".answer").first().waitFor({ timeout: 60_000 });
    await page.waitForTimeout(900);
    if (await stopButton(page).isVisible()) await stopButton(page).click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(1800);
  },
  advisor: async (page) => {
    await mode(page, "Advisor").click();
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: "Start call" }).click();
    // Wait for Sam's greeting caption (a question), not the voice status: on free tiers the voice
    // can fall back or be unavailable, but the captions always appear.
    await page.waitForFunction(() => /\?/.test(document.querySelector("div.fixed.inset-0")?.textContent ?? ""), null, { timeout: 60_000 });
    await page.waitForTimeout(1500);
    await page.getByRole("button", { name: "Type" }).click();
    const say = async (text) => {
      const input = page.getByPlaceholder("Type to Sam…");
      await input.pressSequentially(text, { delay: 18 });
      await input.press("Enter");
    };
    await say("About $12k. I drive 40 miles a day, mostly highway, and it snows here.");
    await page.getByText("Budget", { exact: true }).waitFor({ timeout: 60_000 }); // the note-taker filled the brief
    await page.waitForTimeout(2500);
    await say("Just me, good gas mileage, no must-haves. That's everything: please recommend two or three models now.");
    // exact: the Advisor's tagline on the page behind the call also says "shortlist".
    const shortlist = page.getByText("Shortlist", { exact: true });
    try {
      await shortlist.waitFor({ timeout: 40_000 });
    } catch {
      // Sam sometimes asks one more question first: answer it and ask again.
      await say("No preference on anything else. Which models and years should I look at?");
      await shortlist.waitFor({ timeout: 80_000 });
    }
    await page.waitForTimeout(4000); // let the NHTSA year bars fill in
    await page.getByRole("button", { name: "End call" }).click();
    await page.waitForTimeout(1500);
  },
  tour: async (page) => {
    await page.getByRole("button", { name: /GPT-OSS 120B/ }).click();
    await page.waitForTimeout(1600);
    await page.keyboard.press("Escape");
    await page.mouse.click(700, 300);
    await page.getByRole("button", { name: "API keys" }).click();
    await page.waitForTimeout(1600);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    await page.getByRole("radio", { name: "Light" }).click();
    await page.waitForTimeout(1800);
  },
};

const order = only.length ? only : Object.keys(SCENES);
const START_DELAY_MS = Number(process.env.DEMO_START_DELAY_MS ?? 0);
if (START_DELAY_MS) await sleep(START_DELAY_MS);

// Fake media flags give the call a (silent) mic so it starts normally; autoplay lets Sam's voice play.
const browser = await chromium.launch({
  args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});

let first = true;
for (const name of order) {
  const usesModel = name !== "tour";
  if (!first && usesModel) {
    console.log(`pausing ${PAUSE_MS / 1000}s (not recorded) so the free-tier per-minute budget resets…`);
    await sleep(PAUSE_MS);
  }
  await sceneWithRetry(browser, name, SCENES[name], name === "advisor" ? { permissions: ["microphone"] } : {});
  if (usesModel) first = false;
}
await browser.close();
