"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReplayFrame } from "../lib/types";

const COLORS = ["#ff695f", "#f7c85c", "#49d6b3", "#6ca8ff", "#bf8cff"];
const FONT = '"PingFang SC", "Microsoft YaHei", sans-serif';

type Props = { frames: ReplayFrame[] };

export default function FlowLineChart({ frames }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const candidates = useMemo(() => {
    const latest = frames[frames.length - 1];
    if (!latest) return [];
    return [...latest.inflow, ...latest.outflow]
      .sort((a, b) => Math.abs(b.mainFlow) - Math.abs(a.mainFlow))
      .slice(0, 10);
  }, [frames]);
  const [selected, setSelected] = useState<string[]>(() =>
    candidates.slice(0, 5).map((item) => item.code),
  );

  const activeSelection = useMemo(() => {
    const available = selected.filter((code) => candidates.some((item) => item.code === code));
    return available.length ? available.slice(0, 5) : candidates.slice(0, 5).map((item) => item.code);
  }, [candidates, selected]);

  const series = useMemo(() => {
    return activeSelection.map((code, index) => {
      const fallbackName = candidates.find((item) => item.code === code)?.name || code;
      let lastValue = 0;
      const points = frames.map((frame) => {
        const board = [...frame.inflow, ...frame.outflow].find((item) => item.code === code);
        if (board) lastValue = board.mainFlow / 100_000_000;
        return lastValue;
      });
      return { code, name: fallbackName, color: COLORS[index % COLORS.length], points };
    });
  }, [activeSelection, candidates, frames]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !frames.length || !series.length) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(600, Math.round(rect.width * dpr));
      canvas.height = Math.max(320, Math.round(rect.height * dpr));
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const width = canvas.width;
      const height = canvas.height;
      const margin = { left: 64 * dpr, right: 24 * dpr, top: 28 * dpr, bottom: 44 * dpr };
      ctx.clearRect(0, 0, width, height);
      const background = ctx.createLinearGradient(0, 0, 0, height);
      background.addColorStop(0, "rgba(20,24,31,.9)");
      background.addColorStop(1, "rgba(11,14,19,.94)");
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, width, height);
      const values = series.flatMap((item) => item.points);
      const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value))) * 1.12;
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const xAt = (index: number) => margin.left + (index / Math.max(frames.length - 1, 1)) * plotWidth;
      const yAt = (value: number) => margin.top + ((maxAbs - value) / (maxAbs * 2)) * plotHeight;

      ctx.font = `${11 * dpr}px ${FONT}`;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      for (let tick = -2; tick <= 2; tick += 1) {
        const value = (maxAbs / 2) * tick;
        const y = yAt(value);
        ctx.strokeStyle = tick === 0 ? "rgba(255,255,255,.2)" : "rgba(255,255,255,.07)";
        ctx.lineWidth = tick === 0 ? 1.5 * dpr : dpr;
        ctx.beginPath();
        ctx.moveTo(margin.left, y);
        ctx.lineTo(width - margin.right, y);
        ctx.stroke();
        ctx.fillStyle = "rgba(211,218,228,.55)";
        ctx.fillText(`${value.toFixed(0)}亿`, margin.left - 10 * dpr, y);
      }

      frames.forEach((frame, index) => {
        if (index % Math.max(1, Math.floor(frames.length / 6)) !== 0 && index !== frames.length - 1) return;
        const x = xAt(index);
        ctx.fillStyle = "rgba(211,218,228,.5)";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(frame.time, x, height - margin.bottom + 12 * dpr);
      });

      series.forEach((item) => {
        ctx.strokeStyle = item.color;
        ctx.lineWidth = 2.4 * dpr;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.shadowColor = item.color;
        ctx.shadowBlur = 7 * dpr;
        ctx.beginPath();
        item.points.forEach((value, index) => {
          const x = xAt(index);
          const y = yAt(value);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.shadowBlur = 0;
      });
    };
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [frames, series]);

  const toggle = (code: string) => {
    setSelected((current) => {
      if (current.includes(code)) return current.filter((item) => item !== code);
      if (current.length >= 5) return [...current.slice(1), code];
      return [...current, code];
    });
  };

  return (
    <div className="flow-line-chart">
      <div className="chart-selector" aria-label="选择图表板块">
        {candidates.map((item) => {
          const activeIndex = activeSelection.indexOf(item.code);
          return (
            <button
              type="button"
              key={item.code}
              className={activeIndex >= 0 ? "sector-chip active" : "sector-chip"}
              style={activeIndex >= 0 ? { "--chip-color": COLORS[activeIndex] } as React.CSSProperties : undefined}
              onClick={() => toggle(item.code)}
            >
              {item.name}
            </button>
          );
        })}
      </div>
      <canvas ref={canvasRef} className="line-canvas" aria-label="今日板块资金净流入分时折线图" />
    </div>
  );
}
