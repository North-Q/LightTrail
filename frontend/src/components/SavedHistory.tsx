/** 历史只读选择与显式重新查询，复用服务器实际结果。 */
import type { SavedResult, ResultPointer } from "../api/savedResults";

export function SavedHistory({ results, pointer, status, disabled, onRestore, onRequery }: {
  results: SavedResult[]; pointer: ResultPointer; status: string; disabled: boolean;
  onRestore: (pointer: ResultPointer) => void; onRequery?: () => void;
}) {
  return <section className="card saved-history" style={{ marginBottom: 16 }} data-testid="saved-history">
    <span className="card-kicker">已保存结果 · 历史回看</span>
    <select aria-label="历史结果版本" disabled={disabled} value={`${pointer.session_id}/${pointer.result_id}`}
      onChange={event => { const [session_id, result_id] = event.target.value.split("/"); if (session_id && result_id) onRestore({ session_id, result_id }); }} style={{ maxWidth: "100%" }}>
      <option value="/">选择已保存结果</option>
      {results.map(result => <option key={result.result_id + result.session_id} value={`${result.session_id}/${result.result_id}`}>
        v{result.version} · {result.created_at || "时间未记录"} · {String(result.request?.request ?? result.request?.message ?? result.request?.subject_type ?? "历史计划").slice(0, 35)}
      </option>)}
    </select>
    {pointer.result_id && onRequery ? <button type="button" className="btn btn-ghost" disabled={disabled} onClick={onRequery}>重新查询原请求（新版本）</button> : null}
    <p className="page-sub" role="status">{status || "回看不调用模型、天气或地图；重新查询生成新版本，保留旧结果。"}</p>
  </section>;
}
