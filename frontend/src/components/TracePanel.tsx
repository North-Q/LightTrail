/** 轨迹面板组件（E7-5）：右栏实时渲染 SSE 事件流（trace 即 UI 落地点）。 */

import type { TraceItem } from "../api/events";

const KIND_ICON: Record<TraceItem["kind"], string> = {
  step: "→",
  tool_call: "⚙",
  tool_result: "✓",
};

export function TracePanel({ items }: { items: TraceItem[] }) {
  return (
    <aside className="trace-panel" data-testid="trace-panel">
      <header className="trace-head">
        <span>实时轨迹</span>
        <span className="trace-count">{items.length}</span>
      </header>
      {items.length === 0 ? (
        <p className="trace-empty">运行决策后，这里会实时滚动「正在查天气 → 评分 → 匹配机位…」</p>
      ) : (
        <ol className="trace-list">
          {items.map((item, index) => (
            <li className={`trace-item trace-${item.kind}`} key={index}>
              <span className="trace-icon">{KIND_ICON[item.kind]}</span>
              <span className="trace-name">{item.name}</span>
              {item.detail ? <span className="trace-detail">{item.detail}</span> : null}
              {item.tag ? <span className="trace-tag">{item.tag}</span> : null}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
