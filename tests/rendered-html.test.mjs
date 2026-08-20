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

test("renders the stock anomaly route", async () => {
  const response = await render("/stocks");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /一分钟异动/);
  assert.match(html, /正在核验个股分钟行情/);
});

test("renders a dynamic stock detail route", async () => {
  const response = await render("/stocks/000001?market=0&name=%E5%B9%B3%E5%AE%89%E9%93%B6%E8%A1%8C");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /返回个股异动/);
});

test("renders the composable quantitative strategy laboratory", async () => {
  const response = await render("/strategy");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /可验证的策略/);
  assert.match(html, /模型可以逆势买入/);
  assert.match(html, /策略实验室/);
  assert.match(html, /data-fund-flow-version="10"/);
});
