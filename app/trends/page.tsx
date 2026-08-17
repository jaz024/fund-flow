"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TrendChart from "../components/TrendChart";
import type { BoardFlow, HistoryData, OverviewData } from "../lib/types";
import { fetchJson, formatYi } from "../lib/types";

export default function TrendsPage() {
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [selected, setSelected] = useState<BoardFlow | null>(null);
  const [history, setHistory] = useState<HistoryData | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadHistory = useCallback(async (board: BoardFlow) => {
    setSelected(board);
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson<HistoryData>(`/api/history?code=${encodeURIComponent(board.code)}&name=${encodeURIComponent(board.name)}`);
      setHistory(data);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法载入历史数据");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJson<OverviewData>("/api/overview")
      .then((data) => {
        setOverview(data);
        const first = data.topIn[0] || data.boards[0];
        if (first) void loadHistory(first);
        else setLoading(false);
      })
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : "无法载入板块列表");
        setLoading(false);
      });
  }, [loadHistory]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const boards = overview?.boards || [];
    if (normalized) {
      return boards
        .filter((item) => item.name.toLowerCase().includes(normalized) || item.code.toLowerCase().includes(normalized))
        .sort((left, right) => Math.abs(right.mainFlow) - Math.abs(left.mainFlow))
        .slice(0, 80);
    }
    const inflow = boards.filter((item) => item.mainFlow > 0).slice(0, 40);
    const outflow = boards.filter((item) => item.mainFlow < 0).sort((left, right) => left.mainFlow - right.mainFlow).slice(0, 40);
    return [...inflow, ...outflow].sort((left, right) => Math.abs(right.mainFlow) - Math.abs(left.mainFlow));
  }, [overview, query]);

  return (
    <main className="app-shell trend-page">
      <header className="topbar">
        <Link className="brand" href="/">
          <span className="brand-mark"><i /><i /><i /></span>
          <span><strong>资金脉络</strong><small>A股板块观察台</small></span>
        </Link>
        <nav aria-label="主导航"><Link href="/">今日总览</Link><Link className="active" href="/trends">板块趋势</Link><Link href="/stocks">个股异动</Link><Link href="/strategy">策略模拟</Link></nav>
        <Link className="back-link" href="/">← 返回今日总览</Link>
      </header>

      <section className="trend-hero">
        <div><span className="date-kicker">近三个月 · 日频</span><h1>板块资金<em>趋势透视</em></h1></div>
        <p>选择行业或概念板块，比较每日资金净流入与 5 日、20 日滚动均线。</p>
      </section>

      {error && <div className="error-banner"><div><strong>数据读取失败</strong><span>{error}</span></div></div>}

      <section className="trend-workspace">
        <aside className="sector-sidebar">
          <div className="sidebar-heading">
            <span className="eyebrow">SECTORS</span>
            <strong>今日资金净额前 80</strong>
            <small>流入 40 + 流出 40 · 截至 {overview?.updatedAt?.slice(11, 16) || "--:--"}</small>
          </div>
          <label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索行业或概念" /></label>
          <div className="sector-list">
            {filtered.map((board) => (
              <button type="button" key={board.code} className={selected?.code === board.code ? "active" : ""} onClick={() => loadHistory(board)}>
                <span><strong>{board.name}</strong><small>{board.category}</small></span>
                <em className={board.mainFlow >= 0 ? "up" : "down"}>{formatYi(board.mainFlow)}</em>
              </button>
            ))}
          </div>
        </aside>

        <div className="trend-content">
          <div className="trend-title-row">
            <div><span className="eyebrow">CAPITAL FLOW HISTORY</span><h2>{selected?.name || "选择一个板块"}</h2></div>
            <span className="source-pill"><i />{history?.source || "等待真实数据"}</span>
          </div>
          {loading && !history ? (
            <div className="loading-panel"><span className="loading-orbit" /><strong>正在载入趋势</strong><p>整理近三个月交易日数据…</p></div>
          ) : history ? <TrendChart history={history} /> : null}
          <div className="trend-note">
            <strong>如何阅读</strong>
            <p>柱体显示当日资金净流入；黄色和蓝色曲线分别为 5 日及 20 日均值。行业与概念板块成分可能重叠，适合观察趋势，不应用于推导全市场资金总额。</p>
          </div>
        </div>
      </section>
      <footer><span>资金脉络 · 本地版</span><p>公开行情数据仅供信息展示，不构成任何投资建议。</p></footer>
    </main>
  );
}
