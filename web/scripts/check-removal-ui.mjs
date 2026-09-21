// Drives the removal UI in a REAL browser (headless Chrome over the DevTools protocol) against a SCRATCH API.
//
//   node scripts/check-removal-ui.mjs <hashA> <hashB> <scratchResultsDir>
//
// Never point this at the real store: it clicks "Delete permanently". Setup (see the option-research-viewer
// skill): copy results/ to a scratch dir, serve the API against it on :8011 with readmodel's paths patched, and run
// the UI with NEXT_PUBLIC_API=http://127.0.0.1:8011. What it proves that curl cannot:
//   - the buttons, the confirm dialogs and the typed-hash gate work when actually clicked,
//   - a page on ANOTHER origin cannot change anything: its preflighted POST is blocked by the browser, and its
//     "simple" POST (no preflight, which CORS does not stop from being SENT) is refused by the server's guard.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [HASH, HASH2, SCRATCH] = process.argv.slice(2);
if (!HASH || !HASH2 || !SCRATCH) throw new Error("usage: check-removal-ui.mjs <hashA> <hashB> <scratchResultsDir>");
const CHROME = process.env.CHROME ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const UI = "http://127.0.0.1:3010";
const API = "http://127.0.0.1:8011";
const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? `   [${detail}]` : ""}`);
  if (!ok) fails.push(name);
};
const api = async (path) => (await fetch(API + path)).json();

const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "cdp-"))}`, "about:blank"], { stdio: "ignore" });
try {
  for (let i = 0; i < 50; i++) { try { await fetch(`http://127.0.0.1:${PORT}/json/version`); break; } catch { await sleep(200); } }
  const target = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((t) => t.type === "page");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    return r.result?.result?.value;
  };
  const waitFor = async (expr, what, ms = 8000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { try { if (await js(expr)) return true; } catch { /* page is navigating */ } await sleep(150); }
    console.log(`      (timed out waiting for: ${what})`);
    return false;
  };
  const go = async (url) => { await send("Page.navigate", { url }); await sleep(400); await waitFor("document.readyState === 'complete'", "load"); await sleep(1500); /* hydration */ };
  const click = (text) => js(`(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(text)}); if (!b) return false; b.click(); return true; })()`);
  const enabled = (text) => js(`(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(text)}); return b ? !b.disabled : null; })()`);
  const type = (sel, value) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return false;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, ${JSON.stringify(value)});
      el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
  const text = () => js("document.body.innerText");
  await send("Page.enable");

  const before = await api("/api/health");
  const N = before.n_runs;

  // ---- 1. remove flow: cancel does nothing, confirm removes --------------------------------------------------------
  await go(`${UI}/run/${HASH}`);
  check("run page shows a Remove run button", await click("Remove run"));
  check("clicking it asks first, and says nothing is deleted", await waitFor("document.body.innerText.includes('Nothing is deleted from disk')", "dialog"));
  await click("Cancel");
  check("Cancel closes the dialog and changes nothing", (await api("/api/health")).n_removed === 0 && !(await text()).includes("Nothing is deleted from disk"));
  await click("Remove run");
  await type("input[placeholder^='e.g.']", "ui test");
  await click("Remove");
  check("confirming navigates away to the study page", await waitFor("location.pathname.startsWith('/s/')", "redirect"));
  const h1 = await api("/api/health");
  check("the run is removed on the server (counts drop by one)", h1.n_removed === 1 && h1.n_runs === N - 1, JSON.stringify(h1));
  check("the reason typed in the dialog was saved", (await api("/api/removed"))[0]?.reason === "ui test");
  await go(`${UI}/run/${HASH}`);
  check("the run's page is now not found", (await text()).toLowerCase().includes("404") || (await text()).toLowerCase().includes("could not be found"));

  // ---- 2. restore -------------------------------------------------------------------------------------------------
  await go(`${UI}/removed`);
  check("the Removed runs page lists it", (await text()).includes(HASH));
  await click("Restore");
  check("Restore empties the list", await waitFor("document.body.innerText.includes('No removed runs')", "empty list"));
  const h2 = await api("/api/health");
  check("restore brings the count back", h2.n_runs === N && h2.n_removed === 0, JSON.stringify(h2));

  // ---- 3. permanent delete, with the typed-hash gate --------------------------------------------------------------
  await go(`${UI}/run/${HASH}`);
  await click("Remove run"); await sleep(300); await click("Remove");
  await waitFor("location.pathname.startsWith('/s/')", "redirect");
  await go(`${UI}/removed`);
  check("Delete permanently… opens a dialog that lists the consequences",
    (await click("Delete permanently…")) && (await waitFor("document.body.innerText.includes('timestamped backup')", "delete dialog")));
  check("the final button starts disabled", (await enabled("Delete permanently")) === false);
  await type("input.font-mono", HASH.slice(0, 11));
  check("a wrong (partial) hash keeps it disabled", (await enabled("Delete permanently")) === false);
  await type("input.font-mono", HASH);
  check("the exact hash enables it", (await enabled("Delete permanently")) === true);
  await click("Delete permanently");
  check("the run disappears from the removed list", await waitFor("document.body.innerText.includes('No removed runs')", "empty list"));
  const h3 = await api("/api/health");
  check("it is permanently gone from the store (one fewer run, none removed)", h3.n_runs === N - 1 && h3.n_removed === 0, JSON.stringify(h3));
  check("the API says 404 for it", (await fetch(`${API}/api/runs/${HASH}`)).status === 404);
  check("a backup of runs.parquet was written", readdirSync(SCRATCH).some((f) => f.startsWith("runs.parquet.bak-")));
  check("its trade log was moved, not erased", existsSync(join(SCRATCH, "trades", "_deleted", `${HASH}.parquet`)) && !existsSync(join(SCRATCH, "trades", `${HASH}.parquet`)));

  // ---- 4. another origin cannot change anything -------------------------------------------------------------------
  await go("http://localhost:8011/");            // a different origin from the UI (localhost:8011, not 127.0.0.1:3010)
  const preflighted = await js(`fetch('${API}/api/runs/${HASH2}/remove', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Viewer-Action': '1' }, body: '{}' })
      .then(() => 'sent').catch(() => 'blocked')`);
  check("a POST from another origin is BLOCKED by the browser (preflight)", preflighted === "blocked", preflighted);
  await js(`fetch('${API}/api/runs/${HASH2}/remove', { method: 'POST', mode: 'no-cors', body: 'x' }).then(() => 'sent').catch(() => 'blocked')`);
  await sleep(400);
  const h4 = await api("/api/health");
  check("even a 'simple' cross-site POST (which IS sent) changed nothing: refused by the server guard", h4.n_removed === 0 && h4.n_runs === N - 1, JSON.stringify(h4));
  ws.close();
} finally {
  chrome.kill();
}
console.log(fails.length ? `\n${fails.length} failed` : "\nall browser checks passed");
process.exit(fails.length ? 1 : 0);
