/** 旅程总览页（E7-6 骨架 / E7-7 完善）：今日决策速览（环图）+ 四阶段入口 + 最近计划（会话历史）。 */

import { useState } from "react";
import { getSession } from "../api/client";
import type { DecisionCard, SessionMeta } from "../api/events";
import { useSources } from "../context/SourceContext";
import { DecisionCardView } from "../components/DecisionCard";
import { Ring } from "../components/Ring";
import { go } from "../components/AppShell";
import { PinIcon, RefreshIcon, SparkIcon, TargetIcon } from "../components/icons";

function loadMetas(): SessionMeta[] {
  try {
    return JSON.parse(localStorage.getItem("lt.sessions") ?? "[]") as SessionMeta[];
  } catch {
    return [];
  }
}

const STAGES = [
  { key: "d1", label: "灵感", desc: "一句话出方案 / 参考图反推", icon: SparkIcon },
  { key: "d2", label: "规划", desc: "机位×天象匹配 / 赶场编排", icon: PinIcon },
  { key: "d3", label: "决策", desc: "临场赌注 / 参数推荐", icon: TargetIcon },
  { key: "d4", label: "复盘", desc: "照片智能分析 / 可执行处方", icon: RefreshIcon },
];

interface SelectedSession {
  sessionId: string;
  history: { role: string; content: string }[];
  card: DecisionCard | null;
}

export function HomePage() {
  const metas = loadMetas();
  const [selected, setSelected] = useState<SelectedSession | null>(null);
  const [busy, setBusy] = useState(false);
  const { addSource } = useSources();

  // 演示数据源登记（环图/记忆片段的来源说明），保持可解释性诚实
  addSource({ name: "示例计算", note: "今日火烧云概率（演示值，非实时预测）", time: "示例", fake: true });

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

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">今日拍摄旅程</h1>
          <p className="page-sub">从灵感走向复盘——每一步都可溯源。</p>
        </div>
        <span className="fake-tag">◆ 环图与记忆片段为示例数据</span>
      </header>

      <section className="overview-hero">
        <div className="card">
          <span className="card-kicker">今日决策速览</span>
          <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
            <Ring value={72} label="火烧云概率" />
            <div style={{ flex: 1, minWidth: 200 }}>
              <p style={{ margin: "0 0 8px" }}>
                傍晚日落方向低云比例适中，<strong>值得出门</strong>；推荐 17:40 前到达机位。
              </p>
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
          <span className="card-kicker" style={{ marginTop: 16, display: "block" }}>下一窗口预告</span>
          <p className="page-sub" style={{ margin: 0 }}>蓝调时刻 18:12–18:40 · 银河拱桥 21:40 起东南方向（示例数据）</p>
        </div>

        <div className="card">
          <span className="card-kicker">记忆摘要</span>
          <ul className="event-list">
            <li>器材：松下 S5M2 · 契卡 14mm</li>
            <li>偏好题材：火烧云 · 星空 · 城市风光</li>
            <li>常去机位：西湖断桥 · 崇明东滩</li>
          </ul>
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
