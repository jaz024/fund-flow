"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import FlowBubbleCanvas from "./components/FlowBubbleCanvas";
import FlowLineChart from "./components/FlowLineChart";
import VideoGenerator from "./components/VideoGenerator";
import type { OverviewData, ReplayData } from "./lib/types";
import { fetchJson, formatYi, shortDate } from "./lib/types";

function LoadingPanel() {
  return (
    <div className="loading-panel" role="status">
      <span className="loading-orbit" />
      <strong>正在连接公开行情源</strong>
      <p>读取沪、深、京板块资金净额数据…</p>
    </div>
  );
}

function VerificationPanel() {
  return (
    <div className="loading-panel verification-panel" role="status">
      <span className="loading-orbit" />
      <strong>正在核验真实分时数据</strong>
      <p>后台正在依次检查公开行情源并保存已确认的五分钟观测。</p>
    </div>
  );
}

function RankingList({ title, items, kind }: { title: string; items: OverviewData["topIn"]; kind: "in" | "out" }) {
  const maxValue = Math.max(...items.map((item) => Math.abs(item.mainFlow)), 1);
  return (
    <section className="ranking-panel">
      <div className="panel-heading compact">
        <div>
          <span className="eyebrow">{kind === "in" ? "TOP INFLOW" : "TOP OUTFLOW"}</span>
          <h3>{title}</h3>
        </div>
        <span className={`direction-badge ${kind}`}>{kind === "in" ? "净流入" : "净流出"}</span>
      </div>
      <ol className="ranking-list">
        {items.slice(0, 7).map((item, index) => (
          <li key={item.code}>
            <span className="rank-number">{String(index + 1).padStart(2, "0")}</span>
            <div className="rank-main">
              <div><strong>{item.name}</strong><small>{item.category}</small></div>
              <span className={`rank-value ${kind}`}>{formatYi(item.mainFlow)}</span>
              <i className={kind} style={{ width: `${Math.max(8, (Math.abs(item.mainFlow) / maxValue) * 100)}%` }} />
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function Home() {
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [replay, setReplay] = useState<ReplayData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const suffix = force ? "?refresh=1" : "";
      const overviewData = await fetchJson<OverviewData>(`/api/overview${suffix}`);
      setOverview(overviewData);
      const replayData = await fetchJson<ReplayData>(`/api/replay${suffix}`);
      setReplay(replayData);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法连接本地数据服务");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(false); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const totalFlow = useMemo(() => overview?.boards.reduce((sum, item) => sum + item.mainFlow, 0) || 0, [overview]);
  const strongest = overview?.topIn[0];
  const weakest = overview?.topOut[0];

  return (
    <main className="app-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="资金脉络首页">
          <span className="brand-mark"><i /><i /><i /></span>
          <span><strong>资金脉络</strong><small>A股板块观察台</small></span>
        </Link>
        <nav aria-label="主导航">
          <Link className="active" href="/">今日总览</Link>
          <Link href="/trends">板块趋势</Link>
        </nav>
        <div className="top-actions">
          <span className="source-pill">
            <i />{replay?.verifiedThrough ? `真实分时至 ${replay.verifiedThrough}` : "公开行情核验中"}
          </span>
          <button className="refresh-button" type="button" onClick={() => load(true)} disabled={refreshing}>
            <span className={refreshing ? "spin" : ""}>↻</span>{refreshing ? "正在更新" : "更新收盘数据"}
          </button>
        </div>
      </header>

      <section className="hero-row">
        <div>
          <span className="date-kicker">{overview ? shortDate(overview.date) : "今日"} · 沪深京 A股</span>
          <h1>看清资金，<em>流向哪里。</em></h1>
          <p>行业与概念板块资金净流入的五分钟回放、当日排名与近三个月趋势。</p>
        </div>
        <div className="data-stamp">
          <span>数据时间</span>
          <strong>{overview?.updatedAt?.slice(11, 16) || "--:--"}</strong>
          <small>{overview?.date || "等待连接"} · {overview?.source || "本地服务"}</small>
        </div>
      </section>

      {error && (
        <div className="error-banner">
          <div><strong>本地服务尚未就绪</strong><span>{error}</span></div>
          <button type="button" onClick={() => load(false)}>重新连接</button>
        </div>
      )}
      {(overview?.warning || replay?.warning) && (
        <div className="warning-banner"><strong>数据提示</strong>{replay?.warning || overview?.warning}</div>
      )}

      {loading && !overview ? <LoadingPanel /> : overview && (
        <>
          <section className="index-strip" aria-label="主要指数">
            {overview.indexes.map((item) => (
              <div key={item.code}>
                <span>{item.name}</span>
                <strong>{item.price ? item.price.toFixed(2) : "--"}</strong>
                <em className={item.changePct >= 0 ? "up" : "down"}>
                  {item.changePct >= 0 ? "+" : ""}{item.changePct.toFixed(2)}%
                </em>
              </div>
            ))}
            <div className="index-summary">
              <span>板块资金净额合计</span>
              <strong className={totalFlow >= 0 ? "up" : "down"}>{formatYi(totalFlow)}</strong>
              <small>行业与概念存在成分重叠，不代表全市场净额</small>
            </div>
          </section>

          <section className="metrics-grid">
            <article className="metric-card featured">
              <span>最强净流入</span>
              <div><strong>{strongest?.name || "--"}</strong><em className="up">{strongest ? formatYi(strongest.mainFlow) : "--"}</em></div>
              <small>{strongest?.category || "板块"} · 涨跌幅 {strongest ? `${strongest.changePct >= 0 ? "+" : ""}${strongest.changePct.toFixed(2)}%` : "--"}</small>
            </article>
            <article className="metric-card">
              <span>最强净流出</span>
              <div><strong>{weakest?.name || "--"}</strong><em className="down">{weakest ? formatYi(weakest.mainFlow) : "--"}</em></div>
              <small>{weakest?.category || "板块"} · 涨跌幅 {weakest ? `${weakest.changePct >= 0 ? "+" : ""}${weakest.changePct.toFixed(2)}%` : "--"}</small>
            </article>
            <article className="metric-card">
              <span>覆盖板块</span>
              <div><strong>{overview.boards.length}</strong><em>个</em></div>
              <small>动态合并行业与概念板块</small>
            </article>
          </section>

          <section className="feature-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">FIVE-MINUTE REPLAY</span>
                <h2>今日资金流向回放</h2>
                <p>每个气泡固定对应一个板块；金额越大气泡越大，净流入向上、净流出向下，过零时自动换边变色。</p>
              </div>
              <div className="replay-status-group">
                {replay?.verifiedThrough && (
                  <span className="verified-status">
                    已核验至 {replay.verifiedThrough} · {replay.coveragePercent ?? 0}%覆盖
                  </span>
                )}
                <div className="legend-pills"><span className="red">资金净流入</span><span className="green">资金净流出</span></div>
              </div>
            </div>
            {replay?.frames.length ? (
              <FlowBubbleCanvas date={replay.date} frames={replay.frames} indexes={replay.indexes} />
            ) : <VerificationPanel />}
          </section>

          <section className="rankings-grid">
            <RankingList title="资金净流入前列" items={overview.topIn} kind="in" />
            <RankingList title="资金净流出前列" items={overview.topOut} kind="out" />
          </section>

          <section className="feature-panel line-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">INTRADAY TREND</span>
                <h2>重点板块分时轨迹</h2>
                <p>最多选择五个板块，观察资金净流入在交易日内的累计变化。</p>
              </div>
              <Link className="text-link" href="/trends">查看三个月趋势 <span>→</span></Link>
            </div>
            {replay?.frames.length ? <FlowLineChart frames={replay.frames} /> : <VerificationPanel />}
          </section>

          {replay?.frames.length ? <VideoGenerator replay={replay} /> : null}
        </>
      )}

      <footer>
        <span>资金脉络 · 本地版</span>
        <p>数据来源于公开行情，仅供信息展示，不构成任何投资建议。</p>
      </footer>
    </main>
  );
}
