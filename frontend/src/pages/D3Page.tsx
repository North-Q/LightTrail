/** D3 决策页（E7-9，核心页）：
 *  三态大卡（铁律②：语义色 + 图标 + 文字）+ 置信度三层（铁律①：主值 + 区间条 + 依据）
 *  + 概率依据条 + 倒计时（time_window）+ 曝光三角联动（纯前端 EV 守恒）+ 四步推理（移动端抽屉）。
 */

import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { sendDecide } from "../api/client";
import type { DecisionCard, SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";
import { DecisionCardView } from "../components/DecisionCard";
import { CheckIcon, ClockIcon, WarnIcon } from "../components/icons";

type Verdict = "go" | "wait" | "risk";

interface SliderState {
  aperture: number;
  shutter: number;
  iso: number;
}

const STATES: { key: Verdict; label: string; icon: (props: { className?: string }) => ReactElement }[] = [
  { key: "go", label: "去", icon: CheckIcon },
  { key: "wait", label: "再等等", icon: ClockIcon },
  { key: "risk", label: "放弃", icon: WarnIcon },
];

const STATE_DESC: Record<Verdict, string> = {
  go: "值得出门，按窗口到场",
  wait: "暂缓，观察临近数据再定",
  risk: "不建议专程，有更好窗口",
};

function confidenceMath(level: string): { value: number; lo: number; hi: number } {
  switch (level) {
    case "high":
      return { value: 78, lo: 68, hi: 88 };
    case "low":
      return { value: 34, lo: 22, hi: 48 };
    default:
      return { value: 58, lo: 46, hi: 72 };
  }
}

function verdictFrom(conclusion: string): Verdict {
  if (/等等|再等|观察|谨慎|不建议|放弃/.test(conclusion)) {
    return /不建议|放弃/.test(conclusion) ? "risk" : "wait";
  }
  return "go";
}

function firstTime(text: string): string {
  const match = text.match(/(\d{1,2}):(\d{2})/);
  return match ? `${match[1].padStart(2, "0")}:${match[2]}` : "";
}

function countdownSeconds(targetTime: string): number {
  const [hour, minute] = targetTime.split(":").map((part) => parseInt(part, 10));
  const now = new Date();
  let target = new Date(now);
  target.setHours(hour, minute, 0, 0);
  if (target.getTime() <= now.getTime()) {
    target = new Date(target.getTime() + 24 * 3600 * 1000);
  }
  return Math.max(0, Math.floor((target.getTime() - now.getTime()) / 1000));
}

function formatCountdown(total: number): string {
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatShutter(seconds: number): string {
  return seconds >= 1 ? `${seconds.toFixed(1)}s` : `1/${Math.round(1 / seconds)}s`;
}

function evOf(aperture: number, shutter: number, iso: number): number {
  return Math.log2((aperture * aperture) / shutter) + Math.log2(iso / 100);
}

export function D3Page() {
  const [input, setInput] = useState("");
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [activeVerdict, setActiveVerdict] = useState<Verdict | null>(null);
  const [fieldMode, setFieldMode] = useState(false);
  const [targetTime, setTargetTime] = useState("");
  const [remaining, setRemaining] = useState(0);
  const [whyOpenMobile, setWhyOpenMobile] = useState(false);
  const [exposure, setExposure] = useState<SliderState>({ aperture: 4, shutter: 1 / 250, iso: 100 });
  const reasonsRef = useRef<{ title: string; detail: string; src: string }[]>([]);
  const [reasons, setReasons] = useState<{ title: string; detail: string; src: string }[]>([]);
  const { addSource } = useSources();

  // 倒计时：基于 card.time_window 首个时刻，每秒滴答
  useEffect(() => {
    if (!targetTime) {
      setRemaining(0);
      return;
    }
    let seconds = countdownSeconds(targetTime);
    setRemaining(seconds);
    const timer = window.setInterval(() => {
      seconds = countdownSeconds(targetTime);
      setRemaining(seconds);
      if (seconds === 0) {
        window.clearInterval(timer);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [targetTime]);

  const confidence = useMemo(() => (card ? confidenceMath(card.confidence) : null), [card]);
  const verdict: Verdict | null = useMemo(
    () => (card ? verdictFrom(card.conclusion) : activeVerdict),
    [card, activeVerdict],
  );

  // 曝光三角联动（EV 守恒，纯前端）
  const ev = evOf(exposure.aperture, exposure.shutter, exposure.iso);
  function setAperture(value: number): void {
    setExposure((prev) => {
      const aperture = Math.min(22, Math.max(1, value));
      const isoFactor = prev.iso / 100;
      const shutter = (aperture * aperture) / Math.pow(2, evOf(prev.aperture, prev.shutter, prev.iso)) / isoFactor;
      return { ...prev, aperture, shutter: Math.min(30, Math.max(1 / 8000, shutter)) };
    });
  }
  function setShutter(value: number): void {
    setExposure((prev) => {
      const shutter = Math.min(30, Math.max(1 / 8000, value));
      const isoFactor = prev.iso / 100;
      const aperture = Math.sqrt((shutter * Math.pow(2, evOf(prev.aperture, prev.shutter, prev.iso)) * isoFactor));
      return { ...prev, shutter, aperture: Math.min(22, Math.max(1, aperture)) };
    });
  }
  function setIso(value: number): void {
    setExposure((prev) => {
      const iso = Math.min(25600, Math.max(50, value));
      const isoFactor = iso / 100;
      const shutter = (prev.aperture * prev.aperture) / Math.pow(2, evOf(prev.aperture, prev.shutter, prev.iso)) / isoFactor;
      return { ...prev, iso, shutter: Math.min(30, Math.max(1 / 8000, shutter)) };
    });
  }

  async function submit(): Promise<void> {
    const text = input.trim();
    if (!text || running) {
      return;
    }
    setError("");
    setCard(null);
    setActiveVerdict(null);
    reasonsRef.current = [];
    setReasons([]);
    setRunning(true);
    try {
      await sendDecide(text, "", (event: SSEEvent) => {
        if (event.type === "card") {
          setCard(event.card);
          localStorage.setItem("lt.last_card", JSON.stringify({ card: event.card, ts: Date.now() }));
          setActiveVerdict(verdictFrom(event.card.conclusion));
          const windowText = event.card.time_window ?? "";
          const parsed = firstTime(windowText);
          if (parsed) {
            setTargetTime(parsed);
          }
        } else if (event.type === "tool_result") {
          if (event.data_source) {
            addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
          }
          const next = [...reasonsRef.current, { title: event.name, detail: event.result ?? "", src: event.data_source ?? "" }];
          reasonsRef.current = next;
          setReasons(next);
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

  const goProb = confidence ? confidence.value : 0;
  const waitProb = confidence ? Math.round((100 - confidence.value) * 0.65) : 0;
  const riskProb = confidence ? 100 - goProb - waitProb : 0;

  const reasonList = reasons.length > 0 ? reasons : card?.evidence.map((source) => ({ title: source.tool, detail: source.note ?? "", src: source.tool })) ?? [];

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">决策</h1>
          <p className="page-sub">临场该不该出门、几点到、带什么——每个判断都能给出依据。</p>
        </div>
        <button type="button" className={`btn ${fieldMode ? "btn-ghost" : "btn-primary"}`} onClick={() => setFieldMode((on) => !on)}>
          {fieldMode ? "退出现场模式" : "现场模式"}
        </button>
      </header>

      <section className={`verdict-card decision-panel${fieldMode ? " field-mode" : ""}`}>
        <span className="card-kicker">三态结论</span>
        <div className="state-row">
          {STATES.map((state) => {
            const Icon = state.icon;
            const active = verdict === state.key;
            return (
              <button
                type="button"
                key={state.key}
                className={`state-btn ${state.key}${active ? " is-active" : ""}`}
                onClick={() => setActiveVerdict(state.key)}
                aria-pressed={active}
              >
                <Icon /> {state.label}
              </button>
            );
          })}
        </div>
        <p className="page-sub" style={{ margin: "12px 0 0" }}>
          {verdict ? STATE_DESC[verdict] : "运行一次决策后，这里给出三态结论（可手动修正）"}
        </p>

        <div className="countdown-row">
          <span className="countdown-value">{remaining > 0 ? formatCountdown(remaining) : "—"}</span>
          <span className="countdown-label">
            {targetTime ? `距关键时刻 ${targetTime}` : "决策后依据方案时间窗口显示倒计时"}
          </span>
        </div>

        {confidence ? (
          <div className="card-block">
            <h4>置信度（主值 + 区间 + 依据）</h4>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <span className="confidence-display">{confidence.value}<small style={{ fontSize: 20 }}>%</small></span>
              <span className="confidence-sub">区间 {confidence.lo}–{confidence.hi}</span>
            </div>
            <div className="interval-scale">
              <span className="interval-range" style={{ left: `${confidence.lo}%`, width: `${confidence.hi - confidence.lo}%` }} />
              <span className="interval-marker" style={{ left: `${confidence.value}%` }} />
            </div>
            {(card?.evidence ?? []).length > 0 ? (
              <ul className="evidence-list">
                {card?.evidence.map((source, index) => (
                  <li key={`${source.tool}-${index}`}>
                    <span className={`badge confidence-${source.confidence || "low"}`}>{source.confidence || "low"}</span>
                    <code className="source-tool">{source.tool || "—"}</code>
                    <span className="source-field">{source.field || ""}</span>
                    {source.note ? <span className="source-note">{source.note}</span> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="trace-empty">暂无依据（模型未给出来源）。</p>
            )}
          </div>
        ) : null}

        {confidence ? (
          <div className="prob-bars">
            <h4 className="card-kicker" style={{ margin: 0 }}>可能性评估</h4>
            {(
              [
                { label: "去", value: goProb, className: "go" },
                { label: "再等等", value: waitProb, className: "amber" },
                { label: "放弃/风险", value: riskProb, className: "blue" },
              ] as const
            ).map((row) => (
              <div className="prob-row" key={row.label}>
                <div className="prob-head">
                  <span>{row.label}</span>
                  <span className="num">{row.value}%</span>
                </div>
                <div className="prob-track">
                  <span className={`prob-fill ${row.className}`} style={{ width: `${row.value}%` }} />
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {confidence ? (
          <p className="page-sub" style={{ margin: "8px 0 0" }}>数值由置信度区间换算，供直观参考。</p>
        ) : null}

        <div className="param-cards">
          <div className="param-card">
            <div className="param-name">光圈</div>
            <div className="param-value">f/{exposure.aperture.toFixed(1)}</div>
          </div>
          <div className="param-card">
            <div className="param-name">快门</div>
            <div className="param-value">{formatShutter(exposure.shutter)}</div>
          </div>
          <div className="param-card">
            <div className="param-name">ISO</div>
            <div className="param-value">{exposure.iso}</div>
          </div>
          <div className="param-card">
            <div className="param-name">EV 守恒</div>
            <div className="param-value num">{ev.toFixed(1)}</div>
          </div>
        </div>
        <p className="page-sub" style={{ margin: "8px 0 0" }}>曝光三角联动（拖动任一滑块，其余自动换算保持 EV）</p>
        <div className="card-block">
          <div className="exp-slider-row">
            <span className="exp-slider-label">光圈</span>
            <input type="range" min={1} max={22} step={0.1} value={exposure.aperture} onChange={(e) => setAperture(parseFloat(e.target.value))} aria-label="光圈" />
            <span className="exp-slider-val">f/{exposure.aperture.toFixed(1)}</span>
          </div>
          <div className="exp-slider-row">
            <span className="exp-slider-label">快门</span>
            <input type="range" min={1 / 8000} max={30} step={1 / 1000} value={exposure.shutter} onChange={(e) => setShutter(parseFloat(e.target.value))} aria-label="快门" />
            <span className="exp-slider-val">{formatShutter(exposure.shutter)}</span>
          </div>
          <div className="exp-slider-row">
            <span className="exp-slider-label">ISO</span>
            <input type="range" min={50} max={25600} step={50} value={exposure.iso} onChange={(e) => setIso(parseFloat(e.target.value))} aria-label="ISO" />
            <span className="exp-slider-val num">{exposure.iso}</span>
          </div>
        </div>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">临场提问</span>
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
            {running ? "决策中…" : "决策"}
          </button>
        </form>
        {error ? <p className="message-error">{error}</p> : null}
      </section>

      {card ? <div style={{ marginTop: 20 }}><DecisionCardView card={card} /></div> : null}

      {/* 为什么这么判断：桌面内联 / 移动端底部抽屉 */}
      <section className="why-panel card" style={{ marginTop: 20 }}>
        <span className="card-kicker">为什么这么判断（四步推理链）</span>
        {reasonList.length === 0 ? (
          <p className="trace-empty">运行决策后，这里展示工具调用链推理（为何这么判断）。</p>
        ) : (
          <ol className="reason-list">
            {reasonList.slice(0, 4).map((reason, index) => (
              <li key={`${reason.title}-${index}`}>
                <div className="reason-head">
                  <span className="reason-step">{String(index + 1).padStart(2, "0")}</span>
                  <strong>{reason.title}</strong>
                  {reason.src ? <span className="src-tag">{reason.src}</span> : null}
                  {reasons.length === 0 ? <span className="src-tag">决策依据</span> : null}
                </div>
                <p>{reason.detail.slice(0, 140)}</p>
              </li>
            ))}
          </ol>
        )}
        <button type="button" className="btn btn-ghost why-mobile-trigger" onClick={() => setWhyOpenMobile(true)}>
          查看推理链
        </button>
      </section>

      {/* 移动端底部抽屉（≤820px 展示） */}
      <div className={`sheet-backdrop${whyOpenMobile ? " is-open" : ""}`} onClick={() => setWhyOpenMobile(false)} aria-hidden="true" />
      <aside className={`sheet${whyOpenMobile ? " is-open" : ""}`} role="dialog" aria-modal="true" aria-label="为什么这么判断">
        <div className="sheet-handle" aria-hidden="true" />
        <div className="sheet-head">
          <h3>为什么这么判断</h3>
          <button type="button" className="icon-btn sheet-close" onClick={() => setWhyOpenMobile(false)} aria-label="关闭">
            ✕
          </button>
        </div>
        <div className="sheet-body">
          {reasonList.length === 0 ? (
            <p className="trace-empty">运行决策后，这里展示推理链。</p>
          ) : (
            <ol className="reason-list">
              {reasonList.slice(0, 4).map((reason, index) => (
                <li key={`sheet-${reason.title}-${index}`}>
                  <div className="reason-head">
                    <span className="reason-step">{String(index + 1).padStart(2, "0")}</span>
                    <strong>{reason.title}</strong>
                    {reason.src ? <span className="src-tag">{reason.src}</span> : null}
                  </div>
                  <p>{reason.detail.slice(0, 140)}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
    </div>
  );
}
