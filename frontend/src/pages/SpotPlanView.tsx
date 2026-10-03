/** D2 结构化计划呈现；不从模型摘要反向解析行动时间。 */

import type { PlanArchive, SpotPlan } from "../api/spotPlans";

export type DisplayPlan = SpotPlan | (PlanArchive["plan"] & { request: PlanArchive["request"] });

interface Props {
  plan: DisplayPlan;
  frozen?: boolean;
}

const WINDOW_LABELS = [
  ["arrival_window", "到达"],
  ["setup_window", "准备"],
  ["shoot_window", "拍摄"],
  ["departure_window", "离场"],
] as const;

function localWindow(start: string, end: string): string {
  const endDate = end.slice(0, 10) !== start.slice(0, 10) ? `次日 ${end.slice(11, 16)}` : end.slice(11, 16);
  return `${start.slice(11, 16)}–${endDate}`;
}

export function SpotPlanView({ plan, frozen = false }: Props) {
  const selected = plan.status === "ready";
  const coordinateSystem = plan.site
    ? "point" in plan.site ? plan.site.point.coordinate_system : plan.site.coordinate_system
    : "坐标待确认";
  return (
    <section className="d2-plan" aria-labelledby="d2-plan-title">
      <header className="d2-plan-head">
        <div>
          <span className={selected ? "d2-plan-state is-ready" : "d2-plan-state"}>
            {frozen ? "冻结回放" : selected ? "可执行计划" : "仍需补充数据"}
          </span>
          <h2 id="d2-plan-title">{plan.request.subject_type} · {plan.site?.name ?? plan.request.spot?.name ?? "待选机位"}</h2>
          <p className="page-sub">{plan.selected_date ?? plan.request.target_date ?? "日期待定"} · {plan.request.tz_offset} · {coordinateSystem}</p>
          {frozen ? <p className="page-sub">历史快照 · 未重新获取实时数据</p> : null}
        </div>
        {plan.direction ? <p className="d2-direction"><strong>{Math.round(plan.direction.bearing_deg)}°</strong><span>拍摄方位</span></p> : null}
      </header>

      <div className="d2-plan-grid">
        <div>
          <h3>行动时间线</h3>
          <ol className="d2-plan-timeline">
            {WINDOW_LABELS.map(([field, label]) => {
              const window = plan[field];
              return <li key={field} className={window ? "is-known" : ""}>
                <span>{label}</span>
                <strong>{window ? localWindow(window.start, window.end) : "待确认"}</strong>
              </li>;
            })}
          </ol>
          {plan.direction ? <p className="d2-plan-reason">朝向依据：{plan.direction.reason}</p> : null}
        </div>
        <div>
          <h3>器材与参数</h3>
          {plan.focal_length_mm ? <p className="d2-plan-focal">建议焦距 {plan.focal_length_mm}mm</p> : null}
          {plan.gear.length > 0 ? <p className="d2-plan-gear">{plan.gear.join(" · ")}</p> : <p className="page-sub">参数与器材尚待确认。</p>}
          {plan.parameters.length > 0 ? (
            <dl className="d2-plan-parameters">
              {plan.parameters.map((item) => <div key={item.name}><dt>{item.name}</dt><dd><strong>{item.value}</strong><span>{item.reason}</span></dd></div>)}
            </dl>
          ) : null}
        </div>
      </div>

      {plan.risks.length > 0 ? (
        <div className="d2-plan-section">
          <h3>出发前核对</h3>
          <ul>{plan.risks.map((risk) => <li key={risk.code}>{risk.detail}</li>)}</ul>
        </div>
      ) : null}
      <div className="d2-plan-section">
        <h3>依据来源</h3>
        {plan.evidence_refs.length > 0 ? (
          <ul>{plan.evidence_refs.map((item) => <li key={`${item.source}:${item.field}`}>
            {item.source} · {item.field}{item.observed_at ? ` · ${item.observed_at.slice(0, 16)}` : ""}
          </li>)}</ul>
        ) : <p className="page-sub">尚无可用于完整计划的依据。</p>}
      </div>
    </section>
  );
}
