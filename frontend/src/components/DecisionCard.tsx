/** 决策卡片：资格、日期、数据获取时间、实际来源与缺失原因。 */

import type { DecisionCard } from "../api/events";
import { decisionState } from "../pages/decisionState";

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function confidenceLevel(level: string): string {
  return CONFIDENCE_LABEL[level] ?? level;
}

export function DecisionCardView({ card, historical = false }: { card: DecisionCard; historical?: boolean }) {
  const state = decisionState(card);
  const expires = Date.parse(card.valid_until ?? "");
  const shootStart = Date.parse(card.window_start ?? "");
  const needsUpdateBeforeShoot = Number.isFinite(expires) && Number.isFinite(shootStart) && expires <= shootStart;
  return (
    <article className="decision-card" data-testid="decision-card">
      <header className="card-head">
        <span className="card-kicker">拍摄方案 · 决策卡片</span>
        <span className={`badge confidence-${card.confidence || "low"}`}>
          {state === "needs_data" ? "数据不足 · 暂缓" : state === "stale" ? "历史 / 待更新" : `证据等级 ${confidenceLevel(card.confidence || "low")}`}
        </span>
      </header>

      <p className="card-conclusion">{card.conclusion}</p>
      <p className="page-sub">目标日期：{card.target_date || "未记录"} · 时区：{card.tz_offset || "未记录"}</p>
      <p className="page-sub">数据获取时间：{card.updated_at || "未取得可核对的预报"}</p>
      <p className="page-sub">评估时间：{card.evaluated_at || "未记录"}</p>
      <p className="page-sub">有效截止：{card.valid_until || "未记录"}</p>
      {needsUpdateBeforeShoot ? <p className="card-degraded" data-testid="departure-update-notice">本卡有效期早于拍摄窗口，出发前需手动更新天气与拍摄建议；回看不续期，不会自动查询。</p> : null}
      {historical || state === "stale" ? <p className="card-degraded">历史结果，仅供回看；请重新查询目标日期的数据。</p> : null}
      {(card.missing_data ?? []).length > 0 ? <div className="card-block">
        <h4>数据不足的原因</h4>
        <ul>{card.missing_data?.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
        <p>补齐或更新数据后，使用同一地点和日期重新查询。</p>
      </div> : null}

      {card.time_window ? (
        <div className="card-row">
          <span className="card-label">时间窗口</span>
          <span className="card-value">{card.time_window}</span>
        </div>
      ) : null}

      {card.locations && card.locations.length > 0 ? (
        <div className="card-block">
          <h4>推荐机位</h4>
          <ul className="chip-list">
            {card.locations.map((location, index) => (
              <li className="chip" key={`${location.name}-${index}`}>
                <strong>{location.name}</strong>
                {location.reason ? <span className="chip-note">{location.reason}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {card.params && card.params.length > 0 ? (
        <div className="card-block">
          <h4>参数建议</h4>
          <table className="param-table">
            <tbody>
              {card.params.map((param, index) => (
                <tr key={`${param.name}-${index}`}>
                  <td className="param-name">{param.name}</td>
                  <td className="param-value">{param.value}</td>
                  <td className="param-reason">{param.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {card.evidence && card.evidence.length > 0 ? (
        <div className="card-block">
          <h4>依据与来源</h4>
          <ul className="evidence-list">
            {card.evidence.map((source, index) => (
              <li key={`${source.tool}-${index}`}>
                <span className={`badge confidence-${source.confidence || "low"}`}>
                  {confidenceLevel(source.confidence || "low")}
                </span>
                <code className="source-tool">{source.tool || "—"}</code>
                <span className="source-field">{source.field || ""}</span>
                {source.note ? <span className="source-note">{source.note}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {card.alternatives && card.alternatives.length > 0 ? (
        <div className="card-block">
          <h4>备选方案</h4>
          <ul className="alt-list">
            {card.alternatives.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {card.degraded ? <p className="card-degraded">⚠ {card.degraded}</p> : null}
    </article>
  );
}
