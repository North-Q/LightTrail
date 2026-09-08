/** 对话页（E7-5 核心页 1）：对话 + 一句话决策，SSE 流式渲染 token/工具事件，右侧轨迹面板实时滚动。 */

import { useCallback, useRef, useState } from "react";
import { sendChat, sendDecide } from "../api/client";
import type { DecisionCard, SSEEvent, SessionMeta, TraceItem } from "../api/events";
import { DecisionCardView } from "../components/DecisionCard";
import { TracePanel } from "../components/TracePanel";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SESSION_KEY = "lt.session.id";
const SESSION_LIST_KEY = "lt.sessions";

function loadSessionMetas(): SessionMeta[] {
  try {
    return JSON.parse(localStorage.getItem(SESSION_LIST_KEY) ?? "[]") as SessionMeta[];
  } catch {
    return [];
  }
}

function rememberSession(meta: SessionMeta): void {
  const metas = loadSessionMetas().filter((item) => item.id !== meta.id);
  metas.unshift(meta);
  localStorage.setItem(SESSION_LIST_KEY, JSON.stringify(metas.slice(0, 50)));
}

export function ChatPage() {
  const [mode, setMode] = useState<"chat" | "decide">("chat");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [queuePos, setQueuePos] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const sessionRef = useRef<string>(localStorage.getItem(SESSION_KEY) ?? "");
  const abortRef = useRef<AbortController | null>(null);
  const userMessageRef = useRef("");

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
          appendTrace({
            kind: "step",
            name: event.name,
            detail: event.output_summary || event.input_summary || "",
          });
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
    [appendTrace],
  );

  // streaming 最新值在 done 回调内使用：用 ref 兜底避免闭包过期
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

  const assistantText = streaming;

  return (
    <div className="chat-layout">
      <section className="chat-main">
        <header className="chat-toolbar">
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
          <div className="toolbar-right">
            {queuePos !== null ? <span className="queue-badge">排队第 {queuePos} 位</span> : null}
            {running ? <button type="button" onClick={stop}>停止</button> : null}
            {messages.length > 0 ? (
              <button type="button" onClick={clearConversation} className="ghost">
                清空
              </button>
            ) : null}
          </div>
        </header>

        <div className="message-list">
          {messages.map((message, index) => (
            <div className={`message message-${message.role}`} key={index}>
              <div className="bubble">{message.content}</div>
            </div>
          ))}
          {assistantText ? (
            <div className="message message-assistant">
              <div className="bubble">{assistantText}</div>
            </div>
          ) : null}
          {error ? <div className="message message-error">{error}</div> : null}
        </div>

        <form
          className="composer"
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
          <button type="submit" disabled={running || !input.trim()}>
            {mode === "chat" ? "发送" : "决策"}
          </button>
        </form>

        {card ? <DecisionCardView card={card} /> : null}
      </section>

      <TracePanel items={trace} />
    </div>
  );
}
