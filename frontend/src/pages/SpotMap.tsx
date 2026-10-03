/** 收藏机位地图：用户主动加载，且仅在高德确认转换后展示 WGS84 标记。 */

import { useEffect, useRef, useState } from "react";
import type { FavoriteSpotRecord } from "../api/favoriteSpots";
import type { components } from "../api/generated";

interface MapInstance {
  destroy(): void;
  setCenter(position: number[]): void;
  setFitView(overlays: MarkerInstance[]): void;
}

interface MarkerInstance {
  on(event: "click", callback: () => void): void;
}

interface MapApi {
  Map: new (element: HTMLElement, options: { center: number[]; zoom: number }) => MapInstance;
  Marker: new (options: { map: MapInstance; position: number[]; title: string }) => MarkerInstance;
}

declare global {
  interface Window {
    AMap?: MapApi;
    _AMapSecurityConfig?: { serviceHost: string };
  }
}

interface Props {
  spots: FavoriteSpotRecord[];
  selectedId: string;
  onSelect: (id: string) => void;
}

interface MapConfig {
  available: boolean;
  js_key: string | null;
}

type MapConvertPayload = components["schemas"]["MapConvertPayload"];
type MapConvertResult = components["schemas"]["MapConvertResult"];

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const MAX_CONVERSION_BATCH = 40;

function mappable(spot: FavoriteSpotRecord): boolean {
  return spot.coordinate_state === "ready" && spot.coordinate_system === "WGS84"
    && typeof spot.longitude === "number" && typeof spot.latitude === "number";
}

async function loadSdk(): Promise<MapApi> {
  if (window.AMap) return window.AMap;
  const response = await fetch(BASE + "/api/map/config", { cache: "no-store" });
  if (!response.ok) throw new Error("地图配置读取失败");
  const config = (await response.json()) as MapConfig;
  if (!config.available || !config.js_key) throw new Error("地图服务尚未配置");
  window._AMapSecurityConfig = { serviceHost: `${window.location.origin}/_AMapService` };
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.js_key ?? "")}`;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("高德地图脚本无法加载"));
    document.head.appendChild(script);
  });
  if (!window.AMap) throw new Error("高德地图初始化失败");
  return window.AMap;
}

async function convertBatch(batch: FavoriteSpotRecord[], signal: AbortSignal): Promise<number[][]> {
  const payload: MapConvertPayload = { spot_ids: batch.map((spot) => spot.id) };
  const response = await fetch(BASE + "/api/map/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error("高德坐标转换失败或无权限");
  const converted = (await response.json()) as MapConvertResult;
  if (converted.positions.length !== batch.length) throw new Error("坐标转换结果数量不一致");
  return converted.positions.map((position) => [position.longitude, position.latitude]);
}

export function SpotMap({ spots, selectedId, onSelect }: Props) {
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [reason, setReason] = useState("");
  const [markerCount, setMarkerCount] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const positionsRef = useRef<Map<string, number[]>>(new Map());
  const eligible = spots.filter(mappable);
  const selectedSpot = eligible.find((spot) => spot.id === selectedId);

  useEffect(() => {
    if (!enabled || !window.AMap || !containerRef.current) return;
    const api = window.AMap;
    let map: MapInstance;
    try {
      map = new api.Map(containerRef.current, { center: [104.2, 35.9], zoom: 4 });
    } catch {
      setEnabled(false);
      setStatus("error");
      setReason("地图初始化失败，机位列表仍可使用。");
      return;
    }
    mapRef.current = map;
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    const markers: MarkerInstance[] = [];
    const positions = new Map<string, number[]>();
    positionsRef.current = positions;
    let completed = 0;
    let failed = 0;
    setReason("正在转换机位坐标…");
    const batches: FavoriteSpotRecord[][] = [];
    for (let index = 0; index < eligible.length; index += MAX_CONVERSION_BATCH) {
      batches.push(eligible.slice(index, index + MAX_CONVERSION_BATCH));
    }
    for (const batch of batches) {
      void convertBatch(batch, controller.signal).then((converted) => {
        if (active) {
          batch.forEach((spot, index) => {
            const position = converted[index];
            positions.set(spot.id, position);
            const marker = new api.Marker({ map, position, title: spot.name });
            marker.on("click", () => onSelectRef.current(spot.id));
            markers.push(marker);
          });
        }
      }).catch(() => {
        failed += batch.length;
      }).finally(() => {
        if (!active) return;
        completed += 1;
        if (completed === batches.length) {
          window.clearTimeout(timeout);
          setMarkerCount(markers.length);
          if (markers.length > 1) map.setFitView(markers);
          else if (markers.length === 1) map.setCenter(positions.get(selectedId) ?? positions.values().next().value!);
          setReason(failed ? `${failed} 个机位坐标转换失败，已从地图隐藏；列表仍可使用。` : "");
        }
      });
    }
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
      positionsRef.current = new Map();
      mapRef.current = null;
      map.destroy();
    };
  }, [enabled, spots]);

  useEffect(() => {
    const position = positionsRef.current.get(selectedId);
    if (position) mapRef.current?.setCenter(position);
  }, [selectedId, markerCount]);

  async function enable(): Promise<void> {
    setStatus("loading");
    setReason("");
    try {
      await loadSdk();
      setEnabled(true);
      setStatus("ready");
    } catch (caught) {
      setStatus("error");
      setReason(caught instanceof Error ? caught.message : "地图服务不可用");
    }
  }

  return (
    <div className="d2-map-pane">
      <div className="d2-map-head">
        <h3>机位地图</h3>
        {enabled ? <button type="button" className="btn btn-secondary" onClick={() => { setEnabled(false); setStatus("idle"); setMarkerCount(0); }}>关闭地图</button> : null}
      </div>
      {!enabled ? <div className="d2-map-consent">
        <p>加载地图后，高德会收到地图访问请求及已确认机位的坐标；未知坐标系和无坐标机位不会发送。</p>
        <button type="button" className="btn btn-secondary" disabled={status === "loading" || eligible.length === 0} onClick={() => void enable()}>
          {status === "loading" ? "加载中…" : "加载地图"}
        </button>
      </div> : null}
      {enabled ? <div className="d2-map-canvas" ref={containerRef} role="img" aria-label={`机位地图，显示 ${markerCount} 个标记`} /> : null}
      {reason ? <p className="d2-map-reason" role="status">{reason}</p> : null}
      {enabled && selectedSpot && positionsRef.current.has(selectedSpot.id) ? <div className="d2-map-details">
        <strong>{selectedSpot.name}</strong>
        <span>朝向：{selectedSpot.direction_deg == null ? "未记录" : `${selectedSpot.direction_deg}°`}</span>
        <span>题材：{selectedSpot.subject_types?.length ? selectedSpot.subject_types.join("、") : "未记录"}</span>
        <span>来源：{selectedSpot.source}</span>
      </div> : null}
      {enabled ? <p className="d2-map-attribution">高德地图 · 已转换的 WGS84 机位点位</p> : null}
    </div>
  );
}
