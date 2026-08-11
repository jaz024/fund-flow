"use client";

import { useEffect, useMemo, useRef } from "react";
import type { HistoryData } from "../lib/types";
import { formatYi } from "../lib/types";

const FONT = '"PingFang SC", "Microsoft YaHei", sans-serif';

export default function TrendChart({ history }: { history: HistoryData }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const summary = useMemo(() => {
    const values = history.points.map((item) => item.mainFlow);
    const total = values.reduce((sum, value) => sum + value, 0);
    const positiveDays = values.filter((value) => value > 0).length;
    const latestPoint = history.points[history.points.length - 1];
    const latest = latestPoint?.mainFlow || 0;
    return { total, positiveDays, latest, latestDate: latestPoint?.date || "", count: values.length };
  }, [history]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !history.points.length) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(720, Math.round(rect.width * dpr));
      canvas.height = Math.max(420, Math.round(rect.height * dpr));
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const width = canvas.width;
      const height = canvas.height;
      const margin = { left: 72 * dpr, right: 30 * dpr, top: 32 * dpr, bottom: 52 * dpr };
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#0d1117";
      ctx.fillRect(0, 0, width, height);
      const allValues = history.points.flatMap((point) => [point.mainFlow, point.ma5 || 0, point.ma20 || 0]);
      const maxAbs = Math.max(...allValues.map((value) => Math.abs(value)), 100_000_000) * 1.12;
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const xAt = (index: number) => margin.left + (index / Math.max(history.points.length - 1, 1)) * plotWidth;
      const yAt = (value: number) => margin.top + ((maxAbs - value) / (maxAbs * 2)) * plotHeight;

      ctx.font = `${11 * dpr}px ${FONT}`;
      for (let tick = -2; tick <= 2; tick += 1) {
        const value = (maxAbs / 2) * tick;
        const y = yAt(value);
        ctx.strokeStyle = tick === 0 ? "rgba(255,255,255,.22)" : "rgba(255,255,255,.07)";
        ctx.lineWidth = tick === 0 ? 1.4 * dpr : dpr;
        ctx.beginPath();
        ctx.moveTo(margin.left, y);
        ctx.lineTo(width - margin.right, y);
        ctx.stroke();
        ctx.fillStyle = "rgba(204,211,222,.56)";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(formatYi(value, false), margin.left - 11 * dpr, y);
      }

      history.points.forEach((point, index) => {
        const barWidth = Math.max(2 * dpr, plotWidth / history.points.length - 2 * dpr);
        const x = xAt(index) - barWidth / 2;
        const zero = yAt(0);
        const y = yAt(point.mainFlow);
        ctx.fillStyle = point.mainFlow >= 0 ? "rgba(255,82,88,.34)" : "rgba(39,211,111,.34)";
        ctx.fillRect(x, Math.min(y, zero), barWidth, Math.max(Math.abs(zero - y), 1));
      });

      const drawLine = (key: "ma5" | "ma20", color: string) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.4 * dpr;
        ctx.lineJoin = "round";
        ctx.beginPath();
        let started = false;
        history.points.forEach((point, index) => {
          const value = point[key];
          if (value == null) return;
          const x = xAt(index);
          const y = yAt(value);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else ctx.lineTo(x, y);
        });
        ctx.stroke();
      };
      drawLine("ma5", "#f3c959");
      drawLine("ma20", "#6da8ff");

      const labelStep = Math.max(1, Math.floor(history.points.length / 6));
      history.points.forEach((point, index) => {
        if (index % labelStep !== 0 && index !== history.points.length - 1) return;
        ctx.fillStyle = "rgba(204,211,222,.5)";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(point.date.slice(5), xAt(index), height - margin.bottom + 14 * dpr);
      });
    };
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [history]);

  return (
    <div className="trend-chart-wrap">
      <div className="trend-metrics">
        <div>
          <span>最新历史日净流入<small>{summary.latestDate ? ` · ${summary.latestDate.slice(5)}` : ""}</small></span>
          <strong className={summary.latest >= 0 ? "up" : "down"}>{formatYi(summary.latest)}</strong>
        </div>
        <div><span>三个月累计</span><strong className={summary.total >= 0 ? "up" : "down"}>{formatYi(summary.total)}</strong></div>
        <div><span>净流入交易日</span><strong>{summary.positiveDays}<small> / {summary.count} 天</small></strong></div>
      </div>
      <div className="trend-legend">
        <span><i className="legend-bar" />每日资金净流入</span>
        <span><i style={{ background: "#f3c959" }} />5日均线</span>
        <span><i style={{ background: "#6da8ff" }} />20日均线</span>
      </div>
      <canvas ref={canvasRef} className="trend-canvas" aria-label={`${history.name}近三个月资金趋势`} />
    </div>
  );
}
