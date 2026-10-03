/** M1 收藏机位编辑器：只通过已校验的 CRUD API 读写机位。 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  createFavoriteSpot,
  deleteFavoriteSpot,
  listFavoriteSpots,
  updateFavoriteSpot,
  type FavoriteSpotInput,
  type FavoriteSpotList,
  type FavoriteSpotRecord,
} from "../api/favoriteSpots";
import { getActiveSpotId, setActiveSpotId } from "./activeSpot";

interface SpotDraft {
  name: string;
  latitude: string;
  longitude: string;
  coordinateSystem: "" | "WGS84" | "GCJ-02" | "BD-09";
  tzOffset: string;
  direction: string;
  subjects: string;
  notes: string;
  source: string;
}

const EMPTY_DRAFT: SpotDraft = {
  name: "",
  latitude: "",
  longitude: "",
  coordinateSystem: "",
  tzOffset: "+08:00",
  direction: "",
  subjects: "",
  notes: "",
  source: "",
};

const STATUS_TEXT: Record<FavoriteSpotRecord["coordinate_state"], string> = {
  ready: "可参与计算",
  missing_coordinates: "无坐标，不可参与计算",
  unknown_system: "坐标系待确认，不可参与计算",
  unsupported_system: "当前不支持该坐标系计算",
};

function canUseForActivity(spot: FavoriteSpotRecord): boolean {
  return spot.coordinate_state === "ready" && Boolean(spot.tz_offset);
}

function activityStatus(spot: FavoriteSpotRecord): string {
  if (spot.coordinate_state !== "ready") return STATUS_TEXT[spot.coordinate_state];
  return spot.tz_offset ? STATUS_TEXT.ready : "时区未填，不可用于本次规划";
}

function draftFromRecord(spot: FavoriteSpotRecord): SpotDraft {
  return {
    name: spot.name,
    latitude: spot.latitude == null ? "" : String(spot.latitude),
    longitude: spot.longitude == null ? "" : String(spot.longitude),
    coordinateSystem: spot.coordinate_system ?? "",
    tzOffset: spot.tz_offset ?? "",
    direction: spot.direction_deg == null ? "" : String(spot.direction_deg),
    subjects: (spot.subject_types ?? []).join("、"),
    notes: spot.notes,
    source: spot.source,
  };
}

function optionalNumber(raw: string, label: string, min: number, max: number): number | null {
  if (!raw.trim()) return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${label}须在 ${min}～${max} 之间`);
  }
  return value;
}

function toInput(draft: SpotDraft): FavoriteSpotInput {
  const name = draft.name.trim();
  const source = draft.source.trim();
  const latitude = optionalNumber(draft.latitude, "纬度", -90, 90);
  const longitude = optionalNumber(draft.longitude, "经度", -180, 180);
  const direction = optionalNumber(draft.direction, "朝向", 0, 359.999999);
  if (!name || !source) throw new Error("请填写机位名称和信息来源");
  if ((latitude === null) !== (longitude === null)) throw new Error("纬度和经度须同时填写");
  if (latitude !== null && !draft.coordinateSystem) throw new Error("请确认坐标系");
  if (latitude !== null && !draft.tzOffset.trim()) throw new Error("请填写拍摄地时区");
  return {
    name,
    latitude,
    longitude,
    coordinate_system: latitude === null ? null : draft.coordinateSystem || null,
    tz_offset: draft.tzOffset.trim() || null,
    direction_deg: direction,
    subject_types: draft.subjects.split(/[,，、]/).map((value) => value.trim()).filter(Boolean),
    notes: draft.notes.trim(),
    source,
  };
}

export function FavoriteSpotEditor() {
  const [data, setData] = useState<FavoriteSpotList | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<SpotDraft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeId, setActiveId] = useState(getActiveSpotId);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const spots = data?.items ?? [];

  async function reload(): Promise<void> {
    setLoading(true);
    setLoadError("");
    try {
      const next = await listFavoriteSpots();
      setData(next);
      const currentId = getActiveSpotId();
      if (currentId && !next.items.some((spot) => spot.id === currentId && canUseForActivity(spot))) {
        setActiveSpotId("");
        setActiveId("");
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "机位列表读取失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  useEffect(() => {
    if (draft) nameInputRef.current?.focus();
  }, [editingId, draft !== null]);

  function startNew(): void {
    setEditingId(null);
    setDraft({ ...EMPTY_DRAFT });
    setNotice("");
  }

  function startEdit(spot: FavoriteSpotRecord): void {
    setEditingId(spot.id);
    setDraft(draftFromRecord(spot));
    setNotice("");
  }

  async function saveSpot(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!draft || busy) return;
    setNotice("");
    let input: FavoriteSpotInput;
    try {
      input = toInput(draft);
      if (spots.some((spot) => spot.name === input.name && spot.id !== editingId)) {
        throw new Error("已有同名机位，请使用不同名称");
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "请检查机位字段");
      return;
    }
    setBusy(true);
    try {
      if (editingId) {
        await updateFavoriteSpot(editingId, input);
      } else {
        await createFavoriteSpot(input);
      }
      setDraft(null);
      setEditingId(null);
      await reload();
      setNotice("机位已保存");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "机位保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeSpot(spot: FavoriteSpotRecord): Promise<void> {
    if (busy || !window.confirm(`删除机位「${spot.name}」？此操作会从当前档案移除。`)) return;
    setBusy(true);
    setNotice("");
    try {
      await deleteFavoriteSpot(spot.id);
      if (spot.id === activeId) {
        setActiveSpotId("");
        setActiveId("");
      }
      if (spot.id === editingId) {
        setDraft(null);
        setEditingId(null);
      }
      await reload();
      setNotice("机位已删除");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "机位删除失败");
    } finally {
      setBusy(false);
    }
  }

  function chooseActive(spot: FavoriteSpotRecord): void {
    const nextId = activeId === spot.id ? "" : spot.id;
    setActiveSpotId(nextId);
    setActiveId(nextId);
    setNotice(nextId ? `已将「${spot.name}」设为本次机位` : "已取消本次机位");
  }

  return (
    <section className="m1-spots" aria-labelledby="m1-spots-title">
      <div className="m1-spots-head">
        <div>
          <h2 id="m1-spots-title">收藏机位</h2>
          <p className="page-sub">机位保存在本地档案，坐标仅在确认坐标系后参与计算。</p>
        </div>
        <div className="chip-row">
          {activeId ? <a className="btn btn-ghost" href="#/d2">前往规划</a> : null}
          <button type="button" className="btn btn-primary" onClick={startNew} disabled={busy}>新增机位</button>
        </div>
      </div>
      {notice ? <p className="m1-spot-notice" role="status">{notice}</p> : null}
      {loadError ? (
        <p className="m1-spot-error" role="alert">{loadError} <button type="button" className="btn btn-ghost" onClick={() => void reload()}>重试</button></p>
      ) : null}
      {data && data.skipped.length > 0 ? (
        <p className="m1-spot-error" role="status">旧档案有 {data.skipped.length} 条机位无法读取，原始档案已保留。</p>
      ) : null}
      <div className={draft ? "m1-spots-layout is-editing" : "m1-spots-layout"}>
        <div className="m1-spots-list" aria-label="已收藏机位">
          {loading && !data ? <p className="trace-empty">正在读取机位…</p> : null}
          {!loading && !loadError && spots.length === 0 ? <p className="trace-empty">暂无收藏机位。</p> : null}
          {spots.map((spot) => (
            <article className="m1-spot-row" key={spot.id}>
              <div className="m1-spot-title">
                <strong>{spot.name}</strong>
                {spot.id === activeId ? <span className="m1-spot-current">本次机位</span> : null}
              </div>
              <p className={canUseForActivity(spot) ? "m1-spot-state is-ready" : "m1-spot-state"}>
                {activityStatus(spot)}
              </p>
              <p className="m1-spot-meta">
                {spot.coordinate_system ?? "坐标系未确认"} · {spot.tz_offset ?? "时区未填"}
                {spot.direction_deg == null ? "" : ` · 朝向 ${spot.direction_deg}°`}
              </p>
              {(spot.subject_types ?? []).length > 0 ? <p className="m1-spot-meta">题材：{spot.subject_types?.join("、")}</p> : null}
              <p className="m1-spot-meta">来源：{spot.source}</p>
              {spot.notes ? <p className="m1-spot-meta">{spot.notes}</p> : null}
              <div className="m1-spot-actions">
                <button type="button" className="btn btn-ghost" onClick={() => chooseActive(spot)} disabled={busy || !canUseForActivity(spot)} title={canUseForActivity(spot) ? "设为本次活动机位" : activityStatus(spot)}>
                  {spot.id === activeId ? "取消本次" : "设为本次"}
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => startEdit(spot)} disabled={busy}>编辑</button>
                <button type="button" className="btn btn-ghost m1-delete" onClick={() => void removeSpot(spot)} disabled={busy}>删除</button>
              </div>
            </article>
          ))}
        </div>

        {draft ? (
          <form className="m1-spot-form" onSubmit={(event) => void saveSpot(event)}>
            <h3>{editingId ? "编辑机位" : "新增机位"}</h3>
            {editingId && spots.find((spot) => spot.id === editingId)?.coordinate_state === "unknown_system" ? (
              <p className="m1-spot-error">旧坐标系待确认；请选择真实坐标系并核对时区后保存。</p>
            ) : null}
            <div className="m1-spot-fields">
              <label><span>名称</span><input ref={nameInputRef} required maxLength={120} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
              <label><span>纬度</span><input type="number" min={-90} max={90} step="any" value={draft.latitude} onChange={(event) => setDraft({ ...draft, latitude: event.target.value })} placeholder="如 31.520000" /></label>
              <label><span>经度</span><input type="number" min={-180} max={180} step="any" value={draft.longitude} onChange={(event) => setDraft({ ...draft, longitude: event.target.value })} placeholder="如 121.950000" /></label>
              <label><span>坐标系</span><select value={draft.coordinateSystem} onChange={(event) => setDraft({ ...draft, coordinateSystem: event.target.value as SpotDraft["coordinateSystem"] })}>
                <option value="">未选择</option><option value="WGS84">WGS84</option><option value="GCJ-02">GCJ-02</option><option value="BD-09">BD-09</option>
              </select></label>
              <label><span>拍摄地时区</span><input value={draft.tzOffset} onChange={(event) => setDraft({ ...draft, tzOffset: event.target.value })} placeholder="+08:00" pattern="[+-][0-9]{2}:[0-9]{2}" /></label>
              <label><span>朝向（度）</span><input type="number" min={0} max={359.999999} step="any" value={draft.direction} onChange={(event) => setDraft({ ...draft, direction: event.target.value })} placeholder="如 270" /></label>
              <label className="m1-field-wide"><span>适合题材</span><input value={draft.subjects} onChange={(event) => setDraft({ ...draft, subjects: event.target.value })} placeholder="如 日落、银河" /></label>
              <label className="m1-field-wide"><span>来源</span><input required maxLength={120} value={draft.source} onChange={(event) => setDraft({ ...draft, source: event.target.value })} placeholder="如现场测得、本人标记" /></label>
              <label className="m1-field-wide"><span>备注</span><textarea maxLength={1000} value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></label>
            </div>
            <div className="m1-spot-actions">
              <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "保存中…" : "保存机位"}</button>
              <button type="button" className="btn btn-ghost" onClick={() => setDraft(null)} disabled={busy}>取消</button>
            </div>
          </form>
        ) : null}
      </div>
    </section>
  );
}
