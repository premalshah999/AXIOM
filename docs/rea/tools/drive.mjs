// Drive the dedicated REA Chrome (port 9339 only; never the worker Chrome).
// usage: node drive.mjs <cmd> [args]
//   goto URL | click CSS | clicktext TEXT | fill CSS TEXT | type CSS TEXT [delayMs] | press CSS KEY
//   count CSS | texts CSS [max] | url | shot FILE | frames | wait MS
import { createRequire } from "node:module";
const require = createRequire("/private/tmp/claude-501/-Users-AlexQshah-Desktop-applypilot/5b02cb92-d861-4883-a359-df644dc8574e/scratchpad/rea-tool/node_modules/");
const { chromium } = require("playwright-core");
const [cmd, a, b, c] = process.argv.slice(2);
const browser = await chromium.connectOverCDP("http://127.0.0.1:9339");
const ctx = browser.contexts()[0];
const page = ctx.pages().find((p) => !p.url().startsWith("devtools")) || (await ctx.newPage());
const t0 = Date.now();
const out = (x) => console.log(typeof x === "string" ? x : JSON.stringify(x));
try {
  if (cmd === "goto") { await page.goto(a, { waitUntil: "domcontentloaded", timeout: 45000 }); await page.waitForTimeout(3000); out(page.url()); }
  else if (cmd === "click") { await page.locator(a).first().click({ timeout: 10000 }); }
  else if (cmd === "clicktext") { await page.getByText(a, { exact: false }).first().click({ timeout: 10000 }); }
  else if (cmd === "fill") { await page.locator(a).first().fill(b, { timeout: 10000 }); }
  else if (cmd === "type") { await page.locator(a).first().pressSequentially(b, { delay: Number(c || 60), timeout: 20000 }); }
  else if (cmd === "press") { await page.locator(a).first().press(b, { timeout: 10000 }); }
  else if (cmd === "count") { out(await page.locator(a).count()); }
  else if (cmd === "texts") { out((await page.locator(a).allTextContents()).map((s) => s.trim()).slice(0, Number(b || 30))); }
  else if (cmd === "url") { out(page.url()); }
  else if (cmd === "shot") { await page.screenshot({ path: a }); out(a); }
  else if (cmd === "srcs") { out(await page.evaluate(() => [...new Set([...document.scripts].map((s) => s.src).filter(Boolean).map((u) => new URL(u).origin))])); out(await page.evaluate(() => performance.getEntriesByType("resource").filter((e) => e.initiatorType === "script").map((e) => e.name.slice(0, 140)))); }
  else if (cmd === "attrs") { out(await page.locator(a).evaluateAll((els, max) => els.slice(0, max).map((e) => { const o = { tag: e.tagName.toLowerCase() }; for (const at of e.attributes) if (!["style", "value"].includes(at.name)) o[at.name] = at.value.slice(0, 80); o.portal = !e.closest("form, main"); return o; }), Number(b || 20))); }
  else if (cmd === "pickercheck") { out(await page.evaluate(() => [...document.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu]')].filter((e) => e.getClientRects().length && getComputedStyle(e).visibility !== "hidden").map((e) => ({ tag: e.tagName.toLowerCase(), role: e.getAttribute("role"), id: e.id.slice(0, 60), cls: String(e.className).slice(0, 80), label: e.getAttribute("aria-label"), h: Math.round(e.getBoundingClientRect().height), w: Math.round(e.getBoundingClientRect().width), inDropdown: !!e.closest(".ant-select-dropdown"), hiddenDropdown: !!e.closest(".ant-select-dropdown-hidden"), items: e.children.length })))); }
  else if (cmd === "optinner") { out(await page.locator(a).evaluateAll((els) => els.map((e) => ({ inner: e.innerText.trim().slice(0, 60), aria: e.getAttribute("aria-label"), html: e.innerHTML.slice(0, 160) })))); }
  else if (cmd === "frames") { out(page.frames().map((f) => f.url())); }
  else if (cmd === "wait") { await page.waitForTimeout(Number(a)); }
  else { out("unknown command"); }
} catch (e) { out("ERROR " + (e.message || e).split("\n")[0]); }
out(`(${Date.now() - t0} ms)`);
process.exit(0);
