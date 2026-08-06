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

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: "本地服务未响应" }));
    throw new Error(payload.error || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}
