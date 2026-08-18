"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import StrategyEquityChart from "./StrategyEquityChart";
import type { StrategyLabConfig, StrategyLabData, StrategyLabPosition } from "../lib/types";
import { fetchJson, formatPercent, postJson } from "../lib/types";

const scopeNames = { all: "沪深京 A股", sh: "沪市 A股", sz: "深市 A股", bj: "北交所" } as const;
const sectorNames = { both: "行业上涨且净流入", flow: "行业资金净流入", rise: "行业上涨", none: "不使用行业过滤" } as const;
const priceNames = { minute_open: "目标分钟开盘价", minute_close: "目标分钟最新价", minute_average: "目标分钟均价" } as const;
const exitNames = { next_open: "次日开盘", next_0931: "次日 09:31", risk_close: "止盈/止损，否则次日收盘", model_reverse: "T+1 后由反向模型退出", hold: "持续持有" } as const;
const modelNames = { rapid_rise: "一分钟异动追涨", trend: "短线趋势延续", mean_reversion: "超跌均值回归", volatility_breakout: "放量波动突破" } as const;
const vwapNames = { any: "不使用 VWAP", above: "价格高于 VWAP", below: "价格低于 VWAP" } as const;

function money(value: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(value);
}

function strategySentence(config: StrategyLabConfig) {
  const allocation = config.allocationMode === "fixed_pct" ? `每笔 ${config.positionPct.toFixed(1)}% 初始资金` : "现金按剩余仓位均分";
  const trigger = config.signalModel === "rapid_rise"
    ? `一分钟上涨 ≥ ${config.oneMinuteRise.toFixed(2)}%`
    : config.signalModel === "mean_reversion"
      ? `${config.lookbackMinutes}分钟下跌 ≥ ${config.oneMinuteRise.toFixed(2)}%`
      : config.signalModel === "trend"
        ? `${config.lookbackMinutes}分钟上涨 ≥ ${config.oneMinuteRise.toFixed(2)}%`
        : `突破此前${config.lookbackMinutes}分钟高点 ≥ ${config.oneMinuteRise.toFixed(2)}%`;
  const volume = config.minVolumeRatio > 0 ? `分钟量比 ≥ ${config.minVolumeRatio.toFixed(2)}` : "不限分钟量比";
  return `${scopeNames[config.marketScope]}中，${config.startTime}–${config.endTime} 由“${modelNames[config.signalModel]}”检测到${trigger}，并满足${vwapNames[config.vwapFilter]}、${volume}、${sectorNames[config.sectorFilter]}、成交额 ≥ ${(config.minAmount / 100_000_000).toFixed(2)}亿及评分 ≥ ${config.minScore.toFixed(1)}后，延迟 ${config.buyDelayMinutes} 分钟按${priceNames[config.entryPriceMode]}买入；${allocation}，最多 ${config.maxPositions} 只，${exitNames[config.exitMode]}。`;
}

function positionPrice(position: StrategyLabPosition) {
  return position.lastPrice ?? position.currentPrice ?? position.entryPrice;
}

function replayEntryTime(position: StrategyLabPosition) {
  return position.entryTime || position.executionTime || "--:--";
}

function replayEntryPrice(position: StrategyLabPosition) {
  const value = position.entryPrice ?? position.executionPrice;
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "--";
}

function replayNextOpenPrice(position: StrategyLabPosition) {
  const value = position.nextOpenPrice;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value.toFixed(2) : "等待补录";
}

export default function StrategyLab() {
  const [data, setData] = useState<StrategyLabData | null>(null);
  const [config, setConfig] = useState<StrategyLabConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<"preview" | "start" | "pause" | "">("");
  const [workingSeconds, setWorkingSeconds] = useState(0);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [builderMode, setBuilderMode] = useState<"template" | "custom">("template");
  const [replayView, setReplayView] = useState<"today" | "history">("today");
  const [selectedHistoryId, setSelectedHistoryId] = useState("");
  const autoReplayStarted = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await fetchJson<StrategyLabData>("/api/strategy/lab");
      setData((previous) => ({
        ...result,
        preview: dirty ? previous?.preview ?? result.preview ?? null : result.preview ?? previous?.preview ?? null,
      }));
      setConfig((previous) => previous && dirty ? previous : result.account ? result.activeConfig : result.defaultConfig);
      if (result.preview) autoReplayStarted.current = true;
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

  useEffect(() => {
    if (!working) return;
    const startedAt = Date.now();
    const updateElapsed = () => setWorkingSeconds(Math.floor((Date.now() - startedAt) / 1000));
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(timer);
  }, [working]);

  const update = <K extends keyof StrategyLabConfig>(key: K, value: StrategyLabConfig[K]) => {
    setDirty(true);
    setConfig((current) => current ? { ...current, [key]: value } : current);
  };

  const applyPreset = (preset: StrategyLabData["presets"][number]) => {
    setConfig({ ...preset.config, initialCapital: account?.initialCash || config?.initialCapital || preset.config.initialCapital });
    setDirty(true);
  };

  const act = useCallback(async (action: "preview" | "start" | "update" | "pause") => {
    if (!config && action !== "pause") return;
    setWorkingSeconds(0);
    setWorking(action === "update" ? "start" : action);
    setError("");
    try {
      const result = await postJson<StrategyLabData>("/api/strategy/lab", { action, config });
      if (action === "preview") {
        autoReplayStarted.current = true;
        setData(result);
      } else {
        autoReplayStarted.current = Boolean(result.preview);
        setData(result);
        setConfig(result.activeConfig);
        setDirty(false);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "策略模拟没有完成");
    } finally {
      setWorking("");
      setWorkingSeconds(0);
    }
  }, [config]);

  useEffect(() => {
    if (loading || !data || !config || data.preview || working || autoReplayStarted.current) return;
    autoReplayStarted.current = true;
    const timer = window.setTimeout(() => { void act("preview"); }, 350);
    return () => window.clearTimeout(timer);
  }, [act, config, data, loading, working]);

  const replayProgress = workingSeconds < 6
    ? "正在按时间顺序重建今日信号"
    : workingSeconds < 22
      ? "正在向多个公开行情源核验候选股票"
      : "行情源响应较慢，正在保留已完成的核验结果";

  const summary = useMemo(() => config ? strategySentence(config) : "", [config]);
  const account = data?.account;
  const preview = data?.preview;
  const previewHistory = (data?.previewHistory || []).filter((record) => record.date !== data?.date);
  const selectedHistory = previewHistory.find((record) => record.id === selectedHistoryId) || previewHistory[0] || null;
  const openPositions = data?.positions.filter((position) => position.status === "open") || [];

  if (loading && !data) {
    return <div className="strategy-lab-loading"><span className="loading-orbit" /><strong>正在读取策略账户与真实分钟信号</strong><p>首次进入会核验当前交易日的候选事件。</p></div>;
  }

  return <>
    {error && <div className="error-banner strategy-lab-error"><div><strong>模拟暂未完成</strong><span>{error}</span></div><button type="button" onClick={() => load()}>重新读取</button></div>}

    {config && <section className="strategy-builder-panel">
      <div className="strategy-builder-heading">
        <div><span className="eyebrow">COMPOSABLE STRATEGY ENGINE</span><h2>先定义信号，再定义仓位和退出。</h2><p>追涨只是一个模板。趋势、超跌回归与突破模型都按当时可见的数据独立判断；规则变更只影响变更后的新信号。</p></div>
        <div className="strategy-version-badge"><span>{account ? `策略 V${account.activeVersionId}` : "尚未启动"}</span><strong>{account?.status === "paused" ? "已暂停" : account ? "持续运行" : "试算模式"}</strong></div>
      </div>

      <div className="strategy-builder-mode" role="tablist" aria-label="策略构建方式">
        <button className={builderMode === "template" ? "active" : ""} type="button" onClick={() => setBuilderMode("template")}><strong>策略模板</strong><span>从可运行模型开始</span></button>
        <button className={builderMode === "custom" ? "active" : ""} type="button" onClick={() => setBuilderMode("custom")}><strong>自定义策略</strong><span>组合信号、执行与风控</span></button>
      </div>

      {builderMode === "template" && <div className="strategy-template-grid">
        {(data?.presets || []).map((preset) => <button className={config.signalModel === preset.id ? "active" : ""} type="button" key={preset.id} onClick={() => applyPreset(preset)}><span><i />分钟数据可运行</span><strong>{preset.name}</strong><p>{preset.description}</p><em>{config.signalModel === preset.id ? "当前模板" : "使用模板"}</em></button>)}
      </div>}

      {builderMode === "custom" && <div className="strategy-capability-panel">
        <div><strong>当前真实数据可用</strong><p>分钟价格、成交量、VWAP、公开异动、已保存板块快照。下面的规则会严格按事件时间计算。</p></div>
        <div className="strategy-future-models">{(data?.futureModels || []).map((model) => <article key={model.id} aria-disabled="true"><span>需要更多数据</span><strong>{model.name}</strong><p>{model.requirement}</p></article>)}</div>
      </div>}

      <div className="strategy-rule-section"><div className="strategy-rule-section-title"><span>01</span><div><strong>信号模型</strong><p>模型决定何时产生目标仓位；均值回归可以在下跌时买入。</p></div></div><div className="strategy-choice-grid">
        <label><span>策略名称</span><input value={config.name} onChange={(event) => update("name", event.target.value)} maxLength={36} /></label>
        <label><span>信号模型</span><select value={config.signalModel} onChange={(event) => update("signalModel", event.target.value as StrategyLabConfig["signalModel"])}><option value="rapid_rise">一分钟异动追涨</option><option value="trend">短线趋势延续</option><option value="mean_reversion">超跌均值回归</option><option value="volatility_breakout">放量波动突破</option></select></label>
        <label><span>观察窗口</span><div className="strategy-input-unit"><input type="number" min="1" max="60" step="1" value={config.lookbackMinutes} disabled={config.signalModel === "rapid_rise"} onChange={(event) => update("lookbackMinutes", Number(event.target.value))} /><i>分钟</i></div></label>
        <label><span>{config.signalModel === "mean_reversion" ? "下跌 / 偏离至少" : config.signalModel === "volatility_breakout" ? "突破区间高点至少" : "上涨至少"}</span><div className="strategy-input-unit"><input type="number" min="0.1" max="10" step="0.1" value={config.oneMinuteRise} onChange={(event) => update("oneMinuteRise", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>VWAP 确认</span><select value={config.vwapFilter} onChange={(event) => update("vwapFilter", event.target.value as StrategyLabConfig["vwapFilter"])}><option value="any">不使用 VWAP</option><option value="above">价格高于 VWAP</option><option value="below">价格低于 VWAP</option></select></label>
        <label><span>分钟量比至少</span><input type="number" min="0" max="10" step="0.05" value={config.minVolumeRatio} onChange={(event) => update("minVolumeRatio", Number(event.target.value))} /></label>
        <label><span>行业确认</span><select value={config.sectorFilter} onChange={(event) => update("sectorFilter", event.target.value as StrategyLabConfig["sectorFilter"])}><option value="both">上涨且资金净流入</option><option value="flow">只需资金净流入</option><option value="rise">只需行业上涨</option><option value="none">不使用行业过滤</option></select></label>
        <label><span>选股范围</span><select value={config.marketScope} onChange={(event) => update("marketScope", event.target.value as StrategyLabConfig["marketScope"])}><option value="all">沪深京 A股</option><option value="sh">沪市 A股</option><option value="sz">深市 A股</option><option value="bj">北交所</option></select></label>
        <label><span>累计成交额至少</span><div className="strategy-input-unit"><input type="number" min="0" max="50" step="0.1" value={config.minAmount / 100_000_000} onChange={(event) => update("minAmount", Number(event.target.value) * 100_000_000)} /><i>亿</i></div></label>
        <label><span>技术评分至少</span><input type="number" min="0" max="100" step="1" value={config.minScore} onChange={(event) => update("minScore", Number(event.target.value))} /></label>
        <label><span>允许买入时段</span><div className="strategy-time-range"><input type="time" value={config.startTime} onChange={(event) => update("startTime", event.target.value)} /><i>至</i><input type="time" value={config.endTime} onChange={(event) => update("endTime", event.target.value)} /></div></label>
      </div></div>

      <div className="strategy-rule-section"><div className="strategy-rule-section-title"><span>02</span><div><strong>成交、仓位与退出</strong><p>信号先形成目标，再以真实下一分钟和交易所约束决定是否成交。</p></div></div><div className="strategy-choice-grid">
        <label><span>信号后多久买入</span><select value={config.buyDelayMinutes} onChange={(event) => update("buyDelayMinutes", Number(event.target.value) as 1 | 2 | 5)}><option value={1}>下一分钟</option><option value={2}>延迟 2 分钟</option><option value={5}>延迟 5 分钟</option></select></label>
        <label><span>模拟成交价格</span><select value={config.entryPriceMode} onChange={(event) => update("entryPriceMode", event.target.value as StrategyLabConfig["entryPriceMode"])}><option value="minute_close">目标分钟最新价</option><option value="minute_open">目标分钟开盘价</option><option value="minute_average">目标分钟均价</option></select></label>
        <label><span>资金分配</span><select value={config.allocationMode} onChange={(event) => update("allocationMode", event.target.value as StrategyLabConfig["allocationMode"])}><option value="fixed_pct">固定初始资金比例</option><option value="equal_slots">剩余现金按仓位均分</option></select></label>
        <label><span>单股仓位</span><div className="strategy-input-unit"><input type="number" min="1" max="50" step="1" value={config.positionPct} disabled={config.allocationMode === "equal_slots"} onChange={(event) => update("positionPct", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>最多同时持仓</span><div className="strategy-input-unit"><input type="number" min="1" max="50" step="1" value={config.maxPositions} onChange={(event) => update("maxPositions", Number(event.target.value))} /><i>只</i></div></label>
        <label><span>卖出规则</span><select value={config.exitMode} onChange={(event) => update("exitMode", event.target.value as StrategyLabConfig["exitMode"])}><option value="model_reverse">T+1 后由反向模型退出</option><option value="next_open">次一交易日开盘</option><option value="next_0931">次一交易日 09:31</option><option value="risk_close">止盈/止损，否则次日收盘</option><option value="hold">持续持有</option></select></label>
        <label><span>止盈</span><div className="strategy-input-unit"><input type="number" min="0" max="30" step="0.5" value={config.takeProfitPct} disabled={!(["risk_close", "model_reverse"] as string[]).includes(config.exitMode)} onChange={(event) => update("takeProfitPct", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>止损</span><div className="strategy-input-unit"><input type="number" min="0" max="20" step="0.5" value={config.stopLossPct} disabled={!(["risk_close", "model_reverse"] as string[]).includes(config.exitMode)} onChange={(event) => update("stopLossPct", Number(event.target.value))} /><i>%</i></div></label>
        <label><span>初始模拟资金</span><div className="strategy-input-unit"><input type="number" min="10000" max="100000000" step="10000" value={account?.initialCash || config.initialCapital} disabled={Boolean(account)} onChange={(event) => update("initialCapital", Number(event.target.value))} /><i>元</i></div></label>
      </div></div>

      <div className="strategy-readable-rule"><span>当前规则</span><p>{summary}</p></div>
      <div className="strategy-builder-actions">
        <button className="strategy-secondary-button" type="button" disabled={Boolean(working)} onClick={() => act("preview")}>{working === "preview" ? `正在回放 · ${workingSeconds}秒` : data?.preview ? "更新今日回放" : "回放今日数据"}</button>
        <button className="primary-button" type="button" disabled={Boolean(working) || Boolean(account?.status === "running" && !dirty)} onClick={() => act(account ? "update" : "start")}>{working === "start" ? "正在保存策略…" : account ? dirty ? "保存为新版本并继续" : account.status === "paused" ? "恢复持续模拟" : "策略已在运行" : "开始持续模拟"}</button>
        {account?.status === "running" && <button className="strategy-pause-button" type="button" disabled={Boolean(working)} onClick={() => act("pause")}>{working === "pause" ? "正在暂停…" : "暂停新买入"}</button>}
      </div>
      {working === "preview" && <div className="strategy-replay-progress" role="status" aria-live="polite"><span className="loading-orbit" /><div><strong>{replayProgress}</strong><p>已用 {workingSeconds} 秒。可以继续浏览页面；成功结果会保存在本机，重新打开也不会丢失。</p></div></div>}
    </section>}

    <section className="strategy-account-overview">
      <div className="strategy-replay-nav">
        <div><span className="eyebrow">SAVED REPLAY ARCHIVE</span><h2>策略回放</h2><p>今日试算和已经保存的交易日彼此独立；历史结果不会因后来修改规则而重算。</p></div>
        <div className="strategy-replay-tabs" role="tablist" aria-label="回放时间范围"><button className={replayView === "today" ? "active" : ""} type="button" onClick={() => setReplayView("today")}>今日回放</button><button className={replayView === "history" ? "active" : ""} type="button" onClick={() => setReplayView("history")}>历史回放 <span>{previewHistory.length}</span></button></div>
      </div>

      {replayView === "today" ? <>
        <div className="strategy-account-heading compact"><div><span className="eyebrow">TODAY REPLAY</span><h2>今日策略收益</h2><p>{preview ? `${preview.date} 已核验至 ${preview.verifiedThrough}` : working === "preview" ? "正在生成今日收益曲线" : "进入页面后会自动回放今日数据"}</p></div>{preview && <span className="strategy-source-chip">{preview.isStale ? "上次成功回放" : "真实分钟回放"}</span>}</div>
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
      </> : <div className="strategy-replay-history">
        <aside className="strategy-replay-list"><div className="strategy-replay-list-heading"><strong>已保存回放</strong><span>{previewHistory.length} 个版本</span></div>{previewHistory.map((record) => <button className={selectedHistory?.id === record.id ? "active" : ""} type="button" key={record.id} onClick={() => setSelectedHistoryId(record.id)}><span>{record.date}<i>{record.sessionStatus === "closed" ? "已收盘" : `至 ${record.verifiedThrough}`}</i></span><strong>{record.strategyName}</strong><div><em className={record.preview.returnPct >= 0 ? "up" : "down"}>{formatPercent(record.preview.returnPct, 3)}</em><small>{record.preview.tradesFilled} 笔成交</small></div></button>)}{!previewHistory.length && <div className="strategy-empty">还没有保存的每日回放。完成一次“回放今日数据”后会自动出现在这里。</div>}</aside>
        <div className="strategy-replay-detail">{selectedHistory ? <>
          <div className="strategy-history-title"><div><span>{selectedHistory.date} · {selectedHistory.verifiedThrough}</span><h3>{selectedHistory.strategyName}</h3><p>{selectedHistory.sessionStatus === "closed" ? "完整收盘回放" : "盘中保存版本"} · 保存于 {selectedHistory.savedAt.replace("T", " ")}</p></div><span className="strategy-source-chip">已保存 · 不重算</span></div>
          <div className="strategy-account-grid history-summary">
            <article className="featured"><span>期末组合价值</span><strong>{money(selectedHistory.preview.portfolioValue)}</strong><em className={selectedHistory.preview.returnPct >= 0 ? "up" : "down"}>{formatPercent(selectedHistory.preview.returnPct, 3)}</em></article>
            <article><span>中证全指同期</span><strong className={selectedHistory.preview.benchmarkReturnPct >= 0 ? "up" : "down"}>{formatPercent(selectedHistory.preview.benchmarkReturnPct, 3)}</strong><small>超额 {formatPercent(selectedHistory.preview.returnPct - selectedHistory.preview.benchmarkReturnPct, 3)}</small></article>
            <article><span>信号 / 成交</span><strong>{selectedHistory.preview.signalsMatched} / {selectedHistory.preview.tradesFilled}</strong><small>{selectedHistory.preview.failedOrders} 个未成交</small></article>
            <article><span>费用与滑点</span><strong>{money(selectedHistory.preview.fees)}</strong><small>期末现金 {money(selectedHistory.preview.cash)}</small></article>
          </div>
          <div className={`strategy-next-open-summary ${selectedHistory.preview.nextOpenStatus || "pending"}`}><div><span className="eyebrow">NEXT SESSION OPEN</span><strong>次一交易日开盘观察</strong><p>{selectedHistory.preview.nextOpenDate ? `${selectedHistory.preview.nextOpenDate} 已用真实日线开盘价补录` : "下一交易日开盘后自动补录；不会用估算价格代替"}</p></div><div><span>若开盘卖出 · 组合含成本收益</span><strong className={(selectedHistory.preview.nextOpenReturnPct || 0) >= 0 ? "up" : "down"}>{selectedHistory.preview.nextOpenReturnPct === undefined ? "等待开盘" : formatPercent(selectedHistory.preview.nextOpenReturnPct, 3)}</strong></div><div><span>中证全指隔夜开盘</span><strong className={(selectedHistory.preview.benchmarkNextOpenGapPct || 0) >= 0 ? "up" : "down"}>{selectedHistory.preview.benchmarkNextOpenGapPct === undefined ? "等待开盘" : formatPercent(selectedHistory.preview.benchmarkNextOpenGapPct, 3)}</strong></div><div><span>补录进度</span><strong>{selectedHistory.preview.nextOpenCompletedTrades || 0} / {selectedHistory.preview.tradesFilled}</strong></div></div>
          <div className="strategy-chart-panel history-replay-chart"><div className="strategy-chart-legend"><span><i className="portfolio" />模拟组合</span><span><i className="benchmark" />中证全指</span><span><i className="buy" />买入</span></div><StrategyEquityChart points={selectedHistory.preview.equity} events={selectedHistory.preview.events} label={`${selectedHistory.date} 历史策略回放`} /></div>
          <div className="strategy-history-rule"><strong>当日策略</strong><p>{selectedHistory.strategySummary}</p></div>
          <div className="strategy-history-trades"><div className="strategy-subheading"><h3>当日模拟成交</h3><span>{selectedHistory.preview.tradesFilled} 笔</span></div><div className="strategy-history-trade-list">{selectedHistory.preview.trades.map((trade) => <Link key={`${trade.code}-${replayEntryTime(trade)}`} href={`/stocks/${trade.code}?market=${trade.market}&name=${encodeURIComponent(trade.name)}`}><div><strong>{trade.name}</strong><small>{trade.code} · {trade.modelLabel || "策略信号"} · {trade.signalTime}</small></div><div><span>{replayEntryTime(trade)} 买入 {trade.quantity}股</span><strong>{replayEntryPrice(trade)}</strong></div><div><span>当日收盘浮盈</span><strong className={(trade.unrealizedReturn || 0) >= 0 ? "up" : "down"}>{formatPercent(trade.unrealizedReturn || 0, 3)}</strong></div><div><span>{trade.nextOpenDate ? `${trade.nextOpenDate} 开盘` : "次日开盘"} · {replayNextOpenPrice(trade)}</span><strong className={(trade.nextOpenReturnAfterCostPct || 0) >= 0 ? "up" : "down"}>{trade.nextOpenReturnAfterCostPct === undefined ? "等待真实数据" : `${formatPercent(trade.nextOpenReturnAfterCostPct, 3)} · 若卖出`}</strong></div></Link>)}{!selectedHistory.preview.trades.length && <div className="strategy-empty">这个回放没有产生模拟成交。</div>}</div></div>
        </> : <div className="strategy-empty">选择一个保存日期查看完整回放。</div>}</div>
      </div>}
    </section>

    <section className="strategy-history-section">
      <div className="strategy-account-heading"><div><span className="eyebrow">PAPER ACCOUNT</span><h2>持续模拟账户</h2><p>{account ? `从 ${account.startedAt.replace("T", " ")} 起保存；处理至 ${account.lastProcessedDate} ${account.lastProcessedTime}` : "开始持续模拟后，现金、持仓与事件会保存在本机。"}</p></div>{account && <span className={`strategy-source-chip ${account.status}`}>{account.status === "running" ? "已启用 · 等待新信号" : "已暂停"}</span>}</div>
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
