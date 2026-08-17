export type BoardFlow = {
  code: string;
  name: string;
  category: string;
  price: number;
  changePct: number;
  mainFlow: number;
  superFlow?: number;
  largeFlow?: number;
  mediumFlow?: number;
  smallFlow?: number;
};

export type MarketIndex = {
  code: string;
  name: string;
  price: number;
  changePct: number;
};

export type OverviewData = {
  date: string;
  updatedAt: string;
  source: string;
  isDemo: boolean;
  warning: string;
  indexes: MarketIndex[];
  boards: BoardFlow[];
  topIn: BoardFlow[];
  topOut: BoardFlow[];
};

export type ReplayFrame = {
  time: string;
  boards: BoardFlow[];
  inflow: BoardFlow[];
  outflow: BoardFlow[];
};

export type ReplayData = {
  schemaVersion?: number;
  date: string;
  updatedAt: string;
  source: string;
  isDemo: boolean;
  warning: string;
  indexes: MarketIndex[];
  verifiedThrough?: string;
  capturedSlots?: number;
  coveragePercent?: number;
  frames: ReplayFrame[];
};

export type HistoryPoint = {
  date: string;
  mainFlow: number;
  ma5: number | null;
  ma20: number | null;
};

export type HistoryData = {
  code: string;
  name: string;
  source: string;
  isDemo: boolean;
  points: HistoryPoint[];
};

export type StockRankItem = {
  code: string;
  market: number;
  name: string;
  price: number;
  changePct: number;
  turnover: number;
  preClose: number;
  speed1m?: number;
  asOf: string;
};

export type MarketMinutePoint = {
  date: string;
  time: string;
  value: number;
  changePct: number;
};

export type StockEvent = {
  code: string;
  market: number;
  name: string;
  time: string;
  eventType: number;
  event: string;
  direction: 1 | -1;
  severity: number;
  price: number;
};

export type StockMarketData = {
  date: string;
  updatedAt: string;
  verifiedThrough: string;
  source: string;
  isDemo: false;
  isStale: boolean;
  warning: string;
  index: {
    code: string;
    name: string;
    preClose: number;
    points: MarketMinutePoint[];
  };
  events: StockEvent[];
  fastestRise: StockRankItem[];
  fastestFall: StockRankItem[];
  highestTurnover: StockRankItem[];
  rankingMethod?: string;
};

export type StockMinutePoint = {
  date: string;
  time: string;
  open: number;
  price: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
  average: number;
};

export type StockDailyPoint = {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
  changePct: number;
  turnover: number;
  ma5: number | null;
  ma20: number | null;
};

export type StockQuote = {
  code: string;
  market: number;
  name: string;
  price: number;
  changePct: number;
  high: number;
  low: number;
  open: number;
  preClose: number;
  volume: number;
  amount: number;
  turnover: number;
  pe: number;
  pb: number;
  marketCap: number;
  floatMarketCap: number;
  close: number;
  dayRange: [number, number];
  bidPrice: number;
  bidVolume: number;
  askPrice: number;
  askVolume: number;
  bidLevels: Array<{ level: number; price: number; volume: number }>;
  askLevels: Array<{ level: number; price: number; volume: number }>;
  sourceTime: string;
};

export type StockDetailData = {
  date: string;
  updatedAt: string;
  verifiedThrough: string;
  source: string;
  isDemo: false;
  isStale: boolean;
  warning: string;
  quote: StockQuote;
  intraday: StockMinutePoint[];
  daily: StockDailyPoint[];
  dailyAdjustment: string;
  verifiedSources: string[];
  verificationNotes: string[];
};

export type StrategyTrade = {
  date: string; code: string; market: number; name: string; signalTime: string; event: string;
  oneMinuteReturn: number; industryName: string; sectorChangePct: number; sectorMainFlow: number;
  score: number; allocation: number; status: "pending_execution" | "open" | "unfilled" | "complete";
  reason: string; executionTime: string; executionPrice: number; currentTime: string; currentPrice: number;
  closePrice: number; nextOpenPrice: number; next0931Price: number;
  currentReturn: number | null; currentReturnAfterCost: number | null;
  closeReturn: number | null; closeReturnAfterCost: number | null;
  nextOpenReturn: number | null; nextOpenReturnAfterCost: number | null;
  next0931Return: number | null; next0931ReturnAfterCost: number | null;
  indexCurrentReturn: number | null; indexCloseReturn: number | null;
  indexNextOpenReturn: number | null; indexNext0931Return: number | null;
};

export type StrategyData = {
  date: string; verifiedThrough: string; updatedAt: string; mode: "观察性模拟"; isDemo: false;
  captureStatus: "live" | "closed"; captureMessage: string;
  replayStatus: "complete" | "partial" | "unavailable";
  replayProcessedThrough: string; replaySourceEvents: number; replayUnresolved: number;
  signalsToday: number; tradesToday: number; filledToday: number; unfilledToday: number;
  remainingSlots: number; cashWeight: number; livePortfolioReturn: number;
  liveBenchmarkReturn: number; liveAlpha: number; threshold: number;
  summary: {
    closeMean: number | null; closeMedian: number | null; closeWinRate: number | null;
    nextOpenMean: number | null; nextOpenMedian: number | null; nextOpenWinRate: number | null;
    next0931Mean: number | null; next0931Median: number | null; next0931WinRate: number | null;
    completedClose: number; completedNext: number; completedNextOpen: number;
    closeBenchmarkMean: number | null; closeAlphaMean: number | null;
    nextOpenBenchmarkMean: number | null; nextOpenAlphaMean: number | null;
    next0931BenchmarkMean: number | null; next0931AlphaMean: number | null;
  };
  costs: {
    commissionRate: number; regulatoryAndTransferRate: number; stampDutyRate: number;
    slippagePerSide: number; entryEstimate: number; roundTripEstimate: number;
  };
  trades: StrategyTrade[]; method: string;
};

export type StrategyLabConfig = {
  name: string;
  marketScope: "all" | "sh" | "sz" | "bj";
  oneMinuteRise: number;
  sectorFilter: "both" | "flow" | "rise" | "none";
  minAmount: number;
  minScore: number;
  startTime: string;
  endTime: string;
  buyDelayMinutes: 1 | 2 | 5;
  entryPriceMode: "minute_open" | "minute_close" | "minute_average";
  allocationMode: "fixed_pct" | "equal_slots";
  positionPct: number;
  maxPositions: number;
  exitMode: "next_open" | "next_0931" | "risk_close" | "hold";
  takeProfitPct: number;
  stopLossPct: number;
  initialCapital: number;
};

export type StrategyLabEquityPoint = {
  date: string; time: string; portfolioValue: number; cash: number;
  marketValue: number; returnPct: number; benchmarkReturnPct: number;
};

export type StrategyLabEvent = {
  id?: number; date: string; time: string; type: string; code: string; name: string;
  title: string; detail: string; price: number; quantity: number; amount?: number;
  strategyVersionId?: number;
};

export type StrategyLabPosition = {
  id?: number; code: string; market: number; name: string; quantity: number;
  entryDate?: string; entryTime: string; entryPrice: number; rawExecutionPrice?: number;
  entryCost: number; status: "open" | "closed"; lastPrice?: number;
  lastPriceTime?: string; currentPrice?: number; currentValue?: number; pnl?: number;
  unrealizedPnl?: number; returnPct?: number; unrealizedReturn?: number;
  exitDate?: string; exitTime?: string; exitPrice?: number; exitCost?: number;
  signalTime?: string; oneMinuteReturn?: number; industryName?: string;
  sectorChangePct?: number; sectorMainFlow?: number; score?: number;
  strategyVersionId?: number; exitMode?: string;
};

export type StrategyLabPreview = {
  date: string; verifiedThrough: string; initialCapital: number; portfolioValue: number;
  cash: number; marketValue: number; returnPct: number; benchmarkReturnPct: number;
  fees: number; signalsMatched: number; tradesFilled: number; failedOrders: number;
  openPositions: number; winningPositions: number; trades: StrategyLabPosition[];
  events: StrategyLabEvent[]; equity: StrategyLabEquityPoint[]; notice: string;
};

export type StrategyLabData = {
  date: string; verifiedThrough: string; updatedAt: string; source: string;
  defaultConfig: StrategyLabConfig; activeConfig: StrategyLabConfig;
  strategySummary: string; preview: StrategyLabPreview | null;
  account: null | {
    status: "running" | "paused"; initialCash: number; cash: number;
    portfolioValue: number; marketValue: number; returnPct: number;
    realizedPnl: number; unrealizedPnl: number; openPositions: number;
    closedTrades: number; startedAt: string; activeVersionId: number;
    lastProcessedDate: string; lastProcessedTime: string;
  };
  positions: StrategyLabPosition[];
  versions: Array<{
    id: number; createdAt: string; effectiveDate: string; effectiveTime: string;
    summary: string; config: StrategyLabConfig;
  }>;
  events: StrategyLabEvent[]; equity: StrategyLabEquityPoint[];
  costs: {
    commissionRate: number; regulatoryAndTransferRate: number;
    stampDutyRate: number; slippagePerSide: number;
  };
  dataNotice: string;
};

export const API_BASE = "http://127.0.0.1:8765";

export function formatYi(value: number, signed = true): string {
  const yi = value / 100_000_000;
  const prefix = signed && yi > 0 ? "+" : "";
  const digits = Math.abs(yi) >= 100 ? 0 : Math.abs(yi) >= 10 ? 1 : 2;
  return `${prefix}${yi.toFixed(digits)}亿`;
}

export function shortDate(value: string): string {
  const parts = value.split("-");
  if (parts.length !== 3) return value;
  return `${Number(parts[1])}月${Number(parts[2])}日`;
}

export function formatPercent(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function formatWanYi(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (absolute >= 10_000) return `${(value / 10_000).toFixed(0)}万`;
  return value.toFixed(0);
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: "本地服务未响应" }));
    throw new Error(payload.error || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({ error: "本地服务未响应" }));
    throw new Error(result.error || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}
