/** 决策卡片组件（E7-5）：结论/依据/置信度区间条/来源标签/机位/参数表。 */

import type { DecisionCard } from "../api/events";

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function confidenceLevel(level: string): string {
  return CONFIDENCE_LABEL[level] ?? level;
}

export function DecisionCardView({ card }: { card: DecisionCard }) {
  return (
    <article className="decision-card" data-testid="decision-card">
      <header className="card-head">
        <span className="card-kicker">拍摄方案 · 决策卡片</span>
        <span className={`badge confidence-${card.confidence || "low"}`}>
          置信度 {confidenceLevel(card.confidence || "low")}
        </span>
      </header>

      <p className="card-conclusion">{card.conclusion}</p>

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
