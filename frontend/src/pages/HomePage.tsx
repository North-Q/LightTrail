/** 旅程总览页：今日决策速览（环图接最近一次真实决策卡）+ 四阶段入口 + 最近计划（会话历史）。 */

import { useEffect, useState } from "react";
import { getSession } from "../api/client";
import type { DecisionCard, SessionMeta } from "../api/events";
import { DecisionCardView } from "../components/DecisionCard";
import { Ring } from "../components/Ring";
import { go } from "../components/AppShell";
import { PinIcon, RefreshIcon, SparkIcon, TargetIcon } from "../components/icons";

const LAST_CARD_KEY = "lt.last_card";

function loadMetas(): SessionMeta[] {
  try {
    return JSON.parse(localStorage.getItem("lt.sessions") ?? "[]") as SessionMeta[];
  } catch {
    return [];
  }
}

function confidenceValue(level: string): number {
  switch (level) {
    case "high":
      return 78;
    case "low":
      return 34;
    default:
      return 58;
  }
}

const STAGES = [
  { key: "d1", label: "灵感", desc: "一句话出方案 / 参考图反推", icon: SparkIcon },
  { key: "d2", label: "规划", desc: "机位×天象匹配", icon: PinIcon },
  { key: "d3", label: "决策", desc: "该不该出门、几点去", icon: TargetIcon },
  { key: "d4", label: "复盘", desc: "照片分析与处方", icon: RefreshIcon },
];

interface SelectedSession {
  sessionId: string;
  history: { role: string; content: string }[];
  card: DecisionCard | null;
}

interface Profile {
  camera_body: string;
  lenses: string[];
  preferences: string[];
  common_locations: string[];
  skill_level: string;
}

export function HomePage() {
  const metas = loadMetas();
  const [selected, setSelected] = useState<SelectedSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [lastCard, setLastCard] = useState<DecisionCard | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const resp = await fetch("/api/profile");
        if (resp.ok) {
          setProfile((await resp.json()) as Profile);
        }
      } catch {
        setProfile(null);
      }
    })();
    try {
      const raw = localStorage.getItem(LAST_CARD_KEY);
      if (raw) {
        setLastCard((JSON.parse(raw) as { card: DecisionCard }).card);
      }
    } catch {
      setLastCard(null);
    }
  }, []);

  async function openSession(sessionId: string): Promise<void> {
    setBusy(true);
    try {
      const body = await getSession(sessionId);
      const workspace = (body.session as { workspace?: { last_card?: DecisionCard } }).workspace;
      setSelected({
        sessionId: body.session.session_id,
        history: body.session.history ?? [],
        card: workspace?.last_card ?? null,
      });
    } catch {
      setSelected(null);
    } finally {
      setBusy(false);
    }
  }

  const ringValue = lastCard ? confidenceValue(lastCard.confidence) : null;
  const sourceTool = lastCard?.evidence?.[0]?.tool;

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">今日拍摄旅程</h1>
          <p className="page-sub">从灵感走向复盘——每一步都可溯源。</p>
        </div>
      </header>

      <section className="overview-hero">
        <div className="card">
          <span className="card-kicker">今日决策速览</span>
          {lastCard && ringValue !== null ? (
            <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
                <Ring
                  value={ringValue}
                  label={lastCard.confidence === "high" ? "高置信" : lastCard.confidence === "low" ? "低置信" : "中等置信"}
                />
                <span className="src-time">依据：{sourceTool || "决策卡"}</span>
              </div>
              <div style={{ flex: 1, minWidth: 200 }}>
                <p style={{ margin: "0 0 8px" }}>{lastCard.conclusion}</p>
                <div className="chip-row">
                  <button type="button" className="btn btn-primary" onClick={() => go("#/d3")}>
                    <TargetIcon /> 去决策
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={() => go("#/d2")}>
                    <PinIcon /> 看规划
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div>
              <p className="trace-empty" style={{ marginTop: 0 }}>
                还没有决策记录——先在「决策」页问一句，例如「今晚火烧云值得冲吗」。
              </p>
              <button type="button" className="btn btn-primary" onClick={() => go("#/d3")}>
                <TargetIcon /> 去决策
              </button>
            </div>
          )}
          {lastCard?.time_window ? (
            <>
              <span className="card-kicker" style={{ marginTop: 16, display: "block" }}>下一窗口</span>
              <p className="page-sub" style={{ margin: 0 }}>{lastCard.time_window}</p>
            </>
          ) : null}
        </div>

        <div className="card">
          <span className="card-kicker">记忆摘要</span>
          {profile ? (
            <ul className="event-list">
              <li>器材：{profile.camera_body || "未填写"}{profile.lenses.length > 0 ? ` · ${profile.lenses.join(" / ")}` : ""}</li>
              <li>偏好题材：{profile.preferences.length > 0 ? profile.preferences.join(" · ") : "未填写"}</li>
              <li>常去机位：{profile.common_locations.length > 0 ? profile.common_locations.join(" · ") : "未填写"}</li>
            </ul>
          ) : (
            <p className="trace-empty">后端未启动或档案为空——去「我的记忆」页填写。</p>
          )}
          <button type="button" className="btn btn-ghost" style={{ marginTop: 16 }} onClick={() => go("#/m1")}>
            编辑我的记忆
          </button>
        </div>
      </section>

      <section className="stage-grid">
        {STAGES.map((stage) => {
          const Icon = stage.icon;
          return (
            <button type="button" className="stage-card" key={stage.key} onClick={() => go(`#/${stage.key}`)}>
              <span className="stage-ic"><Icon /></span>
              <h3>{stage.label}</h3>
              <p>{stage.desc}</p>
            </button>
          );
        })}
      </section>

      <section className="card" style={{ marginTop: 24 }}>
        <span className="card-kicker">最近计划 · 会话历史</span>
        {metas.length === 0 ? (
          <p className="trace-empty">还没有会话——去「灵感」页发一条消息试试。</p>
        ) : (
          <ul className="recent-list">
            {metas.slice(0, 6).map((meta) => (
              <li key={meta.id}>
                <button type="button" className="recent-item" onClick={() => void openSession(meta.id)}>
                  <strong>{meta.title}</strong>
                  <span>{new Date(meta.created).toLocaleString("zh-CN", { hour12: false })} · {meta.id.slice(0, 8)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {busy ? <p className="trace-empty">载入中…</p> : null}
        {selected ? (
          <div style={{ marginTop: 16 }}>
            <h4 style={{ margin: "0 0 8px", color: "var(--text-secondary)" }}>会话回放 · {selected.sessionId.slice(0, 8)}</h4>
            {selected.history.slice(-4).map((message, index) => (
              <p className="page-sub" key={index} style={{ margin: "4px 0" }}>
                <strong>{message.role === "user" ? "用户" : "光迹"}</strong>：{message.content.slice(0, 120)}
              </p>
            ))}
            {selected.card ? <DecisionCardView card={selected.card} /> : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}
