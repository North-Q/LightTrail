/** D3 决策页（E7-9，核心页）：
 *  三态大卡（语义色 + 图标 + 文字）+ 数据资格与缺失原因
 *  + 实际依据 + 倒计时（time_window）+ 曝光三角联动（纯前端 EV 守恒）+ 四步推理（移动端抽屉）。
 */

import { useEffect, useRef, useState, type ReactElement } from "react";
import { decisionState } from "./decisionState";
import { sendDecide } from "../api/client";
import { STOP_WAITING_NOTICE } from "../api/requestScope";
import { useRequestScope } from "../api/useRequestScope";
import type { DecisionCard, SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";
import { DecisionCardView } from "../components/DecisionCard";
import { DataNotice } from "../components/DataNotice";
import { SavedHistory } from "../components/SavedHistory";
import { FeedbackPanel } from "../components/FeedbackPanel";
import { useSavedResults } from "../api/savedResults";
import { SpotPlanView } from "./SpotPlanView";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { CheckIcon, ClockIcon, WarnIcon } from "../components/icons";
import { changeExposure, exposureValue, type Exposure } from "./exposure";

type Verdict = "go" | "wait" | "risk";

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

/** 三态结论直接读卡片字段（不再用中文正则猜结论，B4-3）。 */
function asVerdict(raw: string | undefined): Verdict | null {
  return raw === "go" || raw === "wait" || raw === "risk" ? raw : null;
}

function countdownSeconds(targetTime: string): number {
  const targetMs = Date.parse(targetTime);
  return Number.isNaN(targetMs) ? 0 : Math.max(0, Math.floor((targetMs - Date.now()) / 1000));
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

export function D3Page() {
  const feedbackDirty = useRef(false);
  const [feedbackReady, setFeedbackReady] = useState(false);
  const [input, setInput] = useState("");
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const waiting = useRequestScope();
  const [decisionText, setDecisionText] = useState("");
  const [activeVerdict, setActiveVerdict] = useState<Verdict | null>(null);
  const [fieldMode, setFieldMode] = useState(false);
  const [targetTime, setTargetTime] = useState("");
  const [remaining, setRemaining] = useState(0);
  const [whyOpenMobile, setWhyOpenMobile] = useState(false);
  const [exposure, setExposure] = useState<Exposure>({ aperture: 4, shutter: 1 / 250, iso: 100 });
  const reasonsRef = useRef<{ title: string; detail: string; src: string }[]>([]);
  const [reasons, setReasons] = useState<{ title: string; detail: string; src: string }[]>([]);
  const { addSource } = useSources();
  const [historical, setHistorical] = useState(false);
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const saved = useSavedResults("decision", (result, history) => {
    setFeedbackReady(true);
    setCard(result.card ?? null); setDecisionText(result.text ?? "");
    setMessages((result.history ?? []).filter(message => message.role === "user" || message.role === "assistant").map(message => ({ role: String(message.role), content: String(message.content ?? "") })));
    setHistorical(history); setTargetTime(!history && result.card && decisionState(result.card) === "ready" ? result.card.window_start : ""); setActiveVerdict(null); setReasons([]);
  });

  // 倒计时优先使用后端结构化窗口；旧卡才兼容解析展示文案。
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

  // 置信度明细与三态结论都来自卡片的真实字段（后端规则推导 + 模型结论，前端不自算）
  const [clockNow, setClockNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setClockNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const status = card ? (historical ? "stale" : decisionState(card, clockNow)) : null;
  const verdict: Verdict | null = status === "needs_data" ? "wait" : status === "stale" ? null : asVerdict(card?.verdict) ?? activeVerdict;

  // 曝光三角联动（EV 守恒，纯前端）
  const ev = exposureValue(exposure);

  async function submit(requery = false): Promise<void> {
    const text = requery ? String(saved.active?.request?.request ?? "") : input.trim();
    if (!text || running) {
      return;
    }
    setError("");
    if (feedbackDirty.current && !window.confirm("反馈草稿尚未保存，开始生成将丢弃草稿。继续？")) return;
    saved.start();
    setFeedbackReady(false);
    setNotice("");
    setDecisionText("");
    setCard(null);
    setHistorical(false);
    setActiveVerdict(null);
    setTargetTime("");
    reasonsRef.current = [];
    setReasons([]);
    setRunning(true);
    const controller = waiting.begin();
    try {
      await sendDecide(text, saved.pointer.session_id, (event: SSEEvent) => {
        if (!waiting.isCurrent(controller)) return;
        if (event.type === "card") {
          setCard(event.card);
          localStorage.setItem("lt.last_card", JSON.stringify({ card: event.card, ts: Date.now() }));
          setActiveVerdict(asVerdict(event.card.verdict));
          const parsed = decisionState(event.card) === "ready" ? event.card.window_start : "";
          if (parsed) {
            setTargetTime(parsed);
          }
        } else if (event.type === "token") {
          setDecisionText((previous) => previous + event.content);
        } else if (event.type === "tool_result") {
          if (event.data_source) {
            addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
          }
          const next = [...reasonsRef.current, { title: event.name, detail: event.result ?? "", src: event.data_source ?? "" }];
          reasonsRef.current = next;
          setReasons(next);
        } else if (event.type === "error") {
          setError(event.message);
        } else if (event.type === "done") {
          saved.confirm(event);
        }
      }, controller.signal, saved.pointer.result_id, requery);
    } catch (caught) {
      if (waiting.isCurrent(controller)) setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      waiting.finish(controller, () => setRunning(false));
    }
  }


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

      <SavedHistory {...saved} disabled={running || saved.loading} onRestore={target => {
        if (!feedbackDirty.current || window.confirm("反馈草稿尚未保存，切换将丢弃草稿。继续？")) void saved.restore(target);
      }} onRequery={saved.active?.plan ? undefined : () => void submit(true)} />
      {saved.active?.plan ? <SpotPlanView plan={saved.active.plan} frozen /> : null}
      <div className="message-list">{messages.map((message, index) => <div key={index} className={`message message-${message.role}`}><div className={`bubble${message.role === "assistant" ? " bubble-markdown" : ""}`}>{message.role === "assistant" ? <MarkdownMessage content={message.content} /> : message.content}</div></div>)}</div>
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
                disabled={Boolean(card) && status !== "ready"}
              >
                <Icon /> {state.label}
              </button>
            );
          })}
        </div>
        <p className="page-sub" style={{ margin: "12px 0 0" }}>
          {status === "needs_data" ? "数据不足，暂不能判断是否值得出门；补齐后重新查询" : status === "stale" ? "历史或远期结果，请重新查询目标日期的数据" : verdict ? STATE_DESC[verdict] : "运行一次决策后，这里给出行动建议"}
        </p>

        <div className="countdown-row">
          <span className="countdown-value">{status === "ready" && remaining > 0 ? formatCountdown(remaining) : "—"}</span>
          <span className="countdown-label">
            {status === "ready" && targetTime ? `距关键时刻 ${targetTime}` : "有效的当前结果才显示倒计时"}
          </span>
        </div>

        <p className="page-sub">证据等级仅描述可用依据；火烧云启发式评分未经概率校准。</p>

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
            <div className="param-value">{Math.round(exposure.iso)}</div>
          </div>
          <div className="param-card">
            <div className="param-name">EV 守恒</div>
            <div className="param-value num">{ev.toFixed(1)}</div>
          </div>
        </div>
        <p className="page-sub" style={{ margin: "8px 0 0" }}>曝光三角联动保持 EV100；超出器材范围时滑块停在可等效的边界。</p>
        <div className="card-block">
          <div className="exp-slider-row">
            <span className="exp-slider-label">光圈</span>
            <input type="range" min={1} max={22} step="any" value={exposure.aperture} onChange={(e) => setExposure((prev) => changeExposure(prev, "aperture", parseFloat(e.target.value)))} aria-label="光圈" />
            <span className="exp-slider-val">f/{exposure.aperture.toFixed(1)}</span>
          </div>
          <div className="exp-slider-row">
            <span className="exp-slider-label">快门</span>
            <input type="range" min={1 / 8000} max={30} step="any" value={exposure.shutter} onChange={(e) => setExposure((prev) => changeExposure(prev, "shutter", parseFloat(e.target.value)))} aria-label="快门" />
            <span className="exp-slider-val">{formatShutter(exposure.shutter)}</span>
          </div>
          <div className="exp-slider-row">
            <span className="exp-slider-label">ISO</span>
            <input type="range" min={50} max={25600} step="any" value={exposure.iso} onChange={(e) => setExposure((prev) => changeExposure(prev, "iso", parseFloat(e.target.value)))} aria-label="ISO" />
            <span className="exp-slider-val num">{Math.round(exposure.iso)}</span>
          </div>
        </div>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">临场提问</span>
        <DataNotice kind="decision" />
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
            disabled={running || saved.loading}
            aria-label="决策提问"
          />
          <button type="submit" className="btn btn-primary" disabled={running || saved.loading || !input.trim()}>
            {running ? "决策中…" : "决策"}
          </button>
          {running ? <button type="button" className="btn btn-ghost" onClick={() => {
            waiting.stop(); setRunning(false); setNotice(STOP_WAITING_NOTICE);
          }}>停止等待</button> : null}
        </form>
        {notice ? <p className="page-sub" role="status">{notice}</p> : null}
        {error ? <p className="message-error">{error}</p> : null}
        {decisionText ? <p className="trace-empty" role="status">{decisionText}</p> : null}
      </section>

      {card ? <div style={{ marginTop: 20 }}><DecisionCardView card={card} historical={historical} /></div> : null}
      {feedbackReady && saved.active && !saved.loading && !running ? <FeedbackPanel key={`${saved.active.session_id}/${saved.active.result_id}`}
        target={{ session_id: saved.active.session_id, result_id: saved.active.result_id }} onDirty={dirty => { feedbackDirty.current = dirty; }} /> : null}

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
