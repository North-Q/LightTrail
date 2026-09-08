/** 会话列表页（E7-5 核心页 2）：本地会话列表 + 详情回放（历史消息 + 决策卡）。 */

import { useState } from "react";
import { getSession } from "../api/client";
import type { DecisionCard, SessionMeta } from "../api/events";
import { DecisionCardView } from "../components/DecisionCard";

function loadSessionMetas(): SessionMeta[] {
  try {
    return JSON.parse(localStorage.getItem("lt.sessions") ?? "[]") as SessionMeta[];
  } catch {
    return [];
  }
}

interface SessionDetailView {
  history: { role: string; content: string }[];
  card: DecisionCard | null;
  sessionId: string;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

export function SessionsPage() {
  const [metas] = useState<SessionMeta[]>(() => loadSessionMetas());
  const [detail, setDetail] = useState<SessionDetailView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function openSession(sessionId: string): Promise<void> {
    setLoading(true);
    setError("");
    try {
      const body = await getSession(sessionId);
      const session = body.session;
      const workspace = (session as { workspace?: { last_card?: DecisionCard } }).workspace;
      setDetail({
        sessionId: session.session_id,
        history: session.history ?? [],
        card: workspace?.last_card ?? null,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sessions-layout">
      <aside className="session-list">
        <header className="session-list-head">会话列表</header>
        {metas.length === 0 ? (
          <p className="trace-empty">还没有会话——去「对话」页发一条消息试试。</p>
        ) : (
          <ul>
            {metas.map((meta) => (
              <li key={meta.id}>
                <button type="button" className="session-item" onClick={() => void openSession(meta.id)}>
                  <strong>{meta.title}</strong>
                  <span>{formatTime(meta.created)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {error ? <p className="message-error">{error}</p> : null}
      </aside>

      <section className="session-detail">
        {loading ? <p>载入中…</p> : null}
        {!loading && detail === null ? (
          <p className="trace-empty">选择一个会话查看历史与决策卡回放。</p>
        ) : null}
        {detail ? (
          <>
            <header className="chat-toolbar">
              <span className="page-title">会话 · {detail.sessionId.slice(0, 8)}</span>
            </header>
            <div className="message-list">
              {detail.history.map((message, index) => (
                <div className={`message message-${message.role}`} key={index}>
                  <div className="bubble">{message.content}</div>
                </div>
              ))}
            </div>
            {detail.card ? <DecisionCardView card={detail.card} /> : null}
          </>
        ) : null}
      </section>
    </div>
  );
}
