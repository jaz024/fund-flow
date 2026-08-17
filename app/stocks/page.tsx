"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import MarketAnomalyChart from "../components/MarketAnomalyChart";
import type { StockMarketData, StockRankItem } from "../lib/types";
import { fetchJson, formatPercent, shortDate } from "../lib/types";

function stockHref(item: StockRankItem): string {
  return `/stocks/${item.code}?market=${item.market}&name=${encodeURIComponent(item.name)}`;
}

function RankingCard({ title, eyebrow, items, metric }: {
  title: string;
  eyebrow: string;
  items: StockRankItem[];
  metric: "speed" | "turnover";
}) {
  return (
    <section className="stock-ranking-card">
      <div className="stock-ranking-heading"><div><span className="eyebrow">{eyebrow}</span><h3>{title}</h3></div><small>TOP 10</small></div>
      <ol>
        {items.map((item, index) => {
          const value = metric === "speed" ? item.speed1m || 0 : item.turnover;
          const direction = metric === "turnover" ? item.changePct : value;
          return (
            <li key={item.code}>
              <Link href={stockHref(item)}>
                <span className="stock-rank-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="stock-rank-name"><strong>{item.name}</strong><small>{item.code} · {item.price.toFixed(2)}</small></span>
                <span className="stock-rank-change"><strong className={direction >= 0 ? "up" : "down"}>{metric === "speed" ? formatPercent(value, 3) : `${value.toFixed(2)}%`}</strong><small>{metric === "speed" ? `全日 ${formatPercent(item.changePct)}` : `涨跌 ${formatPercent(item.changePct)}`}</small></span>
                <span className="stock-rank-arrow">→</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function StocksPage() {
  const [data, setData] = useState<StockMarketData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const result = await fetchJson<StockMarketData>(`/api/stocks/market${force ? "?refresh=1" : ""}`);
      setData(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "个股行情读取失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => { void load(false); }, 0);
    const timer = window.setInterval(() => { void load(false); }, 60_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  const latest = data?.index.points.at(-1);
  const eventSummary = useMemo(() => {
    const positive = data?.events.filter((event) => event.direction > 0).length || 0;
    const negative = data?.events.filter((event) => event.direction < 0).length || 0;
    return { positive, negative };
  }, [data]);

  return (
    <main className="app-shell stocks-page">
      <header className="topbar">
        <Link className="brand" href="/"><span className="brand-mark"><i /><i /><i /></span><span><strong>资金脉络</strong><small>A股市场观察台</small></span></Link>
        <nav aria-label="主导航"><Link href="/">今日总览</Link><Link href="/trends">板块趋势</Link><Link className="active" href="/stocks">个股异动</Link><Link href="/strategy">策略模拟</Link></nav>
        <div className="top-actions">
          <span className="source-pill"><i />{data ? `真实行情至 ${data.verifiedThrough}` : "公开行情核验中"}</span>
          <button className="refresh-button" type="button" onClick={() => load(true)} disabled={refreshing}><span className={refreshing ? "spin" : ""}>↻</span>{refreshing ? "正在更新" : "刷新行情"}</button>
        </div>
      </header>

      <section className="stocks-hero">
        <div><span className="date-kicker">{data ? shortDate(data.date) : "今日"} · 沪深京 A股</span><h1>捕捉市场的<em>一分钟异动。</em></h1><p>跟踪全市场指数、个股显著事件与实时强弱排名；所有标签和榜单均止于行情源已确认的最后一分钟。</p></div>
        <div className="stocks-live-stamp"><span>VERIFIED THROUGH</span><strong>{data?.verifiedThrough || "--:--"}</strong><small>{data?.source || "等待本地数据服务"}</small></div>
      </section>

      {error && <div className="error-banner"><div><strong>个股行情尚未就绪</strong><span>{error}</span></div><button type="button" onClick={() => load(true)}>重新核验</button></div>}
      {data?.warning && <div className="warning-banner"><strong>{data.isStale ? "最近已保存数据" : "数据提示"}</strong>{data.warning}</div>}

      {loading && !data ? (
        <div className="loading-panel"><span className="loading-orbit" /><strong>正在核验个股分钟行情</strong><p>读取全市场指数、异动事件及 Top 10 排名，首次载入可能需要数秒…</p></div>
      ) : data && (
        <>
          <section className="stocks-metrics">
            <article><span>中证全指</span><strong>{latest?.value.toFixed(2) || "--"}</strong><em className={(latest?.changePct || 0) >= 0 ? "up" : "down"}>{latest ? formatPercent(latest.changePct) : "--"}</em></article>
            <article><span>显著上涨事件</span><strong>{eventSummary.positive}</strong><em>条</em></article>
            <article><span>显著下跌事件</span><strong>{eventSummary.negative}</strong><em>条</em></article>
            <article><span>行情状态</span><strong>{data.isStale ? "本机保存" : "已核验"}</strong><em>{data.verifiedThrough}</em></article>
          </section>

          <section className="feature-panel anomaly-feature">
            <div className="panel-heading"><div><span className="eyebrow">MARKET ANOMALY MAP</span><h2>全市场异动时间线</h2><p>缩小时只保留最突出事件；放大后显示更多局部事件，可拖动下方时间窗口查看早盘。</p></div><div className="legend-pills"><span className="red">上涨异动</span><span className="green">下跌异动</span></div></div>
            <MarketAnomalyChart name={data.index.name} preClose={data.index.preClose} points={data.index.points} events={data.events} />
          </section>

          <section className="stock-rankings-grid">
            <RankingCard title="一分钟涨速最快" eyebrow="FASTEST RISE" items={data.fastestRise} metric="speed" />
            <RankingCard title="一分钟跌速最快" eyebrow="FASTEST FALL" items={data.fastestFall} metric="speed" />
            <RankingCard title="换手率最高" eyebrow="MOST ACTIVE" items={data.highestTurnover} metric="turnover" />
          </section>

          <section className="strategy-launch-card"><div><span className="eyebrow">NO-CODE STRATEGY LAB</span><h2>把异动信号变成可以验证的交易规则。</h2><p>选择买入门槛、板块确认、成交方式、仓位和卖出条件，回放今日真实数据，或启动一个会跨交易日保存现金与持仓的模拟账户。</p></div><Link href="/strategy">打开策略模拟器 <span>→</span></Link></section>

          <div className="stock-method-note"><strong>数据口径</strong><p>{data.rankingMethod || "一分钟涨跌速使用同一只股票最近两个已确认分钟成交价计算"}；因此该榜单是异动候选池的精确一分钟重排，不冒充对近六千只股票的逐只穷举。换手率与事件来自公开延时行情，已排除 ST 与 *ST。事件标签不代表投资建议。</p></div>
        </>
      )}
      <footer><span>资金脉络 · 本地版</span><p>公开行情数据仅供信息展示，不构成任何投资建议。</p></footer>
    </main>
  );
}
