import assert from "node:assert/strict";
import test from "node:test";

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("renders the Chinese fund-flow dashboard", async () => {
  const response = await render("/");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /资金脉络/);
  assert.match(html, /今日总览/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape/);
});

test("renders the sector trend route", async () => {
  const response = await render("/trends");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /板块资金/);
  assert.match(html, /今日资金净额前 80/);
});
