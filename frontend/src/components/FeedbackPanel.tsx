/** 与原结果分离的本地反馈及本人经验面板；等待结束不承诺撤销写入。 */
import { useEffect, useRef, useState } from "react";
import { FeedbackRequestError, requestFeedback, type ExperienceKind, type FeedbackView, type RawFeedback } from "../api/feedback";
import type { ResultPointer } from "../api/savedResults";
import { useRequestScope } from "../api/useRequestScope";

const EMPTY: RawFeedback = {
  departure_status: "unknown", observation_status: "unknown", observation_result: null,
  observation_scope: null, departure_reason: null, observation_reason: null, result_reason: null, note: null,
};
const OBSERVATION = { place_matches_plan: "unknown", window_covers_plan: "unknown", description: "" } as const;
const KIND_LABEL = { planning_preference: "安排偏好", observation_note: "观察笔记" };

export function FeedbackPanel({ target, onDirty }: { target: ResultPointer; onDirty?: (dirty: boolean) => void }) {
  const scope = useRequestScope();
  const [view, setView] = useState<FeedbackView | null>(null);
  const [raw, setRaw] = useState<RawFeedback>({ ...EMPTY });
  const [kind, setKind] = useState<ExperienceKind>("planning_preference");
  const [content, setContent] = useState("");
  const [feedbackDirty, setFeedbackDirty] = useState(false);
  const [experienceDirty, setExperienceDirty] = useState(false);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("读取本地反馈…");
  const [uncertain, setUncertain] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const lastAttempt = useRef<string | null>(null);
  const dirty = feedbackDirty || experienceDirty;
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const observed = raw.observation_status === "observed";
  const experience = view?.record?.experience;
  const editable = view !== null && view.source_state === "unchanged" && !busy && !uncertain && !confirmOpen;
  const active = view?.state === "active";

  function accept(next: FeedbackView) {
    setView(next); setRaw(next.record?.feedback ?? { ...EMPTY });
    setKind(next.record?.experience?.kind ?? "planning_preference");
    setContent(next.record?.experience?.content ?? "");
    setFeedbackDirty(false); setExperienceDirty(false); setUncertain(false); setConfirmOpen(false);
  }

  useEffect(() => {
    const controller = scope.begin();
    void requestFeedback(target, controller.signal).then(next => {
      if (!scope.isCurrent(controller)) return;
      accept(next); setNotice(next.state === "none" ? "尚无反馈；未提供不表示未出门。" : "已恢复本地记录；原卡与有效期保留。");
    }).catch(error => { if (scope.isCurrent(controller)) setNotice(error.message); })
      .finally(() => scope.finish(controller, () => setBusy(false)));
    return () => scope.stop();
  }, []);

  useEffect(() => { onDirty?.(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => { if (dirtyRef.current) { event.preventDefault(); event.returnValue = ""; } };
    const navigation = (event: MouseEvent) => {
      if (!dirtyRef.current || !(event.target instanceof Element)) return;
      if (event.target.closest('a[href^="#"]') && !window.confirm("本地草稿尚未保存，离开将丢弃草稿。继续离开？")) {
        event.preventDefault(); event.stopPropagation();
      }
    };
    const beforeNavigation = (event: Event) => {
      if (dirtyRef.current && !window.confirm("本地草稿尚未保存，离开将丢弃草稿。继续离开？")) event.preventDefault();
    };
    window.addEventListener("beforeunload", unload); document.addEventListener("click", navigation, true);
    window.addEventListener("lighttrail:before-navigation", beforeNavigation);
    return () => { window.removeEventListener("beforeunload", unload); document.removeEventListener("click", navigation, true); window.removeEventListener("lighttrail:before-navigation", beforeNavigation); onDirty?.(false); };
  }, []);

  function change<K extends keyof RawFeedback>(key: K, value: RawFeedback[K]) {
    setRaw(previous => ({ ...previous, [key]: value })); setFeedbackDirty(true);
  }

  async function load() {
    if (dirty && !window.confirm("只读刷新将替换未保存草稿。继续核对？")) return;
    const controller = scope.begin(); setBusy(true);
    try {
      const next = await requestFeedback(target, controller.signal);
      if (!scope.isCurrent(controller)) return;
      const acknowledged = lastAttempt.current !== null && next.last_mutation_id === lastAttempt.current;
      accept(next);
      setNotice(acknowledged ? "已只读核对：最近操作已保存。" : "已读取当前保存状态；不能确认旧操作已取消或未曾保存。");
    } catch (error) { if (scope.isCurrent(controller)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { scope.finish(controller, () => setBusy(false)); }
  }

  async function mutate(method: "PUT" | "POST", suffix: string, fields: Record<string, unknown>, message: string) {
    if (!view || busy || uncertain) return;
    const controller = scope.begin(); setBusy(true); setConfirmOpen(false);
    const mutationId = crypto.randomUUID(); lastAttempt.current = mutationId;
    try {
      const next = await requestFeedback(target, controller.signal, method, suffix, {
        expected_revision: view.revision, mutation_id: mutationId, ...fields,
      });
      if (!scope.isCurrent(controller)) return;
      accept(next); setNotice(message);
    } catch (error) {
      if (!scope.isCurrent(controller)) return;
      setNotice(error instanceof Error ? error.message : String(error));
      // 冲突也要求核对，不能继续用已知过时版本写入。
      setUncertain(error instanceof FeedbackRequestError && (error.status === null || error.status === 409));
    } finally { scope.finish(controller, () => setBusy(false)); }
  }

  function saveFeedback() {
    if (view?.record?.experience && view.record.experience.status !== "withdrawn"
      && !window.confirm("修改反馈将使经验确认失效，需重新核对并保存候选。继续保存？")) return;
    if (experienceDirty && !window.confirm("本次只保存原始反馈，未保存的经验草稿将被当前记录替换。继续？")) return;
    void mutate("PUT", "", { feedback: raw, photo_refs: [] }, "反馈已保存；原卡与有效期保留。");
  }

  function withdrawFeedback() {
    if (window.confirm("撤回将清空原始反馈及关联经验正文，保留最小标识和版本；原卡保留。继续撤回？"))
      void mutate("POST", "/withdraw", {}, "反馈已撤回，关联经验已清空；原卡保留。");
  }

  const field = (label: string, key: "departure_reason" | "observation_reason" | "result_reason" | "note", max: number) =>
    <label>{label}<textarea aria-label={label} maxLength={max} value={raw[key] ?? ""} disabled={!editable}
      onChange={event => change(key, event.target.value || null)} /></label>;

  return <section className="card feedback-panel" data-testid="feedback-panel" style={{ marginTop: 20 }}>
    <span className="card-kicker">本次结果反馈 · 本地记录</span>
    <p className="page-sub">原卡与有效期保留。出门与观察分别记录；未观察不能等同于没出现。首版不上传照片。</p>
    <p role="status" data-testid="feedback-status">{notice}</p>
    {view?.source_state !== undefined && view.source_state !== "unchanged" ? <p className="message-error">原结果{view.source_state === "missing" ? "已缺失" : "已改变"}，仅可回看或撤回；旧经验确认不可复用。</p> : null}
    {view?.state === "withdrawn" ? <p>反馈已撤回；重新填写不会恢复旧经验。</p> : null}
    {busy ? <button type="button" className="btn btn-ghost" onClick={() => {
      scope.stop(); setBusy(false); setUncertain(true); setNotice("已停止等待；不能确认服务端写入已取消，请只读核对保存状态。");
    }}>停止反馈等待</button> : null}
    <button type="button" className="btn btn-ghost" disabled={busy || confirmOpen} onClick={() => void load()}>只读核对保存状态</button>
    <div className="feedback-grid">
      <label>是否出门<select aria-label="是否出门" value={raw.departure_status} disabled={!editable}
        onChange={event => change("departure_status", event.target.value as RawFeedback["departure_status"])}>
        <option value="unknown">未提供</option><option value="went">已出门</option><option value="not_went">未出门</option>
      </select></label>
      <label>是否独立观察<select aria-label="是否独立观察" value={raw.observation_status} disabled={!editable}
        onChange={event => {
          const status = event.target.value as RawFeedback["observation_status"];
          if (status !== "observed" && (raw.observation_result || raw.observation_scope || raw.result_reason)
            && !window.confirm("切换为未观察或未提供将清除旧观察结果、范围及结果原因。继续？")) return;
          setRaw(previous => ({ ...previous, observation_status: status,
            observation_result: status === "observed" ? previous.observation_result : null,
            observation_scope: status === "observed" ? previous.observation_scope ?? { ...OBSERVATION } : null,
            result_reason: status === "observed" ? previous.result_reason : null })); setFeedbackDirty(true);
        }}><option value="unknown">未提供</option><option value="observed">已独立观察</option><option value="not_observed">未观察</option>
      </select></label>
      {observed ? <>
        <label>观察结果<select aria-label="观察结果" disabled={!editable} value={raw.observation_result ?? ""}
          onChange={event => change("observation_result", (event.target.value || null) as RawFeedback["observation_result"])}>
          <option value="">请选择</option><option value="seen">看到</option><option value="not_seen">在所述观察范围内未看到</option><option value="uncertain">不确定</option>
        </select></label>
        {(["place_matches_plan", "window_covers_plan"] as const).map(key => <label key={key}>
          {key === "place_matches_plan" ? "地点与计划相同" : "覆盖完整计划窗口"}
          <select aria-label={key === "place_matches_plan" ? "地点与计划相同" : "覆盖完整计划窗口"}
            disabled={!editable} value={raw.observation_scope?.[key] ?? "unknown"}
            onChange={event => change("observation_scope", { ...(raw.observation_scope ?? OBSERVATION), [key]: event.target.value as "yes" | "no" | "unknown" })}>
            <option value="unknown">未确认</option><option value="yes">是</option><option value="no">否</option>
          </select></label>)}
        <label>观察范围描述<textarea aria-label="观察范围描述" maxLength={300} value={raw.observation_scope?.description ?? ""} disabled={!editable}
          onChange={event => change("observation_scope", { ...(raw.observation_scope ?? OBSERVATION), description: event.target.value })} /></label>
        {field("结果判断原因（可选）", "result_reason", 500)}
      </> : <p className="page-sub">本次未提供独立观察，结果为 null，现场效用无法判读。</p>}
      {field("出门选择原因（可选）", "departure_reason", 500)}
      {field("观察状态原因（可选）", "observation_reason", 500)}
      {field("原始补充（可选）", "note", 1000)}
    </div>
    {observed && (raw.observation_scope?.place_matches_plan !== "yes" || raw.observation_scope?.window_covers_plan !== "yes") ?
      <p>不能据此判读原机位完整窗口；未看到不表示全局没出现。</p> : null}
    {raw.departure_status === "unknown" || raw.observation_status === "unknown" ? <p className="page-sub">反馈信息未完整。</p> : null}
    <div className="feedback-actions">
      <button type="button" className="btn btn-primary" disabled={!editable || (observed && (!raw.observation_result || !raw.observation_scope?.description.trim()))}
        onClick={saveFeedback}>保存反馈</button>
      <button type="button" className="btn btn-ghost" disabled={!active || busy || uncertain || confirmOpen} onClick={withdrawFeedback}>撤回反馈</button>
    </div>
    {active ? <div className="feedback-experience">
      <h3>本人经验 · 仅本地回看</h3>
      <p className="page-sub">手写候选并单独确认。尚未纳入 Agent 记忆；单次本人记录，未经独立校准。</p>
      {!observed ? <p>本次无现场观察；安排偏好不代表天象事实。</p> : null}
      {experience ? <p data-testid="experience-status">{experience.status === "confirmed" && view?.experience_effective ? "已确认本地经验" : experience.status === "needs_review" ? "待复核：请核对后重新保存候选，再单独确认" : experience.status === "withdrawn" ? "经验已撤回，正文已清空" : experience.status === "confirmed" ? "原来源已失效，旧确认不可复用" : "候选未确认"}</p> : <p>尚无经验候选；保存反馈不会自动创建经验。</p>}
      <label>经验类型<select aria-label="经验类型" value={kind} disabled={!editable || feedbackDirty}
        onChange={event => { setKind(event.target.value as ExperienceKind); setExperienceDirty(true); }}>
        <option value="planning_preference">安排偏好</option><option value="observation_note">观察笔记</option>
      </select></label>
      <label>本人经验内容<textarea aria-label="本人经验内容" maxLength={500} value={content} disabled={!editable || feedbackDirty}
        onChange={event => { setContent(event.target.value); setExperienceDirty(true); }} /></label>
      <div className="feedback-actions">
        <button type="button" className="btn btn-ghost" disabled={!editable || feedbackDirty || !content.trim()}
          onClick={() => void mutate("PUT", "/experience", { kind, content }, "候选已保存，尚未确认；不写入 Agent 记忆。")}>保存经验候选</button>
        <button type="button" className="btn btn-primary" disabled={!editable || dirty || experience?.status !== "draft" || (experience.kind === "observation_note" && !observed)}
          onClick={() => setConfirmOpen(true)}>确认本地经验</button>
        <button type="button" className="btn btn-ghost" disabled={busy || uncertain || dirty || confirmOpen || !experience || experience.status === "withdrawn"}
          onClick={() => { if (window.confirm("撤回本条经验并清空正文与确认时间，原始反馈保留。继续？")) void mutate("POST", "/experience/withdraw", {}, "经验已撤回；原始反馈保留。"); }}>撤回经验</button>
      </div>
      {confirmOpen && experience ? <div role="dialog" aria-modal="true" aria-label="确认本人本地经验" className="feedback-confirm">
        <p>核对当前反馈版本 {view.record?.feedback_revision}：出门 {raw.departure_status}，观察 {raw.observation_status}，结果 {raw.observation_result ?? "null"}。</p>
        <p>{KIND_LABEL[experience.kind]}：{experience.content}</p>
        <p>仅保存本地经验，尚未纳入 Agent 记忆；单次本人记录，未经独立校准。</p>
        <button type="button" className="btn btn-primary" onClick={() => void mutate("POST", "/experience/confirm", {
          experience_id: experience.experience_id, source_feedback_revision: experience.source_feedback_revision,
          confirmed_by_user: true,
        }, "本人已确认本地经验；尚未纳入 Agent 记忆。")}>本人确认保存</button>
        <button type="button" className="btn btn-ghost" onClick={() => setConfirmOpen(false)}>取消确认</button>
      </div> : null}
    </div> : null}
    {dirty ? <p className="page-sub">有尚未保存的本地草稿；关闭或切换前请保存或明确丢弃。</p> : null}
  </section>;
}
