"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BoardFlow, MarketIndex, ReplayFrame } from "../lib/types";
import { formatYi, shortDate } from "../lib/types";

type SceneOptions = {
  width: number;
  height: number;
  date: string;
  frame: ReplayFrame;
  nextFrame?: ReplayFrame;
  indexes: MarketIndex[];
  progress?: number;
  scaleMax?: number;
};

const FONT_STACK = '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';

function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function drawCrown(ctx: CanvasRenderingContext2D, x: number, y: number, scale: number) {
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = "#ffd86a";
  ctx.shadowColor = "rgba(255, 208, 88, .8)";
  ctx.shadowBlur = 14 * scale;
  ctx.beginPath();
  ctx.moveTo(-18 * scale, 10 * scale);
  ctx.lineTo(-14 * scale, -10 * scale);
  ctx.lineTo(-4 * scale, 1 * scale);
  ctx.lineTo(0, -15 * scale);
  ctx.lineTo(7 * scale, 0);
  ctx.lineTo(18 * scale, -9 * scale);
  ctx.lineTo(15 * scale, 10 * scale);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

type FlowNode = {
  item: BoardFlow;
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  radius: number;
};

function frameBoards(frame: ReplayFrame): BoardFlow[] {
  if (frame.boards?.length) return frame.boards;
  const byCode = new Map<string, BoardFlow>();
  [...frame.inflow, ...frame.outflow].forEach((item) => byCode.set(item.code, item));
  return [...byCode.values()];
}

export function getReplayScaleMax(frames: ReplayFrame[]): number {
  return Math.max(
    1,
    ...frames.flatMap((frame) => frameBoards(frame).map((item) => Math.abs(item.mainFlow))),
  );
}

function stableUnit(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function interpolateBoards(frame: ReplayFrame, nextFrame: ReplayFrame | undefined, progress: number): BoardFlow[] {
  const current = new Map(frameBoards(frame).map((item) => [item.code, item]));
  const next = new Map(frameBoards(nextFrame || frame).map((item) => [item.code, item]));
  const codes = new Set([...current.keys(), ...next.keys()]);
  return [...codes].map((code) => {
    const from = current.get(code);
    const to = next.get(code);
    const base = from || to!;
    const fromValue = from?.mainFlow ?? 0;
    const toValue = to?.mainFlow ?? 0;
    return { ...base, mainFlow: fromValue + (toValue - fromValue) * progress };
  });
}

function layoutFlowNodes(
  items: BoardFlow[],
  area: { x: number; y: number; width: number; height: number },
  dividerY: number,
  scaleMax: number,
): FlowNode[] {
  const unit = Math.min(area.width, area.height);
  const minRadius = unit * 0.026;
  const maxRadius = unit * 0.092;
  const verticalRange = Math.max(1, area.height * 0.47 - maxRadius * 1.2);
  const nodes = [...items]
    .sort((a, b) => a.code.localeCompare(b.code))
    .map((item) => {
      const normalized = Math.min(1, Math.abs(item.mainFlow) / Math.max(scaleMax, 1));
      const sizeStrength = Math.sqrt(normalized);
      const positionStrength = Math.pow(normalized, 0.42);
      const radius = minRadius + (maxRadius - minRadius) * sizeStrength;
      const signedStrength = item.mainFlow === 0 ? 0 : Math.sign(item.mainFlow) * positionStrength;
      const targetX = area.x + radius + stableUnit(item.code) * Math.max(1, area.width - radius * 2);
      const targetY = dividerY - signedStrength * verticalRange;
      return { item, x: targetX, y: targetY, targetX, targetY, radius };
    });

  const minX = area.x;
  const maxX = area.x + area.width;
  const minY = area.y;
  const maxY = area.y + area.height;
  for (let iteration = 0; iteration < 36; iteration += 1) {
    nodes.forEach((node) => {
      node.x += (node.targetX - node.x) * 0.13;
      node.y += (node.targetY - node.y) * 0.22;
    });
    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        const a = nodes[left];
        const b = nodes[right];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distance = Math.hypot(dx, dy);
        const minimum = a.radius + b.radius + unit * 0.009;
        if (distance >= minimum) continue;
        if (distance < 0.001) {
          const angle = stableUnit(`${a.item.code}:${b.item.code}`) * Math.PI * 2;
          dx = Math.cos(angle);
          dy = Math.sin(angle);
          distance = 1;
        }
        const overlap = minimum - distance;
        const totalMass = a.radius * a.radius + b.radius * b.radius;
        const aShare = (b.radius * b.radius) / totalMass;
        const bShare = (a.radius * a.radius) / totalMass;
        const nx = dx / distance;
        const ny = dy / distance;
        a.x -= nx * overlap * aShare;
        a.y -= ny * overlap * aShare;
        b.x += nx * overlap * bShare;
        b.y += ny * overlap * bShare;
      }
    }
    nodes.forEach((node) => {
      node.x = Math.max(minX + node.radius, Math.min(maxX - node.radius, node.x));
      node.y = Math.max(minY + node.radius, Math.min(maxY - node.radius, node.y));
    });
  }
  // Finish with collision-only passes so the attraction step cannot leave a
  // small residual overlap in the final rendered frame.
  for (let iteration = 0; iteration < 18; iteration += 1) {
    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        const a = nodes[left];
        const b = nodes[right];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distance = Math.hypot(dx, dy);
        const minimum = a.radius + b.radius + unit * 0.009;
        if (distance >= minimum) continue;
        if (distance < 0.001) {
          const angle = stableUnit(`${a.item.code}:${b.item.code}`) * Math.PI * 2;
          dx = Math.cos(angle);
          dy = Math.sin(angle);
          distance = 1;
        }
        const overlap = (minimum - distance) * 0.52;
        const nx = dx / distance;
        const ny = dy / distance;
        a.x -= nx * overlap;
        a.y -= ny * overlap;
        b.x += nx * overlap;
        b.y += ny * overlap;
      }
    }
    nodes.forEach((node) => {
      node.x = Math.max(minX + node.radius, Math.min(maxX - node.radius, node.x));
      node.y = Math.max(minY + node.radius, Math.min(maxY - node.radius, node.y));
    });
  }
  return nodes;
}

function drawFlowBubbles(ctx: CanvasRenderingContext2D, nodes: FlowNode[]) {
  const strongestPositive = [...nodes]
    .filter((node) => node.item.mainFlow > 0)
    .sort((a, b) => b.item.mainFlow - a.item.mainFlow)[0];
  [...nodes].sort((a, b) => a.radius - b.radius).forEach((node) => {
    const { item, x, y, radius } = node;
    const base = item.mainFlow >= 0 ? [255, 57, 61] : [35, 214, 106];
    const glow = ctx.createRadialGradient(x - radius * 0.25, y - radius * 0.28, radius * 0.05, x, y, radius * 1.25);
    glow.addColorStop(0, `rgba(${Math.min(base[0] + 30, 255)}, ${Math.min(base[1] + 30, 255)}, ${Math.min(base[2] + 30, 255)}, .98)`);
    glow.addColorStop(0.72, `rgba(${base[0]}, ${base[1]}, ${base[2]}, .96)`);
    glow.addColorStop(1, `rgba(${base[0]}, ${base[1]}, ${base[2]}, .14)`);
    ctx.save();
    ctx.shadowColor = `rgba(${base[0]}, ${base[1]}, ${base[2]}, .68)`;
    ctx.shadowBlur = radius * 0.36;
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,.16)";
    ctx.lineWidth = Math.max(1, radius * 0.015);
    ctx.stroke();
    ctx.restore();

    const titleSize = Math.max(8, Math.min(radius * 0.34, 25));
    const valueSize = Math.max(7, Math.min(radius * 0.27, 21));
    const maxNameLength = radius < 30 ? 4 : radius < 45 ? 6 : 9;
    const visibleName = item.name.length > maxNameLength ? `${item.name.slice(0, maxNameLength)}…` : item.name;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "rgba(255,255,255,.98)";
    ctx.font = `700 ${titleSize}px ${FONT_STACK}`;
    ctx.fillText(visibleName, x, y - valueSize * 0.5, radius * 1.72);
    ctx.font = `650 ${valueSize}px ${FONT_STACK}`;
    ctx.fillText(formatYi(item.mainFlow), x, y + valueSize * 0.66, radius * 1.82);

    if (strongestPositive?.item.code === item.code) {
      drawCrown(ctx, x, y - radius - 3, Math.max(0.58, radius / 78));
    }
  });
}

function drawIndexStrip(
  ctx: CanvasRenderingContext2D,
  indexes: MarketIndex[],
  x: number,
  y: number,
  width: number,
  fontSize: number,
) {
  const gap = width / Math.max(indexes.length, 1);
  indexes.forEach((item, index) => {
    const color = item.changePct >= 0 ? "#ff5f64" : "#36d77a";
    ctx.textAlign = "right";
    ctx.fillStyle = "rgba(224,228,235,.62)";
    ctx.font = `500 ${fontSize * 0.72}px ${FONT_STACK}`;
    ctx.fillText(item.name, x + gap * (index + 1), y);
    ctx.fillStyle = color;
    ctx.font = `650 ${fontSize}px ${FONT_STACK}`;
    const sign = item.changePct >= 0 ? "+" : "";
    ctx.fillText(`${item.price.toFixed(2)}  ${sign}${item.changePct.toFixed(2)}%`, x + gap * (index + 1), y + fontSize * 1.2);
  });
}

export function drawFundFlowScene(ctx: CanvasRenderingContext2D, options: SceneOptions) {
  const { width, height, date, frame, nextFrame, indexes } = options;
  const progress = Math.max(0, Math.min(1, options.progress ?? 0));
  ctx.clearRect(0, 0, width, height);
  const background = ctx.createLinearGradient(0, 0, width, height);
  background.addColorStop(0, "#090b0f");
  background.addColorStop(0.52, "#050609");
  background.addColorStop(1, "#080b0d");
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);

  const redHaze = ctx.createRadialGradient(width * 0.5, height * 0.34, 0, width * 0.5, height * 0.34, width * 0.58);
  redHaze.addColorStop(0, "rgba(109,18,24,.26)");
  redHaze.addColorStop(1, "rgba(109,18,24,0)");
  ctx.fillStyle = redHaze;
  ctx.fillRect(0, 0, width, height * 0.62);
  const greenHaze = ctx.createRadialGradient(width * 0.5, height * 0.76, 0, width * 0.5, height * 0.76, width * 0.6);
  greenHaze.addColorStop(0, "rgba(12,83,45,.23)");
  greenHaze.addColorStop(1, "rgba(12,83,45,0)");
  ctx.fillStyle = greenHaze;
  ctx.fillRect(0, height * 0.45, width, height * 0.55);

  const pad = width * 0.048;
  const portrait = height / width > 1.25;
  const titleSize = portrait ? width * 0.055 : height * 0.055;
  ctx.fillStyle = "#f5f6f8";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.font = `800 ${titleSize}px ${FONT_STACK}`;
  ctx.fillText(shortDate(date), pad, pad, width * 0.35);
  ctx.fillText("收盘资金流向", pad, pad + titleSize * 1.02, width * 0.38);

  const legendY = pad + titleSize * 0.25;
  const legendX = width * 0.43;
  const dotRadius = Math.max(4, width * 0.006);
  ctx.beginPath();
  ctx.fillStyle = "#ff3b40";
  ctx.arc(legendX, legendY + dotRadius, dotRadius, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#d8dde4";
  ctx.font = `600 ${titleSize * 0.4}px ${FONT_STACK}`;
  ctx.fillText("资金净流入", legendX + dotRadius * 2.1, legendY - dotRadius * 0.3);
  const greenX = legendX + titleSize * 2.45;
  ctx.beginPath();
  ctx.fillStyle = "#27d66f";
  ctx.arc(greenX, legendY + dotRadius, dotRadius, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#d8dde4";
  ctx.fillText("资金净流出", greenX + dotRadius * 2.1, legendY - dotRadius * 0.3);

  ctx.textAlign = "right";
  ctx.fillStyle = "#f7f8fa";
  ctx.font = `800 ${titleSize * 0.9}px ${FONT_STACK}`;
  ctx.fillText(frame.time, width - pad, pad * 0.82);
  drawIndexStrip(ctx, indexes, width * 0.43, pad + titleSize * 1.42, width * 0.52, titleSize * 0.34);

  const contentTop = portrait ? height * 0.145 : height * 0.18;
  const contentBottom = height * 0.93;
  const dividerY = contentTop + (contentBottom - contentTop) * 0.49;
  const interpolated = interpolateBoards(frame, nextFrame, progress);
  const nodes = layoutFlowNodes(
    interpolated,
    { x: pad, y: contentTop, width: width - pad * 2, height: contentBottom - contentTop },
    dividerY,
    options.scaleMax || Math.max(1, ...interpolated.map((item) => Math.abs(item.mainFlow))),
  );
  drawFlowBubbles(ctx, nodes);

  const line = ctx.createLinearGradient(pad, 0, width - pad, 0);
  line.addColorStop(0, "rgba(255,255,255,0)");
  line.addColorStop(0.25, "rgba(255,255,255,.38)");
  line.addColorStop(0.75, "rgba(255,255,255,.38)");
  line.addColorStop(1, "rgba(255,255,255,0)");
  ctx.strokeStyle = line;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, dividerY);
  ctx.lineTo(width - pad, dividerY);
  ctx.stroke();
  const badgeWidth = width * 0.35;
  const badgeHeight = titleSize * 0.56;
  ctx.fillStyle = "#08090c";
  roundedRect(ctx, width / 2 - badgeWidth / 2, dividerY - badgeHeight / 2, badgeWidth, badgeHeight, badgeHeight / 2);
  ctx.fill();
  ctx.fillStyle = "rgba(235,238,243,.78)";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `600 ${titleSize * 0.31}px ${FONT_STACK}`;
  ctx.fillText("沪 · 深 · 京 A股板块资金", width / 2, dividerY);

  ctx.fillStyle = "rgba(205,211,220,.58)";
  ctx.font = `500 ${titleSize * 0.27}px ${FONT_STACK}`;
  ctx.textAlign = "center";
  ctx.fillText("以上内容仅供参考，不构成任何投资建议。市场有风险，投资需谨慎。", width / 2, height * 0.965, width * 0.9);
}

type Props = {
  date: string;
  frames: ReplayFrame[];
  indexes: MarketIndex[];
  duration?: number;
};

export default function FlowBubbleCanvas({ date, frames, indexes, duration = 28 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const startedAt = useRef<number>(0);
  const pauseOffset = useRef<number>(0);
  const renderPosition = useRef<number>(0);
  const [playing, setPlaying] = useState(true);
  const [frameIndex, setFrameIndex] = useState(0);
  const scaleMax = useMemo(() => getReplayScaleMax(frames), [frames]);

  const draw = useCallback((index: number, transition = 0) => {
    const canvas = canvasRef.current;
    const frame = frames[index];
    if (!canvas || !frame) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(640, Math.round(rect.width * dpr));
    const height = Math.max(650, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const context = canvas.getContext("2d");
    if (context) {
      drawFundFlowScene(context, {
        width,
        height,
        date,
        frame,
        nextFrame: frames[Math.min(index + 1, frames.length - 1)],
        indexes,
        progress: transition,
        scaleMax,
      });
    }
  }, [date, frames, indexes, scaleMax]);

  useEffect(() => {
    if (!frames.length || !playing) return;
    if (frames.length === 1) {
      renderPosition.current = 0;
      setFrameIndex(0);
      draw(0, 0);
      setPlaying(false);
      return;
    }
    let animationId = 0;
    const animate = (timestamp: number) => {
      if (!startedAt.current) startedAt.current = timestamp - pauseOffset.current;
      const elapsed = Math.max(0, (timestamp - startedAt.current) / 1000);
      const playbackProgress = Math.min(1, elapsed / duration);
      const lastPosition = frames.length - 1;
      const position = playbackProgress * lastPosition;
      const index = Math.min(lastPosition, Math.floor(position));
      const transition = playbackProgress >= 1 ? 0 : position - index;
      renderPosition.current = position;
      setFrameIndex((current) => (current === index ? current : index));
      draw(index, transition);
      if (playbackProgress >= 1) {
        renderPosition.current = lastPosition;
        pauseOffset.current = duration * 1000;
        startedAt.current = 0;
        setFrameIndex(lastPosition);
        setPlaying(false);
        return;
      }
      animationId = requestAnimationFrame(animate);
    };
    animationId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animationId);
  }, [draw, duration, frames.length, playing]);

  useEffect(() => {
    const lastPosition = Math.max(frames.length - 1, 0);
    const position = Math.min(renderPosition.current, lastPosition);
    const index = Math.min(lastPosition, Math.floor(position));
    renderPosition.current = position;
    setFrameIndex(index);
    draw(index, position - index);
    const onResize = () => {
      const position = renderPosition.current;
      const index = Math.min(frames.length - 1, Math.floor(position));
      draw(index, position - index);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [draw, frames.length]);

  const toggle = () => {
    if (playing) {
      pauseOffset.current = (renderPosition.current / Math.max(frames.length - 1, 1)) * duration * 1000;
      setPlaying(false);
    } else {
      const lastPosition = Math.max(frames.length - 1, 0);
      if (renderPosition.current >= lastPosition) {
        renderPosition.current = 0;
        pauseOffset.current = 0;
        setFrameIndex(0);
        draw(0, 0);
      }
      startedAt.current = performance.now() - pauseOffset.current;
      setPlaying(true);
    }
  };

  const seek = (value: number) => {
    const index = Math.max(0, Math.min(frames.length - 1, value));
    setFrameIndex(index);
    renderPosition.current = index;
    pauseOffset.current = (index / Math.max(frames.length - 1, 1)) * duration * 1000;
    startedAt.current = performance.now() - pauseOffset.current;
    draw(index, 0);
    if (index >= frames.length - 1) setPlaying(false);
  };

  return (
    <div className="bubble-player">
      <canvas ref={canvasRef} className="bubble-canvas" aria-label="A股板块资金流向气泡回放" />
      <div className="player-controls">
        <button
          type="button"
          className="icon-button"
          onClick={toggle}
          aria-label={playing ? "暂停" : frameIndex >= frames.length - 1 ? "从头播放一次" : "继续播放"}
        >
          {playing ? "Ⅱ" : "▶"}
        </button>
        <input
          aria-label="回放进度"
          type="range"
          min={0}
          max={Math.max(frames.length - 1, 0)}
          value={frameIndex}
          onChange={(event) => seek(Number(event.target.value))}
        />
        <span className="time-chip">{frames[frameIndex]?.time || "--:--"}</span>
        <span className="frame-chip">5分钟/帧</span>
      </div>
    </div>
  );
}
