/** DecisionCard 渲染页（E7-5 核心页 3）：从历史会话读取最近决策卡，结构化渲染。 */

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

export function CardPage() {
  const [metas] = useState<SessionMeta[]>(() => loadSessionMetas());
  const [selectedId, setSelectedId] = useState<string>("");
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [hint, setHint] = useState("");
  const [loading, setLoading] = useState(false);

  async function pick(sessionId: string): Promise<void> {
    setSelectedId(sessionId);
    setCard(null);
    setHint("");
    if (!sessionId) {
      return;
    }
    setLoading(true);
    try {
      const body = await getSession(sessionId);
      const workspace = (body.session as { workspace?: { last_card?: DecisionCard } }).workspace;
      const lastCard = workspace?.last_card ?? null;
      setCard(lastCard);
      if (!lastCard) {
        setHint("该会话还没有决策卡片（请先在「对话」页用「一句话决策」生成）。");
      }
    } catch {
      setCard(null);
      setHint("读取会话失败，请确认后端服务已启动。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card-page">
      <header className="chat-toolbar">
        <span className="page-title">决策卡片渲染</span>
        <select
          aria-label="选择会话"
          value={selectedId}
          onChange={(event) => void pick(event.target.value)}
        >
          <option value="">选择历史会话…</option>
          {metas.map((meta) => (
            <option key={meta.id} value={meta.id}>
              {meta.title}（{meta.id.slice(0, 8)}）
            </option>
          ))}
        </select>
      </header>
      {loading ? <p>载入中…</p> : null}
      {hint ? <p className="trace-empty">{hint}</p> : null}
      {card ? <DecisionCardView card={card} /> : null}
    </section>
  );
}
