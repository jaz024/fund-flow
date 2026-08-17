"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import StrategyEquityChart from "./StrategyEquityChart";
import type { StrategyLabConfig, StrategyLabData, StrategyLabPosition } from "../lib/types";
import { fetchJson, formatPercent, postJson } from "../lib/types";

const scopeNames = { all: "沪深京 A股", sh: "沪市 A股", sz: "深市 A股", bj: "北交所" } as const;
const sectorNames = { both: "行业上涨且净流入", flow: "行业资金净流入", rise: "行业上涨", none: "不使用行业过滤" } as const;
const priceNames = { minute_open: "目标分钟开盘价", minute_close: "目标分钟最新价", minute_average: "目标分钟均价" } as const;
const exitNames = { next_open: "次日开盘", next_0931: "次日 09:31", risk_close: "止盈/止损，否则次日收盘", hold: "持续持有" } as const;

function money(value: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(value);
}

function strategySentence(config: StrategyLabConfig) {
  const allocation = config.allocationMode === "fixed_pct" ? `每笔 ${config.positionPct.toFixed(1)}% 初始资金` : "现金按剩余仓位均分";
  return `${scopeNames[config.marketScope]}中，${config.startTime}–${config.endTime} 出现一分钟上涨 ≥ ${config.oneMinuteRise.toFixed(2)}%，${sectorNames[config.sectorFilter]}、成交额 ≥ ${(config.minAmount / 100_000_000).toFixed(2)}亿、评分 ≥ ${config.minScore.toFixed(1)}时，延迟 ${config.buyDelayMinutes} 分钟按${priceNames[config.entryPriceMode]}买入；${allocation}，最多 ${config.maxPositions} 只，${exitNames[config.exitMode]}卖出。`;
}

function positionPrice(position: StrategyLabPosition) {
  return position.lastPrice ?? position.currentPrice ?? position.entryPrice;
}

export default function StrategyLab() {
  const [data, setData] = useState<StrategyLabData | null>(null);
  const [config, setConfig] = useState<StrategyLabConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<"preview" | "start" | "pause" | "">("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await fetchJson<StrategyLabData>("/api/strategy/lab");
      setData((previous) => ({ ...result, preview: result.preview ?? previous?.preview ?? null }));
      setConfig((previous) => previous && dirty ? previous : result.account ? result.activeConfig : result.defaultConfig);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "策略模拟服务暂不可用");
    } finally {
      setLoading(false);
    }
  }, [dirty]);

  useEffect(() => {
    const initial = window.setTimeout(() => { void load(); }, 0);
    const timer = window.setInterval(() => { void load(); }, 60_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  const update = <K extends keyof StrategyLabConfig>(key: K, value: StrategyLabConfig[K]) => {
    setDirty(true);
    setConfig((current) => current ? { ...current, [key]: value } : current);
  };

  const act = async (action: "preview" | "start" | "update" | "pause") => {
    if (!config && action !== "pause") return;
    setWorking(action === "update" ? "start" : action);
    setError("");
    try {
      const result = await postJson<StrategyLabData>("/api/strategy/lab", { action, config });
      setData((previous) => ({ ...result, preview: result.preview ?? previous?.preview ?? null }));
      if (action !== "preview") {
        setConfig(result.activeConfig);
        setDirty(false);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "策略模拟没有完成");
    } finally {
      setWorking("");
    }
  };

  const summary = useMemo(() => config ? strategySentence(config) : "", [config]);
  const account = data?.account;
  const preview = data?.preview;
  const openPositions = data?.positions.filter((position) => position.status === "open") || [];

  if (loading && !data) {
    return <div className="strategy-lab-loading"><span className="loading-orbit" /><strong>正在读取策略账户与真实分钟信号</strong><p>首次进入会核验当前交易日的候选事件。</p></div>;
  }

  return <>
    {error && <div className="error-banner strategy-lab-error"><div><strong>模拟暂未完成</strong><span>{error}</span></div><button type="button" onClick={() => load()}>重新读取</button></div>}

    {config && <section className="strategy-builder-panel">
      <div className="strategy-builder-heading">
        <div><span className="eyebrow">NO-CODE STRATEGY BUILDER</span><h2>用选择题组成交易规则。</h2><p>每次回放都按时间顺序读取真实分钟数据；持续模拟的规则变更只影响变更之后的新信号。</p></div>
        <div className="strategy-version-badge"><span>{account ? `策略 V${account.activeVersionId}` : "尚未启动"}</span><strong>{account?.status === "paused" ? "已暂停" : account ? "持续运行" : "试算模式"}</strong></div>
      </div>

      <div className="strategy-choice-grid">
        <label><span>策略名称</span><input value={config.name} onChange={(event) => update("name", event.target.value)} maxLength={36} /></label>
        <label><span>选股范围</span><select value={config.marketScope} onChange={(event) => update("marketScope", event.target.value as StrategyLabConfig["marketScope"])}><option value="all">沪深京 A股</option><option value="sh">沪市 A股</option><option value="sz">深市 A股</option><option value="bj">北交所</option></select></label>
        <label><span>一分钟上涨至少</span><div className="strategy-input-unit"><input type="number" min="0.1" max="10" step="0.1" value={config.oneMinuteRise} onChange={(event) => update("oneMinuteRise", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>行业确认</span><select value={config.sectorFilter} onChange={(event) => update("sectorFilter", event.target.value as StrategyLabConfig["sectorFilter"])}><option value="both">上涨且资金净流入</option><option value="flow">只需资金净流入</option><option value="rise">只需行业上涨</option><option value="none">不使用行业过滤</option></select></label>
        <label><span>累计成交额至少</span><div className="strategy-input-unit"><input type="number" min="0" max="50" step="0.1" value={config.minAmount / 100_000_000} onChange={(event) => update("minAmount", Number(event.target.value) * 100_000_000)} /><i>亿</i></div></label>
        <label><span>综合评分至少</span><input type="number" min="0" max="100" step="1" value={config.minScore} onChange={(event) => update("minScore", Number(event.target.value))} /></label>
        <label><span>允许买入时段</span><div className="strategy-time-range"><input type="time" value={config.startTime} onChange={(event) => update("startTime", event.target.value)} /><i>至</i><input type="time" value={config.endTime} onChange={(event) => update("endTime", event.target.value)} /></div></label>
        <label><span>信号后多久买入</span><select value={config.buyDelayMinutes} onChange={(event) => update("buyDelayMinutes", Number(event.target.value) as 1 | 2 | 5)}><option value={1}>下一分钟</option><option value={2}>延迟 2 分钟</option><option value={5}>延迟 5 分钟</option></select></label>
        <label><span>模拟成交价格</span><select value={config.entryPriceMode} onChange={(event) => update("entryPriceMode", event.target.value as StrategyLabConfig["entryPriceMode"])}><option value="minute_close">目标分钟最新价</option><option value="minute_open">目标分钟开盘价</option><option value="minute_average">目标分钟均价</option></select></label>
        <label><span>资金分配</span><select value={config.allocationMode} onChange={(event) => update("allocationMode", event.target.value as StrategyLabConfig["allocationMode"])}><option value="fixed_pct">固定初始资金比例</option><option value="equal_slots">剩余现金按仓位均分</option></select></label>
        <label><span>单股仓位</span><div className="strategy-input-unit"><input type="number" min="1" max="50" step="1" value={config.positionPct} disabled={config.allocationMode === "equal_slots"} onChange={(event) => update("positionPct", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>最多同时持仓</span><div className="strategy-input-unit"><input type="number" min="1" max="50" step="1" value={config.maxPositions} onChange={(event) => update("maxPositions", Number(event.target.value))} /><i>只</i></div></label>
        <label><span>卖出规则</span><select value={config.exitMode} onChange={(event) => update("exitMode", event.target.value as StrategyLabConfig["exitMode"])}><option value="next_open">次一交易日开盘</option><option value="next_0931">次一交易日 09:31</option><option value="risk_close">止盈/止损，否则次日收盘</option><option value="hold">持续持有</option></select></label>
        <label><span>止盈</span><div className="strategy-input-unit"><input type="number" min="0" max="30" step="0.5" value={config.takeProfitPct} disabled={config.exitMode !== "risk_close"} onChange={(event) => update("takeProfitPct", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>止损</span><div className="strategy-input-unit"><input type="number" min="0" max="20" step="0.5" value={config.stopLossPct} disabled={config.exitMode !== "risk_close"} onChange={(event) => update("stopLossPct", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>初始模拟资金</span><div className="strategy-input-unit"><input type="number" min="10000" max="100000000" step="10000" value={account?.initialCash || config.initialCapital} disabled={Boolean(account)} onChange={(event) => update("initialCapital", Number(event.target.value))} /><i>元</i></div></label>
      </div>

      <div className="strategy-readable-rule"><span>当前规则</span><p>{summary}</p></div>
      <div className="strategy-builder-actions">
        <button className="strategy-secondary-button" type="button" disabled={Boolean(working)} onClick={() => act("preview")}>{working === "preview" ? "正在按分钟回放…" : "回放今日数据"}</button>
        <button className="primary-button" type="button" disabled={Boolean(working) || Boolean(account?.status === "running" && !dirty)} onClick={() => act(account ? "update" : "start")}>{working === "start" ? "正在保存策略…" : account ? dirty ? "保存为新版本并继续" : account.status === "paused" ? "恢复持续模拟" : "策略已在运行" : "开始持续模拟"}</button>
        {account?.status === "running" && <button className="strategy-pause-button" type="button" disabled={Boolean(working)} onClick={() => act("pause")}>{working === "pause" ? "正在暂停…" : "暂停新买入"}</button>}
      </div>
    </section>}

    <section className="strategy-account-overview">
      <div className="strategy-account-heading"><div><span className="eyebrow">TODAY REPLAY</span><h2>今日策略收益</h2><p>{preview ? `${preview.date} 已核验至 ${preview.verifiedThrough}` : "选择规则后点击“回放今日数据”"}</p></div>{preview && <span className="strategy-source-chip">真实分钟回放</span>}</div>
      <div className="strategy-account-grid">
        <article className="featured"><span>今日组合价值</span><strong>{preview ? money(preview.portfolioValue) : "--"}</strong><em className={(preview?.returnPct || 0) >= 0 ? "up" : "down"}>{preview ? formatPercent(preview.returnPct, 3) : "等待回放"}</em></article>
        <article><span>中证全指同期</span><strong className={(preview?.benchmarkReturnPct || 0) >= 0 ? "up" : "down"}>{preview ? formatPercent(preview.benchmarkReturnPct, 3) : "--"}</strong><small>同一时间区间</small></article>
        <article><span>模拟成交</span><strong>{preview?.tradesFilled ?? 0}</strong><small>{preview ? `${preview.signalsMatched} 个匹配信号 · ${preview.failedOrders} 个未成交` : "尚未运行"}</small></article>
        <article><span>现金 / 持仓</span><strong>{preview ? `${money(preview.cash)} / ${money(preview.marketValue)}` : "--"}</strong><small>{preview ? `费用与滑点约 ${money(preview.fees)}` : "T+1 当日仅显示浮盈"}</small></article>
      </div>
      <div className="strategy-chart-panel">
        <div className="strategy-chart-legend"><span><i className="portfolio" />模拟组合</span><span><i className="benchmark" />中证全指</span><span><i className="buy" />买入</span><span><i className="sell" />卖出</span></div>
        <StrategyEquityChart points={preview?.equity || []} events={preview?.events || []} label="今日策略组合与中证全指收益曲线" />
      </div>
      {preview && <p className="strategy-chart-note">{preview.notice}</p>}
    </section>

    <section className="strategy-history-section">
      <div className="strategy-account-heading"><div><span className="eyebrow">PAPER ACCOUNT</span><h2>持续模拟账户</h2><p>{account ? `从 ${account.startedAt.replace("T", " ")} 起保存；处理至 ${account.lastProcessedDate} ${account.lastProcessedTime}` : "开始持续模拟后，现金、持仓与事件会保存在本机。"}</p></div>{account && <span className={`strategy-source-chip ${account.status}`}>{account.status === "running" ? "运行中" : "已暂停"}</span>}</div>
      <div className="strategy-account-grid continuous">
        <article className="featured"><span>账户总资产</span><strong>{account ? money(account.portfolioValue) : "--"}</strong><em className={(account?.returnPct || 0) >= 0 ? "up" : "down"}>{account ? formatPercent(account.returnPct, 3) : "尚未启动"}</em></article>
        <article><span>现金</span><strong>{account ? money(account.cash) : "--"}</strong><small>{account ? `初始 ${money(account.initialCash)}` : "首次启动时确定"}</small></article>
        <article><span>持仓</span><strong>{account?.openPositions ?? 0}</strong><small>{account ? `市值 ${money(account.marketValue)}` : "按各交易所申报单位模拟"}</small></article>
        <article><span>已实现 / 浮动盈亏</span><strong>{account ? `${money(account.realizedPnl)} / ${money(account.unrealizedPnl)}` : "--"}</strong><small>{account ? `${account.closedTrades} 笔已平仓` : "卖出后自动累计"}</small></article>
      </div>
      <div className="strategy-chart-panel history">
        <div className="strategy-chart-legend"><span><i className="portfolio" />账户累计收益</span><span><i className="benchmark" />中证全指同期</span><span><i className="buy" />买入</span><span><i className="sell" />卖出</span><span><i className="version" />规则变更</span></div>
        <StrategyEquityChart points={data?.equity || []} events={data?.events || []} label="持续模拟账户历史净值与事件图" />
      </div>

      <div className="strategy-history-grid">
        <div className="strategy-position-panel"><div className="strategy-subheading"><h3>当前持仓</h3><span>{openPositions.length} 只</span></div>{openPositions.length ? <div className="strategy-position-list">{openPositions.map((position) => <Link key={position.id} href={`/stocks/${position.code}?market=${position.market}&name=${encodeURIComponent(position.name)}`}><div><strong>{position.name}</strong><small>{position.code} · {position.quantity}股 · V{position.strategyVersionId}</small></div><div><strong>{positionPrice(position).toFixed(2)}</strong><small className={(position.pnl || 0) >= 0 ? "up" : "down"}>{money(position.pnl || 0)} · {formatPercent(position.returnPct || 0)}</small></div></Link>)}</div> : <div className="strategy-empty">当前没有持仓。未使用资金保持为现金。</div>}</div>
        <div className="strategy-event-panel"><div className="strategy-subheading"><h3>账户事件</h3><span>最新 300 条</span></div><div className="strategy-event-list">{(data?.events || []).slice(0, 60).map((event) => <article key={event.id ?? `${event.date}-${event.time}-${event.title}`} className={event.type}><i /><div><span>{event.date} {event.time}</span><strong>{event.title}</strong><p>{event.detail}</p></div>{event.price > 0 && <em>{event.price.toFixed(2)}<small>{event.quantity}股</small></em>}</article>)}{!data?.events.length && <div className="strategy-empty">启动持续模拟后，这里会记录策略、买入、卖出和数据缺口。</div>}</div></div>
      </div>

      {Boolean(data?.versions.length) && <div className="strategy-version-history"><div className="strategy-subheading"><h3>策略版本</h3><span>过去结果不会重算</span></div>{data?.versions.map((version) => <article key={version.id}><span>V{version.id}</span><div><strong>{version.effectiveDate} {version.effectiveTime} 生效</strong><p>{version.summary}</p></div></article>)}</div>}
      <div className="strategy-data-notice"><strong>模拟边界</strong><p>{data?.dataNotice} 佣金 {data?.costs.commissionRate.toFixed(4)}%/边、规费及过户估算 {data?.costs.regulatoryAndTransferRate.toFixed(4)}%/边、卖出印花税 {data?.costs.stampDutyRate.toFixed(3)}%、滑点 {data?.costs.slippagePerSide.toFixed(2)}%/边。结果仅用于研究，不构成投资建议。</p></div>
    </section>
  </>;
}
