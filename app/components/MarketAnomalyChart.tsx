"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { MarketMinutePoint, StockEvent } from "../lib/types";
import { formatPercent } from "../lib/types";

type Props = {
  name: string;
  preClose: number;
  points: MarketMinutePoint[];
  events: StockEvent[];
};

type LabelHit = { left: number; top: number; right: number; bottom: number; event: StockEvent };

const WINDOWS = [Number.POSITIVE_INFINITY, 160, 90, 45];
const EVENT_LIMITS = [12, 22, 36, 52];

function stockHref(event: StockEvent): string {
  return `/stocks/${event.code}?market=${event.market}&name=${encodeURIComponent(event.name)}`;
}

function nearestPointIndex(points: MarketMinutePoint[], time: string): number {
  let best = 0;
  let distance = Number.POSITIVE_INFINITY;
  const target = Number(time.replace(":", ""));
  points.forEach((point, index) => {
    const current = Number(point.time.replace(":", ""));
    const nextDistance = Math.abs(current - target);
    if (nextDistance < distance) {
      distance = nextDistance;
      best = index;
    }
  });
  return best;
}

export default function MarketAnomalyChart({ name, preClose, points, events }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hitsRef = useRef<LabelHit[]>([]);
  const dragRef = useRef({ active: false, startX: 0, startEnd: 0, moved: false });
  const router = useRouter();
  const [zoom, setZoom] = useState(0);
  const [endIndex, setEndIndex] = useState(Number.MAX_SAFE_INTEGER);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const visible = useMemo(() => {
    const count = Math.min(WINDOWS[zoom], points.length);
    const end = Math.min(Math.max(endIndex, count - 1), Math.max(points.length - 1, 0));
    const start = Math.max(0, end - count + 1);
    return { start, end, points: points.slice(start, end + 1) };
  }, [endIndex, points, zoom]);

  const selectedEvents = useMemo(() => {
    if (!visible.points.length) return [];
    const firstTime = visible.points[0].time;
    const lastTime = visible.points[visible.points.length - 1].time;
    const candidates = events
      .filter((event) => event.time >= firstTime && event.time <= lastTime)
      .map((event) => ({ event, index: nearestPointIndex(points, event.time) }))
      .filter((item) => item.index >= visible.start && item.index <= visible.end)
      .sort((left, right) => right.event.severity - left.event.severity);
    const minimumDistance = Math.max(1, Math.round(10 - zoom * 2));
    const perDirection = Math.max(1, Math.floor(EVENT_LIMITS[zoom] / 2));
    const chooseDirection = (direction: 1 | -1) => {
      const chosen: typeof candidates = [];
      for (const item of candidates.filter((candidate) => candidate.event.direction === direction)) {
        const clashes = chosen.some((existing) => Math.abs(existing.index - item.index) < minimumDistance);
        if (!clashes) chosen.push(item);
        if (chosen.length >= perDirection) break;
      }
      return chosen;
    };
    // Reserve the same label budget for rising and falling events. A limit-up
    // event has a large severity score, but must not crowd every green label
    // out of the full-day view.
    const chosen = [...chooseDirection(1), ...chooseDirection(-1)];
    return chosen.sort((left, right) => left.index - right.index);
  }, [events, points, visible, zoom]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible.points.length) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(320, rect.width);
      const height = Math.max(430, rect.height);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const plot = { left: 66, right: width - 22, top: 70, bottom: height - 50 };
      const values = visible.points.map((point) => point.value);
      const rawMin = Math.min(...values, preClose);
      const rawMax = Math.max(...values, preClose);
      const padding = Math.max((rawMax - rawMin) * 0.22, preClose * 0.0025, 1);
      const minimum = rawMin - padding;
      const maximum = rawMax + padding;
      const x = (index: number) => plot.left + (index / Math.max(visible.points.length - 1, 1)) * (plot.right - plot.left);
      const y = (value: number) => plot.top + ((maximum - value) / Math.max(maximum - minimum, 0.0001)) * (plot.bottom - plot.top);

      context.font = "11px Inter, system-ui, sans-serif";
      context.textBaseline = "middle";
      for (let line = 0; line <= 4; line += 1) {
        const ratioY = line / 4;
        const value = maximum - ratioY * (maximum - minimum);
        const lineY = plot.top + ratioY * (plot.bottom - plot.top);
        context.strokeStyle = Math.abs(value - preClose) < (maximum - minimum) / 7 ? "rgba(255,255,255,.17)" : "rgba(255,255,255,.065)";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(plot.left, lineY);
        context.lineTo(plot.right, lineY);
        context.stroke();
        context.fillStyle = "#687383";
        context.textAlign = "right";
        context.fillText(value.toFixed(2), plot.left - 10, lineY);
        context.textAlign = "left";
        context.fillText(formatPercent(preClose ? (value / preClose - 1) * 100 : 0), plot.right - 44, lineY - 12);
      }
      const baselineY = y(preClose);
      context.strokeStyle = "rgba(255,255,255,.22)";
      context.setLineDash([5, 5]);
      context.beginPath();
      context.moveTo(plot.left, baselineY);
      context.lineTo(plot.right, baselineY);
      context.stroke();
      context.setLineDash([]);
      context.fillStyle = "#8a94a2";
      context.textAlign = "left";
      context.fillText("0.00%", plot.right - 40, baselineY - 11);

      const gradient = context.createLinearGradient(0, plot.top, 0, plot.bottom);
      gradient.addColorStop(0, "rgba(75,137,255,.22)");
      gradient.addColorStop(1, "rgba(75,137,255,0)");
      context.beginPath();
      visible.points.forEach((point, index) => {
        if (index === 0) context.moveTo(x(index), y(point.value));
        else context.lineTo(x(index), y(point.value));
      });
      context.lineTo(plot.right, plot.bottom);
      context.lineTo(plot.left, plot.bottom);
      context.closePath();
      context.fillStyle = gradient;
      context.fill();

      context.beginPath();
      visible.points.forEach((point, index) => {
        if (index === 0) context.moveTo(x(index), y(point.value));
        else context.lineTo(x(index), y(point.value));
      });
      context.strokeStyle = "#629dff";
      context.lineWidth = 2.1;
      context.lineJoin = "round";
      context.stroke();

      hitsRef.current = [];
      const lanes = { up: 0, down: 0 };
      selectedEvents.forEach(({ event, index }) => {
        const localIndex = index - visible.start;
        const point = points[index];
        if (!point) return;
        const pointX = x(localIndex);
        const pointY = y(point.value);
        const positive = event.direction > 0;
        const laneKey = positive ? "up" : "down";
        const lane = lanes[laneKey] % 3;
        lanes[laneKey] += 1;
        const label = `${event.name} ${event.event}`;
        context.font = "600 10px Inter, system-ui, sans-serif";
        const boxWidth = Math.min(126, Math.max(68, context.measureText(label).width + 14));
        const boxHeight = 24;
        const offset = 38 + lane * 31;
        const boxLeft = Math.min(plot.right - boxWidth, Math.max(plot.left, pointX - boxWidth / 2));
        const rawTop = positive ? pointY - offset - boxHeight : pointY + offset;
        const boxTop = Math.min(plot.bottom - boxHeight, Math.max(plot.top, rawTop));
        const color = positive ? "#ff5b62" : "#2ed879";
        context.strokeStyle = positive ? "rgba(255,91,98,.72)" : "rgba(46,216,121,.68)";
        context.fillStyle = positive ? "rgba(63,26,30,.94)" : "rgba(18,52,38,.94)";
        context.lineWidth = 1;
        context.beginPath();
        context.roundRect(boxLeft, boxTop, boxWidth, boxHeight, 4);
        context.fill();
        context.stroke();
        context.strokeStyle = color;
        context.beginPath();
        context.moveTo(pointX, pointY);
        context.lineTo(Math.min(boxLeft + boxWidth - 8, Math.max(boxLeft + 8, pointX)), positive ? boxTop + boxHeight : boxTop);
        context.stroke();
        context.fillStyle = color;
        context.beginPath();
        context.arc(pointX, pointY, 3.5, 0, Math.PI * 2);
        context.fill();
        context.textAlign = "center";
        context.fillText(label, boxLeft + boxWidth / 2, boxTop + boxHeight / 2, boxWidth - 10);
        hitsRef.current.push({ left: boxLeft, top: boxTop, right: boxLeft + boxWidth, bottom: boxTop + boxHeight, event });
      });

      [0, Math.floor((visible.points.length - 1) / 2), visible.points.length - 1].forEach((index) => {
        const point = visible.points[index];
        if (!point) return;
        context.fillStyle = "#687383";
        context.font = "10px Inter, system-ui, sans-serif";
        context.textAlign = index === 0 ? "left" : index === visible.points.length - 1 ? "right" : "center";
        context.fillText(point.time, x(index), plot.bottom + 25);
      });

      if (hoverIndex !== null && visible.points[hoverIndex]) {
        const point = visible.points[hoverIndex];
        const pointX = x(hoverIndex);
        const pointY = y(point.value);
        context.strokeStyle = "rgba(255,255,255,.22)";
        context.setLineDash([4, 4]);
        context.beginPath();
        context.moveTo(pointX, plot.top);
        context.lineTo(pointX, plot.bottom);
        context.stroke();
        context.setLineDash([]);
        context.fillStyle = "#f4f7fb";
        context.beginPath();
        context.arc(pointX, pointY, 4, 0, Math.PI * 2);
        context.fill();
        const text = `${point.time}  ${point.value.toFixed(2)}  ${formatPercent(point.changePct)}`;
        context.font = "600 11px Inter, system-ui, sans-serif";
        const tooltipWidth = context.measureText(text).width + 20;
        const tooltipLeft = Math.min(plot.right - tooltipWidth, Math.max(plot.left, pointX - tooltipWidth / 2));
        context.fillStyle = "rgba(15,20,28,.96)";
        context.beginPath();
        context.roundRect(tooltipLeft, plot.top - 35, tooltipWidth, 26, 6);
        context.fill();
        context.fillStyle = "#e8edf5";
        context.textAlign = "center";
        context.fillText(text, tooltipLeft + tooltipWidth / 2, plot.top - 22);
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [hoverIndex, points, preClose, selectedEvents, visible]);

  const pointerPosition = (event: { clientX: number; clientY: number }) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  return (
    <div className="anomaly-chart-shell">
      <div className="chart-toolbar">
        <div>
          <strong>{name}</strong>
          <span>蓝线为全市场价格指数 · 标签点击进入个股详情</span>
        </div>
        <div className="zoom-control" aria-label="图表缩放">
          <button type="button" onClick={() => setZoom((value) => Math.max(0, value - 1))} disabled={zoom === 0}>−</button>
          <span>{zoom === 0 ? "全天" : `${WINDOWS[zoom]} 分钟`}</span>
          <button type="button" onClick={() => setZoom((value) => Math.min(3, value + 1))} disabled={zoom === 3}>＋</button>
        </div>
      </div>
      <canvas
        ref={canvasRef}
        className="anomaly-canvas"
        onPointerDown={(event) => {
          if (zoom === 0) return;
          dragRef.current = { active: true, startX: event.clientX, startEnd: visible.end, moved: false };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (dragRef.current.active && zoom > 0) {
            const delta = event.clientX - dragRef.current.startX;
            if (Math.abs(delta) > 4) dragRef.current.moved = true;
            const next = dragRef.current.startEnd - Math.round(delta / 9);
            setEndIndex(Math.min(points.length - 1, Math.max(WINDOWS[zoom] - 1, next)));
            if (canvasRef.current) canvasRef.current.style.cursor = "grabbing";
            return;
          }
          const position = pointerPosition(event);
          const plotWidth = Math.max((canvasRef.current?.getBoundingClientRect().width || 0) - 88, 1);
          const relative = Math.min(1, Math.max(0, (position.x - 66) / plotWidth));
          setHoverIndex(Math.round(relative * Math.max(visible.points.length - 1, 0)));
          const overLabel = hitsRef.current.some((hit) => position.x >= hit.left && position.x <= hit.right && position.y >= hit.top && position.y <= hit.bottom);
          if (canvasRef.current) canvasRef.current.style.cursor = overLabel ? "pointer" : "crosshair";
        }}
        onPointerUp={(event) => {
          dragRef.current.active = false;
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => { dragRef.current.active = false; }}
        onPointerLeave={() => { if (!dragRef.current.active) setHoverIndex(null); }}
        onWheel={(event) => {
          if (zoom === 0) return;
          event.preventDefault();
          const direction = Math.sign(event.deltaX || event.deltaY);
          setEndIndex((current) => {
            const end = Math.min(current, points.length - 1);
            return Math.min(points.length - 1, Math.max(WINDOWS[zoom] - 1, end + direction * 5));
          });
        }}
        onClick={(event) => {
          if (dragRef.current.moved) {
            dragRef.current.moved = false;
            return;
          }
          const position = pointerPosition(event);
          const hit = hitsRef.current.find((item) => position.x >= item.left && position.x <= item.right && position.y >= item.top && position.y <= item.bottom);
          if (hit) router.push(stockHref(hit.event));
        }}
      />
      {zoom > 0 && points.length > WINDOWS[zoom] && (
        <div className="chart-range">
          <button type="button" onClick={() => setEndIndex(Math.max(WINDOWS[zoom] - 1, visible.end - 10))} disabled={visible.start === 0}>← 早盘</button>
          <input
            aria-label="平移时间窗口"
            type="range"
            min={WINDOWS[zoom] - 1}
            max={points.length - 1}
            value={visible.end}
            onChange={(event) => setEndIndex(Number(event.target.value))}
          />
          <strong>{visible.points[0]?.time}—{visible.points.at(-1)?.time}</strong>
          <button type="button" onClick={() => setEndIndex(Math.min(points.length - 1, visible.end + 10))} disabled={visible.end >= points.length - 1}>午后 →</button>
        </div>
      )}
    </div>
  );
}
