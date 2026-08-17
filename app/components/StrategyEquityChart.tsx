"use client";

import { useEffect, useRef } from "react";
import type { StrategyLabEquityPoint, StrategyLabEvent } from "../lib/types";

function keyOf(date: string, time: string) {
  return `${date} ${time}`;
}

export default function StrategyEquityChart({
  points,
  events,
  label,
}: {
  points: StrategyLabEquityPoint[];
  events: StrategyLabEvent[];
  label: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * ratio));
      canvas.height = Math.max(1, Math.round(rect.height * ratio));
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      const width = rect.width;
      const height = rect.height;
      context.clearRect(0, 0, width, height);
      context.fillStyle = "#0b1016";
      context.fillRect(0, 0, width, height);
      if (!points.length) {
        context.fillStyle = "#657080";
        context.font = '12px "PingFang SC", sans-serif';
        context.textAlign = "center";
        context.fillText("运行今日回放或开始持续模拟后显示净值曲线", width / 2, height / 2);
        return;
      }
      const pad = { left: 58, right: 24, top: 28, bottom: 40 };
      const plotWidth = width - pad.left - pad.right;
      const plotHeight = height - pad.top - pad.bottom;
      const values = points.flatMap((point) => [point.returnPct, point.benchmarkReturnPct]);
      const spread = Math.max(...values.map(Math.abs), 0.35);
      const minimum = -spread * 1.18;
      const maximum = spread * 1.18;
      const x = (index: number) => pad.left + (points.length === 1 ? plotWidth / 2 : index / (points.length - 1) * plotWidth);
      const y = (value: number) => pad.top + (maximum - value) / (maximum - minimum) * plotHeight;

      context.lineWidth = 1;
      context.font = '10px ui-monospace, SFMono-Regular, monospace';
      context.textAlign = "right";
      for (let index = 0; index <= 4; index += 1) {
        const value = maximum - index / 4 * (maximum - minimum);
        const yPosition = y(value);
        context.strokeStyle = value === 0 ? "rgba(255,255,255,.18)" : "rgba(255,255,255,.065)";
        context.beginPath();
        context.moveTo(pad.left, yPosition);
        context.lineTo(width - pad.right, yPosition);
        context.stroke();
        context.fillStyle = "#657080";
        context.fillText(`${value > 0 ? "+" : ""}${value.toFixed(2)}%`, pad.left - 9, yPosition + 3);
      }

      const line = (field: "returnPct" | "benchmarkReturnPct", color: string, widthValue: number) => {
        context.beginPath();
        points.forEach((point, index) => {
          const px = x(index);
          const py = y(point[field]);
          if (!index) context.moveTo(px, py);
          else context.lineTo(px, py);
        });
        context.strokeStyle = color;
        context.lineWidth = widthValue;
        context.lineJoin = "round";
        context.lineCap = "round";
        context.stroke();
      };
      line("benchmarkReturnPct", "#657080", 1.5);
      line("returnPct", "#6f9fff", 2.4);

      const pointKeys = points.map((point) => keyOf(point.date, point.time));
      const visibleEvents = events
        .filter((event) => ["buy", "sell", "strategy_started", "strategy_changed"].includes(event.type))
        .sort((left, right) => keyOf(left.date, left.time).localeCompare(keyOf(right.date, right.time)))
        .slice(-18);
      visibleEvents.forEach((event, eventIndex) => {
        const eventKey = keyOf(event.date, event.time);
        let nearest = 0;
        let nearestDistance = Number.POSITIVE_INFINITY;
        pointKeys.forEach((pointKey, index) => {
          const distance = Math.abs(pointKey.localeCompare(eventKey));
          if (pointKey <= eventKey && distance <= nearestDistance) {
            nearest = index;
            nearestDistance = distance;
          }
        });
        const px = x(nearest);
        const py = y(points[nearest].returnPct);
        const color = event.type === "buy" ? "#ff5b62" : event.type === "sell" ? "#2ed879" : "#f1c766";
        context.fillStyle = color;
        context.beginPath();
        context.arc(px, py, 4, 0, Math.PI * 2);
        context.fill();
        if (visibleEvents.length <= 10 || eventIndex % 2 === 0) {
          context.font = '9px "PingFang SC", sans-serif';
          context.textAlign = "center";
          context.fillStyle = color;
          const text = event.type === "buy" ? `买 ${event.name}` : event.type === "sell" ? `卖 ${event.name}` : "规则变更";
          context.fillText(text.slice(0, 9), px, py - 10 - (eventIndex % 2) * 11);
        }
      });

      const ticks = Math.min(5, points.length);
      context.fillStyle = "#657080";
      context.textAlign = "center";
      context.font = '9px ui-monospace, SFMono-Regular, monospace';
      for (let index = 0; index < ticks; index += 1) {
        const pointIndex = ticks === 1 ? 0 : Math.round(index / (ticks - 1) * (points.length - 1));
        const point = points[pointIndex];
        const text = points[0].date === points.at(-1)?.date ? point.time : `${point.date.slice(5)} ${point.time}`;
        context.fillText(text, x(pointIndex), height - 15);
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [events, points]);

  return <canvas ref={canvasRef} className="strategy-equity-canvas" aria-label={label} />;
}
