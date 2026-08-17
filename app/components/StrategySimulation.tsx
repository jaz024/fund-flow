"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { StrategyData, StrategyTrade } from "../lib/types";
import { fetchJson, formatPercent, formatYi } from "../lib/types";

function tradeHref(trade: StrategyTrade) { return `/stocks/${trade.code}?market=${trade.market}&name=${encodeURIComponent(trade.name)}`; }
function result(value: number | null, pending: string) { return value === null ? <span className="strategy-pending">{pending}</span> : <strong className={value >= 0 ? "up" : "down"}>{formatPercent(value)}</strong>; }
function statusLabel(trade: StrategyTrade) {
  if (trade.status === "unfilled") return "无法成交";
  if (trade.status === "pending_execution") return "等待下一分钟";
  if (trade.status === "complete") return "次日已完成";
  return "持仓观察中";
}

export default function StrategySimulation() {
  const [data, setData] = useState<StrategyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState<"today" | "history">("today");
  const load = useCallback(async (force = false) => {
    setError("");
    try { setData(await fetchJson<StrategyData>(`/api/stocks/strategy${force ? "?refresh=1" : ""}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "观察性模拟暂不可用"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    const initial = window.setTimeout(() => { void load(false); }, 0);
    const timer = window.setInterval(() => { void load(false); }, 60_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);
  const trades = useMemo(() => data?.trades.filter((trade) => view === "history" || trade.date === data.date) || [], [data, view]);

  return <section className="strategy-section">
    <div className="strategy-heading"><div><span className="eyebrow">OBSERVATIONAL SIMULATION</span><h2>追涨策略 · 实时观察</h2><p>只使用信号当时已经保存的数据；不连接券商，不下真实订单，也不使用收盘后才知道的信息。</p></div><div className="strategy-switch"><button className={view === "today" ? "active" : ""} type="button" onClick={() => setView("today")}>今日实时</button><button className={view === "history" ? "active" : ""} type="button" onClick={() => setView("history")}>30日记录</button></div></div>
    {loading && !data && <div className="strategy-empty">正在核验信号、主营行业和下一分钟成交…</div>}
    {error && <div className="error-banner"><div><strong>模拟暂未更新</strong><span>{error}</span></div><button type="button" onClick={() => load(true)}>重试</button></div>}
    {data && <>
      <div className={`strategy-capture ${data.captureStatus}`}><i />{data.captureMessage}</div>
      <div className="strategy-live-grid">
        <article className="strategy-live-return"><span>今日组合实时收益 · 已估成本</span><strong className={data.livePortfolioReturn >= 0 ? "up" : "down"}>{formatPercent(data.livePortfolioReturn, 3)}</strong><small>{data.filledToday} 笔 · 现金 {(data.cashWeight * 100).toFixed(0)}% · 中证全指同期 {formatPercent(data.liveBenchmarkReturn, 3)} · 超额 {formatPercent(data.liveAlpha, 3)}</small></article>
        <article><span>上涨信号</span><strong>{data.signalsToday}</strong><small>每股仅记录首次触发</small></article>
        <article><span>模拟买入</span><strong>{data.tradesToday} / 20</strong><small>剩余 {data.remainingSlots} 个 5% 仓位</small></article>
        <article><span>无法成交</span><strong>{data.unfilledToday}</strong><small>涨停或无下一分钟成交</small></article>
        <article><span>实时门槛</span><strong>{data.threshold.toFixed(1)}</strong><small>只由此前交易日校准</small></article>
      </div>
      <div className="strategy-result-grid">
        <article><span>当日收盘浮盈 · 日均组合</span>{result(data.summary.closeMean, "等待样本")}<small>{data.summary.completedClose} 日 · 中证全指同期 {data.summary.closeBenchmarkMean === null ? "--" : formatPercent(data.summary.closeBenchmarkMean)} · 超额 {data.summary.closeAlphaMean === null ? "--" : formatPercent(data.summary.closeAlphaMean)}</small></article>
        <article><span>当日收盘 · 胜率</span><strong>{data.summary.closeWinRate === null ? "--" : `${data.summary.closeWinRate.toFixed(1)}%`}</strong><small>扣除估算交易成本后</small></article>
        <article><span>次日官方开盘 · 日均组合</span>{result(data.summary.nextOpenMean, "等待次日")}<small>{data.summary.completedNextOpen} 日 · 胜率 {data.summary.nextOpenWinRate === null ? "--" : `${data.summary.nextOpenWinRate.toFixed(1)}%`} · 超额 {data.summary.nextOpenAlphaMean === null ? "--" : formatPercent(data.summary.nextOpenAlphaMean)}</small></article>
        <article><span>次日 09:31 · 日均组合</span>{result(data.summary.next0931Mean, "等待次日")}<small>{data.summary.completedNext} 日 · 胜率 {data.summary.next0931WinRate === null ? "--" : `${data.summary.next0931WinRate.toFixed(1)}%`} · 超额 {data.summary.next0931AlphaMean === null ? "--" : formatPercent(data.summary.next0931AlphaMean)}</small></article>
      </div>
      <div className="strategy-table-wrap"><table className="strategy-table"><thead><tr><th>股票 / 信号</th><th>主营行业过滤</th><th>模拟成交</th><th>实时收益</th><th>当日收盘浮盈</th><th>次日开盘 / 09:31</th><th>状态</th></tr></thead><tbody>
        {trades.map((trade) => <tr key={`${trade.date}-${trade.code}`}>
          <td><Link href={tradeHref(trade)}><strong>{trade.name}</strong><small>{trade.code} · {trade.date} {trade.signalTime}<br />1分钟 {formatPercent(trade.oneMinuteReturn)}</small></Link></td>
          <td><strong>{trade.industryName || "未匹配"}</strong><small>{formatPercent(trade.sectorChangePct)} · {formatYi(trade.sectorMainFlow)}</small></td>
          <td><strong>{trade.executionPrice ? trade.executionPrice.toFixed(2) : "--"}</strong><small>{trade.executionTime || "等待"} · 5% 仓位</small></td>
          <td>{result(trade.currentReturnAfterCost, "未成交")}</td><td>{result(trade.closeReturnAfterCost, "等待收盘")}</td>
          <td><div className="strategy-dual-result">{result(trade.nextOpenReturnAfterCost, "等待开盘")}{result(trade.next0931ReturnAfterCost, "等待09:31")}</div></td>
          <td><span className={`strategy-status ${trade.status}`}>{statusLabel(trade)}</span>{trade.reason && <small>{trade.reason}</small>}</td>
        </tr>)}
        {!trades.length && <tr><td colSpan={7}><div className="strategy-empty">当前没有符合全部实时条件的模拟交易。现金保持未投入。</div></td></tr>}
      </tbody></table></div>
      <div className="strategy-method"><strong>固定规则</strong><p>{data.method} 当日收盘仅为 T+1 约束下的浮动收益，不假设可以当日卖出。</p><strong>成本假设</strong><p>佣金 {data.costs.commissionRate.toFixed(4)}%/边、规费及过户估算 {data.costs.regulatoryAndTransferRate.toFixed(4)}%/边、卖出印花税 {data.costs.stampDutyRate.toFixed(3)}%、滑点 {data.costs.slippagePerSide.toFixed(2)}%/边；入场约 {data.costs.entryEstimate.toFixed(3)}%，次日往返约 {data.costs.roundTripEstimate.toFixed(3)}%。小额账户的每笔最低佣金未计入。</p></div>
    </>}
  </section>;
}
