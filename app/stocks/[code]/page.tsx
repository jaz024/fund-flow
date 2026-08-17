"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import StockPriceChart from "../../components/StockPriceChart";
import type { StockDetailData } from "../../lib/types";
import { fetchJson, formatPercent, formatWanYi } from "../../lib/types";

function priceOrDash(value: number | undefined): string {
  return value && value > 0 ? value.toFixed(2) : "--";
}

function amountOrDash(value: number | undefined): string {
  return value && value > 0 ? formatWanYi(value) : "--";
}

export default function StockDetailPage() {
  const params = useParams<{ code: string }>();
  const searchParams = useSearchParams();
  const code = decodeURIComponent(params.code || "");
  const market = searchParams.get("market") || "";
  const fallbackName = searchParams.get("name") || code;
  const [mode, setMode] = useState<"intraday" | "daily">("intraday");
  const [data, setData] = useState<StockDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ code, market, name: fallbackName });
      if (force) query.set("refresh", "1");
      const result = await fetchJson<StockDetailData>(`/api/stocks/detail?${query}`);
      setData(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "个股详情读取失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [code, fallbackName, market]);

  useEffect(() => {
    const initial = window.setTimeout(() => { void load(false); }, 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => { if (mode === "intraday") void load(false); }, 60_000);
    return () => window.clearInterval(timer);
  }, [load, mode]);

  const quote = data?.quote;
  const up = (quote?.changePct || 0) >= 0;

  return (
    <main className="app-shell stock-detail-page">
      <header className="topbar">
        <Link className="brand" href="/"><span className="brand-mark"><i /><i /><i /></span><span><strong>资金脉络</strong><small>A股市场观察台</small></span></Link>
        <nav aria-label="主导航"><Link href="/">今日总览</Link><Link href="/trends">板块趋势</Link><Link className="active" href="/stocks">个股异动</Link><Link href="/strategy">策略模拟</Link></nav>
        <Link className="back-link" href="/stocks">← 返回个股异动</Link>
      </header>

      <section className="stock-detail-hero">
        <div><span className="date-kicker">{quote?.code || code} · {quote?.market === 1 ? "沪市" : code.startsWith("92") ? "北交所" : "深市"}</span><h1>{quote?.name || fallbackName}</h1><p>{data?.source || "正在连接公开行情"} · 已核验至 {data?.verifiedThrough || "--:--"}</p></div>
        <div className="quote-price"><strong className={up ? "up" : "down"}>{quote?.price.toFixed(2) || "--"}</strong><span className={up ? "up" : "down"}>{quote ? formatPercent(quote.changePct) : "--"}</span><button className="refresh-button" type="button" disabled={refreshing} onClick={() => load(true)}><span className={refreshing ? "spin" : ""}>↻</span>{refreshing ? "核验中" : "刷新"}</button></div>
      </section>

      {error && <div className="error-banner"><div><strong>个股详情尚未就绪</strong><span>{error}</span></div><button type="button" onClick={() => load(true)}>重新核验</button></div>}
      {data?.warning && <div className="warning-banner"><strong>{data.isStale ? "最近已保存数据" : "数据提示"}</strong>{data.warning}</div>}

      {loading && !data ? (
        <div className="loading-panel"><span className="loading-orbit" /><strong>正在读取 {fallbackName}</strong><p>核验今日分钟成交和近三个月复权日线…</p></div>
      ) : data && quote && (
        <>
          <section className="quote-stat-grid">
            <article><span>LAST PRICE · 最新价</span><strong>{priceOrDash(quote.price)}</strong><small>昨收 {priceOrDash(quote.preClose)}</small></article>
            <article><span>BID · 买一</span><strong>{priceOrDash(quote.bidPrice)}</strong><small>委买 {amountOrDash(quote.bidVolume)} 股</small></article>
            <article><span>ASK · 卖一</span><strong>{priceOrDash(quote.askPrice)}</strong><small>委卖 {amountOrDash(quote.askVolume)} 股</small></article>
            <article><span>OPEN · 今开</span><strong>{priceOrDash(quote.open)}</strong><small>涨跌 {formatPercent(quote.changePct)}</small></article>
            <article><span>CLOSE · 最新收盘</span><strong>{priceOrDash(quote.close)}</strong><small>{data.daily.at(-1)?.date || "--"}</small></article>
            <article><span>DAY'S RANGE · 日内区间</span><strong>{priceOrDash(quote.low)}—{priceOrDash(quote.high)}</strong><small>最低—最高</small></article>
            <article><span>VOLUME · 成交量</span><strong>{amountOrDash(quote.volume)}</strong><small>股</small></article>
            <article><span>AMOUNT · 成交额</span><strong>{amountOrDash(quote.amount)}</strong><small>换手 {quote.turnover ? `${quote.turnover.toFixed(2)}%` : "--"}</small></article>
            <article><span>VALUATION · 估值</span><strong>PE {quote.pe ? quote.pe.toFixed(2) : "--"}</strong><small>PB {quote.pb ? quote.pb.toFixed(2) : "--"}</small></article>
            <article><span>MARKET CAP · 市值</span><strong>{amountOrDash(quote.marketCap)}</strong><small>流通 {amountOrDash(quote.floatMarketCap)}</small></article>
          </section>

          <div className="verification-source-list"><strong>真实数据核验</strong>{data.verifiedSources?.join(" · ") || data.source}{data.verificationNotes?.length ? `；${data.verificationNotes.join("；")}` : ""}</div>

          <section className="feature-panel stock-detail-chart-panel">
            <div className="stock-chart-heading">
              <div><span className="eyebrow">PRICE HISTORY</span><h2>{mode === "intraday" ? "今日分时价格" : "近三个月价格趋势"}</h2><p>{mode === "intraday" ? "成交价与行情源提供或由累计成交额/量精确计算的当日均价。" : `${data.dailyAdjustment || "真实"}日收盘价及 5 日、20 日滚动均线。`}</p></div>
              <div className="chart-mode-switch"><button type="button" className={mode === "intraday" ? "active" : ""} onClick={() => setMode("intraday")}>今日分时</button><button type="button" className={mode === "daily" ? "active" : ""} onClick={() => setMode("daily")}>近三个月</button></div>
            </div>
            {mode === "intraday" ? <StockPriceChart mode="intraday" intraday={data.intraday} /> : <StockPriceChart mode="daily" daily={data.daily} />}
          </section>

          <div className="stock-method-note"><strong>如何阅读</strong><p>“今日分时”只绘制行情源已经返回的分钟点，午间休市不插值；近三个月使用同一行情源、同一复权口径，不混合不同来源的历史序列。均线只反映历史价格统计，不是预测。</p></div>
        </>
      )}
      <footer><span>资金脉络 · 本地版</span><p>公开行情数据仅供信息展示，不构成任何投资建议。</p></footer>
    </main>
  );
}
