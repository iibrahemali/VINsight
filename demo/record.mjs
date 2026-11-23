// Records a short, real demo of VINsight (no mocked UI), one video per scene:
//   1 vin      Inspector: a VIN check, reasoning and tool steps streaming into a cited verdict
//   2 listing  Inspector: a scam listing attached as a PDF, X-rayed, VIN mismatch caught
//   3 scout    Scout: a shortlist with years to avoid, EPA running costs and clickable sources
//   4 coach    Coach: reads a linked web page and answers from it, citing it
//   5 advisor  Advisor: a voice call (the caller types via the call's Type control: headless
//              browsers can't capture audio here, but Sam's voice, captions and the car brief are real)
//   6 tour     New chat, Stop mid-answer, model picker, API keys, light mode
// Scenes are recorded separately with unrecorded pauses between them, so the free tier's
// per-minute token budget resets. A scene that shows a rate-limit, model-switch, pacing or error
// message anywhere is thrown away and retried: the demo only ever shows clean runs.
// Needs the app running (backend :8000, frontend :3000).
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
  // Watch the page while the scene runs: any of these, even briefly, spoils the take.
  const seen = new Set();
  const watch = setInterval(async () => {
    const text = await page.locator("body").innerText().catch(() => "");
    for (const m of text.matchAll(BLEMISHES)) seen.add(m[0]);
  }, 400);
  try {
    await page.goto(URL);
    await page.waitForTimeout(900);
    await run(page);
    // Raw citations: checked on the finished answers (mid-stream, a citation's "]" may not be there yet).
    const answers = (await page.locator(".answer").allInnerTexts().catch(() => [])).join("\n");
    for (const m of answers.matchAll(RAW_CITES)) seen.add(m[0]);
    if (seen.size) throw new Error(`blemish on screen: ${[...seen].join(" | ")}`);
  } catch (err) {
    clearInterval(watch);
    await context.close();
    throw err;
  }
  clearInterval(watch);
  const video = page.video();
  await context.close(); // finishes writing the video
  await video.saveAs(`out/${name}.webm`);
  console.log(`recorded out/${name}.webm`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const BLEMISHES =
  /[^\n]*(free-tier limit|free quota|over its free|Switched to|busy this minute|busy right now|Pacing to|couldn't answer|took too long|not in tool results|No voice available|voice hit its|Couldn't update|blocked bots|Couldn't read|Error executing|Every model)[^\n]*/gi;
// Citations the answer renderer didn't turn into numbered sources (checked in answers only: the
// model's reasoning panel legitimately mentions the citation format).
const RAW_CITES = /[^\n]*(【|\[(?:recall|complaints?|years|vpic|epa|doc|link):|\bopen recalls?\b)[^\n]*/gi;

/** One retry per scene: a free-tier hiccup shouldn't throw away the whole run. */
async function sceneWithRetry(browser, name, run, extra) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      return await scene(browser, name, run, extra);
    } catch (err) {
      console.log(`scene ${name} failed (attempt ${attempt}): ${String(err).split("\n")[0]}`);
      if (attempt === 3) return console.log(`skipping scene ${name}`);
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
    await linger(page, "text=Sources", 2500);
  },
  listing: async (page) => {
    await page.locator('input[type="file"]').setInputFiles("listing.pdf");
    await page.getByText(/Read ·/).first().waitFor();
    await page.waitForTimeout(600);
    await ask(page, "Is this a good deal?");
    await linger(page, "text=Sources", 2500);
  },
  scout: async (page) => {
    await mode(page, "Scout").click();
    await page.waitForTimeout(500);
    await ask(page, "About $12k, 40 highway miles a day, it snows, just me. Gas only, good mileage, no must-haves. What should I look at?");
    // Scroll through the shortlist, running-costs table and Sources, then hover a source to show its link.
    await page.getByText("Sources", { exact: true }).waitFor({ timeout: 90_000 });
    await page.waitForTimeout(1500);
    const source = page.locator('li[id^="src-"] a').last();
    await source.scrollIntoViewIfNeeded();
    await source.hover();
    await page.waitForTimeout(2200);
  },
  coach: async (page) => {
    await mode(page, "Coach").click();
    await page.waitForTimeout(500);
    // Links: attach a web page and ask about it; the answer cites the page.
    await page.getByRole("button", { name: "Add link" }).click();
    await page.getByPlaceholder(/Paste a listing link/).pressSequentially("https://en.wikipedia.org/wiki/Vehicle_title_branding", { delay: 12 });
    await page.keyboard.press("Enter");
    await page.getByText(/Read ·/).first().waitFor({ timeout: 20_000 });
    await page.waitForTimeout(600);
    await ask(page, "Using this page, what's the difference between a salvage and a rebuilt title? Keep it short.");
    await linger(page, "text=Sources", 2500);
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
    // Let the NHTSA year checks finish and their results show on the cards.
    await page.getByText(/Checking NHTSA/).first().waitFor({ state: "detached", timeout: 30_000 }).catch(() => {});
    await page.waitForTimeout(2500);
    await page.getByRole("button", { name: "End call" }).click();
    await page.waitForTimeout(1500);
  },
  tour: async (page) => {
    // Stop: start an answer and stop it mid-stream.
    await mode(page, "Coach").click();
    await page.waitForTimeout(400);
    await type(page, "Walk me through buying my first used car, step by step.");
    await page.locator(".answer").first().waitFor({ timeout: 60_000 });
    await page.waitForTimeout(700);
    if (await stopButton(page).isVisible()) await stopButton(page).click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await page.getByRole("button", { name: "New chat" }).click();
    await page.waitForTimeout(800);
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
  const usesModel = true; // every scene asks a model something now (the tour shows Stop)
  if (!first && usesModel) {
    console.log(`pausing ${PAUSE_MS / 1000}s (not recorded) so the free-tier per-minute budget resets…`);
    await sleep(PAUSE_MS);
  }
  await sceneWithRetry(browser, name, SCENES[name], name === "advisor" ? { permissions: ["microphone"] } : {});
  if (usesModel) first = false;
}
await browser.close();
