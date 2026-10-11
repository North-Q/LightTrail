/** D4 复盘页：批量上传 → /api/photos/review → 复盘卡 → 4 维分析 + 可执行处方（无数据时展示空态）。 */

import { useEffect, useRef, useState } from "react";
import { postFormSSE } from "../api/client";
import { DataNotice } from "../components/DataNotice";
import { STOP_WAITING_NOTICE } from "../api/requestScope";
import { useRequestScope } from "../api/useRequestScope";
import type { DecisionCard, SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";
import { canWriteReviewEvent, type ReviewOutcome } from "./reviewEvent";

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png"]);
const DIMENSION_LABELS = ["曝光", "构图", "色彩", "时间"];

interface PhotoPreview {
  name: string;
  url: string;
}

export function D4Page() {
  const [running, setRunning] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [notice, setNotice] = useState("");
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [uploaded, setUploaded] = useState<string[]>([]);
  const [location, setLocation] = useState("");
  const [subjectType, setSubjectType] = useState("");
  const [outcome, setOutcome] = useState<ReviewOutcome>("");
  const [savingEvent, setSavingEvent] = useState(false);
  const [eventSaved, setEventSaved] = useState(false);
  const [eventMessage, setEventMessage] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [preview, setPreview] = useState<PhotoPreview | null>(null);
  const waiting = useRequestScope();
  const eventWaiting = useRequestScope();
  const previewUrlRef = useRef<string | null>(null);
  const { addSource } = useSources();

  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  useEffect(() => {
    if (!running) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  function showPreview(file: File | null): void {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    const url = file ? URL.createObjectURL(file) : null;
    previewUrlRef.current = url;
    setPreview(url && file ? { name: file.name, url } : null);
  }

  async function uploadOne(file: File, controller: AbortController): Promise<DecisionCard> {
    let reviewCard: DecisionCard | null = null;
    const form = new FormData();
    form.append("file", file);
    form.append("focus", "");
    form.append("plan_reference", "");
    await postFormSSE("/api/photos/review", form, (event: SSEEvent) => {
      if (!waiting.isCurrent(controller)) return;
      if (event.type === "queued") {
        setNotice(`${file.name}：已提交，等待分析…`);
      } else if (event.type === "step") {
        setNotice(`${file.name}：${event.name}${event.output_summary ? ` · ${event.output_summary}` : ""}`);
      } else if (event.type === "tool_call") {
        setNotice(`${file.name}：正在运行 ${event.name}…`);
      } else if (event.type === "card") {
        reviewCard = event.card;
      } else if (event.type === "tool_result") {
        if (event.data_source) {
          addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
        }
      } else if (event.type === "error") {
        throw new Error(event.message);
      }
    }, controller.signal);
    controller.signal.throwIfAborted();
    if (reviewCard === null) {
      throw new Error("复盘未返回决策卡");
    }
    return reviewCard;
  }

  async function handleFiles(files: FileList | null): Promise<void> {
    if (!files || files.length === 0 || running) {
      return;
    }
    setUploadError("");
    setCard(null);
    showPreview(null);
    setUploaded([]);
    setNotice("");
    setEventMessage("");
    eventWaiting.stop();
    setSavingEvent(false);
    setOutcome("");
    setEventSaved(false);
    setElapsedSeconds(0);
    const list = Array.from(files);
    let analyzedCount = 0;
    let lastSuccessful: { file: File; card: DecisionCard } | null = null;
    const controller = waiting.begin();
    setRunning(true);
    try {
      for (const file of list) {
        if (!waiting.isCurrent(controller)) break;
        if (!ALLOWED_TYPES.has(file.type)) {
          setUploadError(`${file.name}：仅支持 jpg/png，已跳过`);
          continue;
        }
        if (file.size > MAX_PHOTO_BYTES) {
          setUploadError(`${file.name}：超过 10MB，已跳过`);
          continue;
        }
        showPreview(file);
        setCard(null);
        setNotice(`正在分析 ${file.name}…`);
        try {
          const reviewCard = await uploadOne(file, controller);
          if (!waiting.isCurrent(controller)) break;
          setCard(reviewCard);
          lastSuccessful = { file, card: reviewCard };
          analyzedCount += 1;
          setUploaded((prev) => [...prev, file.name]);
        } catch (caught) {
          if (!waiting.isCurrent(controller)) break;
          setUploadError(`${file.name}：${caught instanceof Error ? caught.message : String(caught)}`);
        }
      }
      if (waiting.isCurrent(controller)) {
        if (lastSuccessful) {
          showPreview(lastSuccessful.file);
          setCard(lastSuccessful.card);
        }
        setNotice(analyzedCount > 0 ? `已分析 ${analyzedCount} 张，下方展示最近一张的结果` : "未取得可展示的复盘结果。");
      }
    } finally {
      waiting.finish(controller, () => setRunning(false));
    }
  }

  async function saveEvent(): Promise<void> {
    if (!card || !canWriteReviewEvent(true, location, outcome, savingEvent, eventSaved)) {
      return;
    }
    setSavingEvent(true);
    setEventMessage("");
    const controller = eventWaiting.begin();
    try {
      const response = await fetch("/api/events", {
        signal: controller.signal,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          location: location.trim(),
          subject_type: subjectType.trim(),
          summary: card.conclusion,
          outcome,
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      if (!eventWaiting.isCurrent(controller)) return;
      setEventSaved(true);
      setEventMessage("已确认写入事件记忆 ✓");
    } catch (caught) {
      if (eventWaiting.isCurrent(controller)) setEventMessage(`写入失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      eventWaiting.finish(controller, () => setSavingEvent(false));
    }
  }

  const evidence = card?.evidence ?? [];
  const params = card?.params ?? [];

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">复盘</h1>
          <p className="page-sub">上传实拍照片 → 结合 EXIF 与画面给出分析与下一步处方。</p>
        </div>
      </header>

      <DataNotice kind="photo" />
      <label className="upload-zone" htmlFor="d4-uploads">
        <input
          id="d4-uploads"
          type="file"
          accept="image/jpeg,image/png"
          multiple
          disabled={running}
          onChange={(event) => void handleFiles(event.target.files)}
        />
        <p style={{ margin: 0 }}>＋ 选择要复盘的照片（jpg/png，单张 10MB 内，可多选）</p>
        <p className="page-sub" style={{ margin: "8px 0 0" }}>
          {running ? `分析中（已等待 ${elapsedSeconds} 秒；画面分析可能调用模型）…` : "系统逐张分析，完成后给出四个维度评价与处方"}
        </p>
      </label>
      {running ? (
        <button type="button" className="btn btn-ghost" onClick={() => {
          waiting.stop(); setRunning(false); setNotice(STOP_WAITING_NOTICE);
        }}>
          停止等待
        </button>
      ) : null}
      {uploadError ? <p className="message-error">{uploadError}</p> : null}
      {notice ? <p className="page-sub">{notice}</p> : null}
      {preview ? (
        <section className="card d4-preview" aria-label="本次复盘照片预览">
          <div>
            <span className="card-kicker">本次复盘照片</span>
            <p className="page-sub">{preview.name} · 仅在当前页面预览，未另存照片</p>
          </div>
          <img className="d4-preview-image" src={preview.url} alt={`本次复盘照片：${preview.name}`} />
        </section>
      ) : null}
      {uploaded.length > 0 ? (
        <p className="page-sub" style={{ marginTop: 8 }}>已分析：{uploaded.join("、")}</p>
      ) : null}

      {!card ? (
        <section className="card" style={{ marginTop: 20 }}>
          <span className="card-kicker">分析结果</span>
          <p className="trace-empty">上传照片后，这里会展示曝光 / 构图 / 色彩 / 时间四个维度的评价与可执行处方。</p>
        </section>
      ) : (
        <>
          <section className="analysis-grid" style={{ marginTop: 20 }}>
            {DIMENSION_LABELS.map((label) => {
              // 未命中该维度的依据就如实留空（不再拿首条依据兜底——那会把「曝光」文案显示到「色彩/时间」下，属误导）
              const source = evidence.find((item) => item.field?.includes(label)) ?? null;
              return (
                <div className="analysis-card" key={label}>
                  <span className="card-kicker">{label}</span>
                  <p style={{ margin: "4px 0" }}><strong>{source?.field || label}</strong></p>
                  <p className="page-sub" style={{ margin: 0 }}>{source?.note || "—"}</p>
                  {source ? (
                    <p className="page-sub" style={{ margin: "8px 0 0", fontSize: 12 }}>
                      依据：<code className="source-tool">{source.tool || "—"}</code> · 置信度 {source.confidence || "—"}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </section>

          <section className="card" style={{ marginTop: 20 }}>
            <span className="card-kicker">可执行处方</span>
            {params.length === 0 ? (
              <p className="trace-empty">本次复盘未生成参数处方。</p>
            ) : (
              <div className="prescription-list">
                {params.map((param, index) => (
                  <div className="prescription" key={`${param.name}-${index}`}>
                    <span className={`level level-${index === 0 ? "high" : index === 1 ? "mid" : "low"}`}>
                      {index === 0 ? "高" : index === 1 ? "中" : "低"}
                    </span>
                    <span>
                      <strong>{param.name} = {param.value}</strong> — {param.reason}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="card" style={{ marginTop: 20 }}>
            <span className="card-kicker">复盘结论</span>
            <p className="card-conclusion">{card.conclusion}</p>
          </section>

          <section className="card" style={{ marginTop: 20 }}>
            <span className="card-kicker">记录本次拍摄</span>
            <p className="page-sub">事件记忆仅在确认后写入本地；分析结果与 trace 已保留于本地会话，后续相关请求可能将事件摘要发送给模型服务。</p>
            <div className="gear-field">
              <label htmlFor="event-location">地点</label>
              <input id="event-location" value={location} onChange={(event) => setLocation(event.target.value)} placeholder="如 临港海堤" />
            </div>
            <div className="gear-field">
              <label htmlFor="event-subject">题材</label>
              <input id="event-subject" value={subjectType} onChange={(event) => setSubjectType(event.target.value)} placeholder="如 火烧云" />
            </div>
            <div className="gear-field">
              <label htmlFor="event-outcome">实际结果</label>
              <select id="event-outcome" value={outcome} onChange={(event) => setOutcome(event.target.value as ReviewOutcome)}>
                <option value="">请选择是否出片</option>
                <option value="success">出片</option>
                <option value="fail">未出片</option>
              </select>
            </div>
            <button type="button" className="btn btn-primary" disabled={!canWriteReviewEvent(Boolean(card), location, outcome, savingEvent, eventSaved)} onClick={() => void saveEvent()}>
              {savingEvent ? "写入中…" : eventSaved ? "已写入" : "确认写入事件记忆"}
            </button>
            {eventMessage ? <p className="page-sub">{eventMessage}</p> : null}
          </section>
        </>
      )}
    </div>
  );
}
