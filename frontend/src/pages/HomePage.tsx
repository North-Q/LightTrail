/** 旅程总览页：最近决策记录（环图接最近一次真实决策卡）+ 四阶段入口 + 最近计划（会话历史）。 */

import { useEffect, useState } from "react";
import { getSession } from "../api/client";
import { getJSON } from "../api/client";
import type { SavedSession } from "../api/savedResults";
import { useRequestScope } from "../api/useRequestScope";
import type { DecisionCard, ProfilePayload, SessionDetail, SessionMeta } from "../api/events";
import { DecisionCardView } from "../components/DecisionCard";
import { decisionState } from "./decisionState";
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

const STAGES = [
  { key: "d1", label: "灵感", desc: "一句话出方案 / 参考图反推", icon: SparkIcon },
  { key: "d2", label: "规划", desc: "机位×天象匹配", icon: PinIcon },
  { key: "d3", label: "决策", desc: "该不该出门、几点去", icon: TargetIcon },
  { key: "d4", label: "复盘", desc: "照片分析与处方", icon: RefreshIcon },
];

interface SelectedSession {
  sessionId: string;
  history: SessionDetail["session"]["history"];
  card: DecisionCard | null;
  versions: SavedSession["results"];
}

/** 档案视图模型：把契约（generated.ts）里的可空字段归一为非空（仅页面内展示用）。 */
type Profile = { [K in keyof ProfilePayload]-?: NonNullable<ProfilePayload[K]> };

export function HomePage() {
  const [metas, setMetas] = useState(loadMetas);
  const historyWait = useRequestScope();
  const detailWait = useRequestScope();
  const [selected, setSelected] = useState<SelectedSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [lastCard, setLastCard] = useState<DecisionCard | null>(null);

  useEffect(() => {
    const controller = historyWait.begin();
    void getJSON<SavedSession[]>("/api/sessions", controller.signal).then(sessions => {
      if (!historyWait.isCurrent(controller) || !Array.isArray(sessions)) return;
      setMetas(sessions.map(session => ({ id: session.session_id, title: String(session.results?.[0]?.request?.request ?? session.results?.[0]?.request?.message ?? session.results?.[0]?.request?.subject_type ?? "已保存计划"), created: session.created_at })));
      const decisions = sessions.flatMap(session => session.results ?? []).filter(result => result.card).sort((a, b) => b.created_at.localeCompare(a.created_at));
      setLastCard(decisions[0]?.card ?? null);
    }).catch(() => { /* 读取失败不冒充新的保存确认。 */ });
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
    const controller = detailWait.begin();
    setBusy(true);
    try {
      const body = await getSession(sessionId, controller.signal);
      if (!detailWait.isCurrent(controller)) return;
      const workspace = (body.session as { workspace?: { last_card?: DecisionCard } }).workspace;
      setSelected({
        sessionId: body.session.session_id,
        history: body.session.history ?? [],
        card: workspace?.last_card ?? null,
        versions: body.session.results ?? [],
      });
    } catch {
      if (detailWait.isCurrent(controller)) setSelected(null);
    } finally {
      detailWait.finish(controller, () => setBusy(false));
    }
  }



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
          <span className="card-kicker">最近决策记录</span>
          {lastCard ? (
            <div>
              <p className="card-degraded">历史结果 · {decisionState(lastCard) === "needs_data" ? "数据不足" : "需重新查询后决定出发"}</p>
              <DecisionCardView card={lastCard} historical />
              <button type="button" className="btn btn-primary" onClick={() => go("#/d3")}>
                <TargetIcon /> 重新查询
              </button>
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
              <span className="card-kicker" style={{ marginTop: 16, display: "block" }}>历史窗口</span>
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
                <strong>{message.role === "user" ? "用户" : "光迹"}</strong>：{typeof message.content === "string" ? message.content.slice(0, 120) : "（非文本消息）"}
              </p>
            ))}
            {selected.card ? <DecisionCardView card={selected.card} historical /> : null}
            <div className="chip-row">{selected.versions?.map(result => <a key={result.result_id} className="btn btn-ghost"
              href={`#/${result.kind === "spot_plan" ? "d2" : result.kind === "decision" ? "d3" : "d1"}?session=${encodeURIComponent(result.session_id)}&result=${encodeURIComponent(result.result_id)}`}>
              恢复 v{result.version} 并继续
            </a>)}</div>
          </div>
        ) : null}
      </section>
    </div>
  );
}
