"use client";

import { useState } from "react";
import type { ReplayData } from "../lib/types";
import { API_BASE } from "../lib/types";
import { drawFundFlowScene, getReplayScaleMax } from "./FlowBubbleCanvas";

export default function VideoGenerator({ replay }: { replay: ReplayData }) {
  const [state, setState] = useState<"idle" | "recording" | "converting" | "done" | "error">("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("生成一条干净的竖屏收盘资金流向视频");

  const generate = async () => {
    if (!replay.frames.length || state === "recording" || state === "converting") return;
    try {
      if (replay.verifiedThrough !== "15:00") {
        throw new Error(`当前真实回放只核验至 ${replay.verifiedThrough || "--:--"}，收盘视频需等待 15:00 数据`);
      }
      const coveragePercent = replay.coveragePercent ?? 0;
      if (coveragePercent < 100) {
        throw new Error(`今日分钟回放覆盖率仅 ${coveragePercent}%，请先点击“更新收盘数据”补齐真实分时后再生成视频`);
      }
      setState("converting");
      setProgress(0);
      setMessage("正在检查本地视频服务和 FFmpeg…");
      let capability: { ok?: boolean; message?: string };
      try {
        const preflight = await fetch(`${API_BASE}/api/video/capability`, { cache: "no-store" });
        if (!preflight.ok) throw new Error(`本地视频服务返回 ${preflight.status}`);
        capability = await preflight.json();
      } catch {
        throw new Error("本地视频服务未运行。请关闭残留网页，再用 start.command 或 start-windows.bat 重新打开应用");
      }
      if (!capability.ok) throw new Error(capability.message || "FFmpeg 当前不可用");
      const canvas = document.createElement("canvas");
      canvas.width = 720;
      canvas.height = 1280;
      const context = canvas.getContext("2d");
      if (!context || typeof canvas.captureStream !== "function" || typeof MediaRecorder === "undefined") {
        throw new Error("当前浏览器不支持视频录制，请使用 Chrome、Edge 或 Safari 最新版");
      }
      const preferred = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
      const mimeType = preferred.find((value) => MediaRecorder.isTypeSupported(value));
      if (!mimeType) throw new Error("当前浏览器没有可用的视频编码器");
      const stream = canvas.captureStream(30);
      const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 5_500_000 });
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      const stopped = new Promise<Blob>((resolve, reject) => {
        recorder.onerror = () => reject(new Error("浏览器录制失败"));
        recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType }));
      });
      const durationSeconds = 28;
      const endingHoldSeconds = 3;
      const motionSeconds = durationSeconds - endingHoldSeconds;
      const scaleMax = getReplayScaleMax(replay.frames);
      setState("recording");
      setProgress(0);
      setMessage("正在绘制 28 秒回放，请保持此页面打开…");
      recorder.start(1000);
      const startedAt = performance.now();
      await new Promise<void>((resolve) => {
        const render = (timestamp: number) => {
          // Some Windows/Edge builds use a slightly earlier origin for the
          // first animation-frame timestamp. Never let that create frame -1.
          const elapsed = Math.max(0, (timestamp - startedAt) / 1000);
          const ratio = Math.min(1, elapsed / durationSeconds);
          const motionRatio = Math.min(1, elapsed / motionSeconds);
          const framePosition = motionRatio * Math.max(replay.frames.length - 1, 0);
          const frameIndex = Math.max(0, Math.min(replay.frames.length - 1, Math.floor(framePosition)));
          drawFundFlowScene(context, {
            width: canvas.width,
            height: canvas.height,
            date: replay.date,
            frame: replay.frames[frameIndex],
            nextFrame: replay.frames[Math.min(frameIndex + 1, replay.frames.length - 1)],
            indexes: replay.indexes,
            progress: framePosition - frameIndex,
            scaleMax,
          });
          setProgress(Math.round(ratio * 75));
          if (ratio < 1) requestAnimationFrame(render);
          else resolve();
        };
        requestAnimationFrame(render);
      });
      recorder.stop();
      stream.getTracks().forEach((track) => track.stop());
      const webm = await stopped;
      if (webm.size < 1024) throw new Error("浏览器没有录到有效画面，请保持页面在前台后重试");
      setState("converting");
      setMessage("正在转换为 MP4 并保存到本地…");
      setProgress(82);
      const response = await fetch(`${API_BASE}/api/video/convert?date=${encodeURIComponent(replay.date)}`, {
        method: "POST",
        headers: { "Content-Type": "video/webm" },
        body: webm,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ error: "MP4 转换失败" }));
        throw new Error(payload.error || "MP4 转换失败");
      }
      const mp4 = await response.blob();
      if (mp4.size < 1024) throw new Error("本地转换服务返回了空视频");
      setProgress(100);
      const url = URL.createObjectURL(mp4);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${replay.date}_收盘资金流向.mp4`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setState("done");
      setMessage(`MP4 已下载；结尾停留在 ${replay.verifiedThrough}，并保存在项目 output 文件夹中`);
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "视频生成失败");
    }
  };

  const busy = state === "recording" || state === "converting";
  return (
    <div className="video-generator">
      <div>
        <span className="eyebrow">ON-DEMAND VIDEO</span>
        <h3>生成今日资金流向视频</h3>
        <p>{message}</p>
      </div>
      <div className="video-actions">
        {busy && (
          <div className="video-progress" aria-label={`视频生成进度 ${progress}%`}>
            <span style={{ width: `${progress}%` }} />
          </div>
        )}
        <button type="button" className="primary-button" onClick={generate} disabled={busy}>
          {state === "recording" ? `正在生成 ${progress}%` : state === "converting" ? (progress < 80 ? "正在检查视频服务" : "正在转换 MP4") : "生成 28 秒 MP4"}
        </button>
      </div>
    </div>
  );
}
