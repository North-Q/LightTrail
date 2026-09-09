/** D1 灵感页（E7-8）：一句话→SSE→方案卡 A/B/C；参考图反推走 /api/photos/review 流式上传。 */

import { useCallback, useRef, useState } from "react";
import { postFormSSE, sendChat, sendDecide } from "../api/client";
import type { DecisionCard, SSEEvent, TraceItem } from "../api/events";
import { useSources } from "../context/SourceContext";
import { DecisionCardView } from "../components/DecisionCard";
import { TracePanel } from "../components/TracePanel";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SESSION_KEY = "lt.session.id";
const SESSION_LIST_KEY = "lt.sessions";
const EXAMPLE_PROMPTS = ["这周末想去拍银河", "今晚火烧云值得冲吗", "14mm f/2.8 拍银河，快门上限多少"];
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png"]);

function loadMetas(): { id: string; title: string; created: string }[] {
  try {
    return JSON.parse(localStorage.getItem(SESSION_LIST_KEY) ?? "[]") as { id: string; title: string; created: string }[];
  } catch {
    return [];
  }
}

function rememberSession(meta: { id: string; title: string; created: string }): void {
  const metas = loadMetas().filter((item) => item.id !== meta.id);
  metas.unshift(meta);
  localStorage.setItem(SESSION_LIST_KEY, JSON.stringify(metas.slice(0, 50)));
}

export function D1Page() {
  const [mode, setMode] = useState<"chat" | "decide">("chat");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [queuePos, setQueuePos] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const sessionRef = useRef<string>(localStorage.getItem(SESSION_KEY) ?? "");
  const abortRef = useRef<AbortController | null>(null);
  const userMessageRef = useRef("");
  const { addSource } = useSources();

  const appendTrace = useCallback((item: TraceItem) => {
    setTrace((prev) => [...prev.slice(-199), item]);
  }, []);

  const handleEvent = useCallback(
    (event: SSEEvent) => {
      switch (event.type) {
        case "queued":
          setQueuePos(event.position);
          break;
        case "step":
          appendTrace({ kind: "step", name: event.name, detail: event.output_summary || event.input_summary || "" });
          break;
        case "tool_call":
          appendTrace({ kind: "tool_call", name: event.name, detail: event.arguments ?? "" });
          break;
        case "tool_result":
          appendTrace({
            kind: "tool_result",
            name: event.name,
            detail: event.result ?? "",
            tag: event.data_source ? `来源 ${event.data_source}` : undefined,
          });
          if (event.data_source) {
            addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
          }
          break;
        case "token":
          setStreaming((prev) => prev + event.content);
          break;
        case "card":
          setCard(event.card);
          break;
        case "error":
          setError(event.message);
          break;
        case "done":
          setQueuePos(null);
          if (event.session_id) {
            sessionRef.current = event.session_id;
            localStorage.setItem(SESSION_KEY, event.session_id);
            rememberSession({
              id: event.session_id,
              title: userMessageRef.current || "新会话",
              created: new Date().toISOString(),
            });
          }
          const content = (streamingRef.current || event.text || "").trim();
          if (content) {
            setMessages((prev) => [...prev, { role: "assistant", content }]);
          }
          setStreaming("");
          break;
        default:
          break;
      }
    },
    [appendTrace, addSource],
  );

  const streamingRef = useRef("");
  streamingRef.current = streaming;

  async function submit(): Promise<void> {
    const text = input.trim();
    if (!text || running) {
      return;
    }
    setInput("");
    setError("");
    setCard(null);
    setTrace([]);
    setQueuePos(null);
    setStreaming("");
    userMessageRef.current = text;
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setRunning(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (mode === "chat") {
        await sendChat(text, sessionRef.current, handleEvent, controller.signal);
      } else {
        await sendDecide(text, sessionRef.current, handleEvent, controller.signal);
      }
    } catch (caught) {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    } finally {
      setRunning(false);
      setQueuePos(null);
      abortRef.current = null;
    }
  }

  async function handleUpload(file: File | undefined): Promise<void> {
    if (!file) {
      return;
    }
    setError("");
    setUploadError("");
    if (!ALLOWED_TYPES.has(file.type)) {
      setUploadError("仅支持 jpg/png 照片");
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setUploadError("照片超过 10MB 上限");
      return;
    }
    setCard(null);
    setTrace([]);
    setUploading(true);
    userMessageRef.current = `参考图反推：${file.name}`;
    const form = new FormData();
    form.append("file", file);
    form.append("focus", "");
    form.append("plan_reference", "");
    try {
      await postFormSSE("/api/photos/review", form, handleEvent);
    } catch (caught) {
      setUploadError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setUploading(false);
    }
  }
  const [uploadError, setUploadError] = useState("");

  function stop(): void {
    abortRef.current?.abort();
  }

  function clearConversation(): void {
    sessionRef.current = "";
    localStorage.removeItem(SESSION_KEY);
    setMessages([]);
    setStreaming("");
    setTrace([]);
    setCard(null);
    setError("");
  }

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">D1 · 灵感</h1>
          <p className="page-sub">模糊一句话，或丢一张参考大片 → 光迹给方案（可追问，依据全程可溯源）。</p>
        </div>
      </header>

      <div className="chat-layout">
        <section className="chat-main">
          <div className="card">
            <span className="card-kicker">功能选择</span>
            <div className="segmented" role="tablist" aria-label="输入模式">
              <button
                type="button"
                className={mode === "chat" ? "active" : ""}
                onClick={() => setMode("chat")}
                role="tab"
                aria-selected={mode === "chat"}
              >
                对话
              </button>
              <button
                type="button"
                className={mode === "decide" ? "active" : ""}
                onClick={() => setMode("decide")}
                role="tab"
                aria-selected={mode === "decide"}
              >
                一句话决策
              </button>
            </div>

            <div className="chip-row" style={{ marginTop: 12 }}>
              {EXAMPLE_PROMPTS.map((prompt) => (
                <button type="button" className="chip" key={prompt} onClick={() => setInput(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>

            <form
              className="prompt-row"
              style={{ marginTop: 12 }}
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={mode === "chat" ? "问光迹：现在几点？或 这周末去拍银河…" : "一句话决策：今晚火烧云值得冲吗？"}
                disabled={running}
                aria-label="输入内容"
              />
              <button type="submit" className="btn btn-primary" disabled={running || !input.trim()}>
                {mode === "chat" ? "发送" : "决策"}
              </button>
              {running ? (
                <button type="button" className="btn btn-ghost" onClick={stop}>停止</button>
              ) : null}
              {messages.length > 0 ? (
                <button type="button" className="btn btn-ghost" onClick={clearConversation}>清空</button>
              ) : null}
            </form>
            {queuePos !== null ? <span className="queue-badge">排队第 {queuePos} 位</span> : null}
          </div>

          <label className="upload-zone" htmlFor="ref-upload">
            <input
              id="ref-upload"
              type="file"
              accept="image/jpeg,image/png"
              disabled={uploading}
              onChange={(event) => void handleUpload(event.target.files?.[0])}
            />
            <p style={{ margin: 0 }}>📷 参考图反推（D1.2）——点击选择参考大片，光迹反推复刻计划</p>
            <p className="page-sub" style={{ margin: "8px 0 0" }}>
              {uploading ? "分析中（/api/photos/review · EXIF + 多模态）…" : "jpg/png · 单张 ≤10MB；上传即跑照片反推管线"}
            </p>
          </label>
          {uploadError ? <p className="message-error">{uploadError}</p> : null}

          <div className="message-list">
            {messages.map((message, index) => (
              <div className={`message message-${message.role}`} key={index}>
                <div className="bubble">{message.content}</div>
              </div>
            ))}
            {streaming ? (
              <div className="message message-assistant">
                <div className="bubble">{streaming}</div>
              </div>
            ) : null}
            {error ? <div className="message message-error">{error}</div> : null}
          </div>

          {card ? (
            <div className="plan-cards">
              <DecisionCardView card={card} />
              {card.alternatives && card.alternatives.length > 0 ? (
                <div className="plan-alts">
                  {card.alternatives.map((alternative, index) => (
                    <div className="card" key={`${alternative}-${index}`}>
                      <span className="card-kicker">方案 {["B", "C", "D"][index] ?? `#${index + 2}`} · 备选</span>
                      <p style={{ margin: 0 }}>{alternative}</p>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <TracePanel items={trace} />
      </div>
    </div>
  );
}
