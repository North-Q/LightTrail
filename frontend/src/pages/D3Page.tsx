/** D3 决策页（E7-6 骨架：三态卡 + 决策输入 + DecisionCard 底座；置信度三层/倒计时 E7-9 完善）。 */

import { useState } from "react";
import { sendDecide } from "../api/client";
import type { DecisionCard, SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";
import { DecisionCardView } from "../components/DecisionCard";
import { CheckIcon, ClockIcon, WarnIcon } from "../components/icons";

const STATES = [
  { key: "go", label: "去", icon: CheckIcon, className: "go" },
  { key: "wait", label: "再等等", icon: ClockIcon, className: "wait" },
  { key: "risk", label: "放弃", icon: WarnIcon, className: "risk" },
];

export function D3Page() {
  const [input, setInput] = useState("");
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const { addSource } = useSources();

  async function submit(): Promise<void> {
    const text = input.trim();
    if (!text || running) {
      return;
    }
    setError("");
    setCard(null);
    setRunning(true);
    try {
      await sendDecide(text, "", (event: SSEEvent) => {
        if (event.type === "card") {
          setCard(event.card);
        } else if (event.type === "tool_result") {
          if (event.data_source) {
            addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
          }
        } else if (event.type === "error") {
          setError(event.message);
        }
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">D3 · 决策</h1>
          <p className="page-sub">临场该不该出门、几点到、带什么——每个判断都能给出依据。</p>
        </div>
      </header>

      <section className="verdict-card">
        <span className="card-kicker">三态结论（示例交互 · E7-9 完善）</span>
        <div className="state-row">
          {STATES.map((state) => {
            const Icon = state.icon;
            return (
              <button type="button" className={`state-btn ${state.className}`} key={state.key}>
                <Icon /> {state.label}
              </button>
            );
          })}
        </div>
        <span className="fake-tag" style={{ marginTop: 12 }}>◆ 三态高亮/倒计时/置信度三层在 E7-9 接入真实 card 事件</span>

        <div className="countdown-row">
          <span className="countdown-value">—</span>
          <span className="countdown-label">距关键时刻（card.time_window 接入后启用倒计时）</span>
        </div>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">临场决策</span>
        <form
          className="prompt-row"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="今晚火烧云值得冲吗？"
            disabled={running}
            aria-label="决策提问"
          />
          <button type="submit" className="btn btn-primary" disabled={running || !input.trim()}>
            决策
          </button>
        </form>
        {error ? <p className="message-error">{error}</p> : null}
      </section>

      {card ? <div style={{ marginTop: 20 }}><DecisionCardView card={card} /></div> : null}
    </div>
  );
}
