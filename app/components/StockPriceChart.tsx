"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { StockDailyPoint, StockMinutePoint } from "../lib/types";

type Props =
  | { mode: "intraday"; intraday: StockMinutePoint[]; daily?: never }
  | { mode: "daily"; daily: StockDailyPoint[]; intraday?: never };

type Series = { label: string; color: string; values: Array<number | null> };

export default function StockPriceChart(props: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const { labels, series } = useMemo<{ labels: string[]; series: Series[] }>(() => {
    if (props.mode === "intraday") {
      return {
        labels: props.intraday.map((point) => point.time),
        series: [
          { label: "成交价", color: "#68a2ff", values: props.intraday.map((point) => point.price) },
          { label: "当日均价", color: "#f2c653", values: props.intraday.map((point) => point.average || null) },
        ],
      };
    }
    return {
      labels: props.daily.map((point) => point.date.slice(5)),
      series: [
        { label: "收盘价", color: "#e7edf7", values: props.daily.map((point) => point.close) },
        { label: "5 日均线", color: "#f2c653", values: props.daily.map((point) => point.ma5) },
        { label: "20 日均线", color: "#68a2ff", values: props.daily.map((point) => point.ma20) },
      ],
    };
  }, [props]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !labels.length) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(rect.width, 320);
      const height = Math.max(rect.height, 330);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const plot = { left: 58, right: width - 24, top: 35, bottom: height - 45 };
      const allValues = series.flatMap((item) => item.values.filter((value): value is number => value !== null && value > 0));
      const rawMin = Math.min(...allValues);
      const rawMax = Math.max(...allValues);
      const padding = Math.max((rawMax - rawMin) * 0.12, rawMax * 0.002, 0.01);
      const minimum = rawMin - padding;
      const maximum = rawMax + padding;
      const x = (index: number) => plot.left + (index / Math.max(labels.length - 1, 1)) * (plot.right - plot.left);
      const y = (value: number) => plot.top + ((maximum - value) / Math.max(maximum - minimum, 0.0001)) * (plot.bottom - plot.top);

      context.font = "10px Inter, system-ui, sans-serif";
      context.textBaseline = "middle";
      for (let line = 0; line <= 4; line += 1) {
        const lineY = plot.top + (line / 4) * (plot.bottom - plot.top);
        const value = maximum - (line / 4) * (maximum - minimum);
        context.strokeStyle = "rgba(255,255,255,.07)";
        context.beginPath();
        context.moveTo(plot.left, lineY);
        context.lineTo(plot.right, lineY);
        context.stroke();
        context.fillStyle = "#687383";
        context.textAlign = "right";
        context.fillText(value.toFixed(2), plot.left - 9, lineY);
      }

      series.forEach((item) => {
        context.beginPath();
        let drawing = false;
        item.values.forEach((value, index) => {
          if (value === null || value <= 0) {
            drawing = false;
            return;
          }
          if (!drawing) context.moveTo(x(index), y(value));
          else context.lineTo(x(index), y(value));
          drawing = true;
        });
        context.strokeStyle = item.color;
        context.lineWidth = item.label === "成交价" || item.label === "收盘价" ? 2 : 1.7;
        context.lineJoin = "round";
        context.stroke();
      });

      [0, Math.floor((labels.length - 1) / 2), labels.length - 1].forEach((index) => {
        context.fillStyle = "#687383";
        context.textAlign = index === 0 ? "left" : index === labels.length - 1 ? "right" : "center";
        context.fillText(labels[index], x(index), plot.bottom + 24);
      });

      if (hover !== null && labels[hover]) {
        const pointX = x(hover);
        context.strokeStyle = "rgba(255,255,255,.25)";
        context.setLineDash([4, 4]);
        context.beginPath();
        context.moveTo(pointX, plot.top);
        context.lineTo(pointX, plot.bottom);
        context.stroke();
        context.setLineDash([]);
        const values = series
          .map((item) => item.values[hover] ? `${item.label} ${item.values[hover]?.toFixed(2)}` : "")
          .filter(Boolean);
        const text = `${labels[hover]} · ${values.join(" · ")}`;
        context.font = "600 10px Inter, system-ui, sans-serif";
        const boxWidth = Math.min(width - 30, context.measureText(text).width + 20);
        const left = Math.min(plot.right - boxWidth, Math.max(plot.left, pointX - boxWidth / 2));
        context.fillStyle = "rgba(14,19,27,.97)";
        context.beginPath();
        context.roundRect(left, 3, boxWidth, 25, 6);
        context.fill();
        context.fillStyle = "#eef2f8";
        context.textAlign = "center";
        context.fillText(text, left + boxWidth / 2, 15.5);
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [hover, labels, series]);

  return (
    <div className="stock-price-chart">
      <div className="stock-chart-legend">
        {series.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}
      </div>
      <canvas
        ref={canvasRef}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const relative = Math.min(1, Math.max(0, (event.clientX - rect.left - 58) / Math.max(rect.width - 82, 1)));
          setHover(Math.round(relative * Math.max(labels.length - 1, 0)));
        }}
        onMouseLeave={() => setHover(null)}
      />
    </div>
  );
}
