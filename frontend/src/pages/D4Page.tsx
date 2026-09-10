/** D4 复盘页：批量上传 → /api/photos/review → 复盘卡 → 4 维分析 + 可执行处方（无数据时展示空态）。 */

import { useState } from "react";
import { postFormSSE } from "../api/client";
import type { DecisionCard, SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png"]);
const DIMENSION_LABELS = ["曝光", "构图", "色彩", "时间"];

export function D4Page() {
  const [running, setRunning] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [notice, setNotice] = useState("");
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [uploaded, setUploaded] = useState<string[]>([]);
  const { addSource } = useSources();

  async function uploadOne(file: File): Promise<void> {
    const form = new FormData();
    form.append("file", file);
    form.append("focus", "");
    form.append("plan_reference", "");
    await postFormSSE("/api/photos/review", form, (event: SSEEvent) => {
      if (event.type === "card") {
        setCard(event.card);
      } else if (event.type === "tool_result") {
        if (event.data_source) {
          addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
        }
      } else if (event.type === "error") {
        throw new Error(event.message);
      }
    });
  }

  async function handleFiles(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) {
      return;
    }
    setUploadError("");
    setCard(null);
    setUploaded([]);
    setNotice("");
    const list = Array.from(files);
    for (const file of list) {
      if (!ALLOWED_TYPES.has(file.type)) {
        setUploadError(`${file.name}：仅支持 jpg/png，已跳过`);
        continue;
      }
      if (file.size > MAX_PHOTO_BYTES) {
        setUploadError(`${file.name}：超过 10MB，已跳过`);
        continue;
      }
      setRunning(true);
      setNotice(`正在分析 ${file.name}…`);
      try {
        await uploadOne(file);
        setUploaded((prev) => [...prev, file.name]);
      } catch (caught) {
        setUploadError(`${file.name}：${caught instanceof Error ? caught.message : String(caught)}`);
      } finally {
        setRunning(false);
      }
    }
    setNotice(uploaded.length > 0 ? `已分析 ${uploaded.length} 张，下方展示最近一张的结果` : "");
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
          {running ? "分析中（读取 EXIF 与画面）…" : "系统逐张分析，完成后给出四个维度评价与处方"}
        </p>
      </label>
      {uploadError ? <p className="message-error">{uploadError}</p> : null}
      {notice ? <p className="page-sub">{notice}</p> : null}
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
              const source = evidence.find((item) => item.field?.includes(label)) ?? evidence[0];
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
        </>
      )}
    </div>
  );
}
