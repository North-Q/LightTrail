/** 规划页：本轮选择驱动结构化 SpotPlan，赶场调度保持独立。 */

import { useEffect, useRef, useState } from "react";
import { postJson } from "../api/client";
import type { components } from "../api/generated";
import { listFavoriteSpots, type FavoriteSpotRecord } from "../api/favoriteSpots";
import {
  createSpotPlan, exportPlanArchive, replayPlanArchive,
  type PlanArchive, type PlanningRequest, type SpotPlan,
} from "../api/spotPlans";
import { getActiveSpotId, setActiveSpotId } from "./activeSpot";
import { parsePlanUrl, PLAN_SUBJECTS, replacePlanUrl } from "./planUrl";
import { SpotMap } from "./SpotMap";
import { SpotPlanView, type DisplayPlan } from "./SpotPlanView";

type ChasePlanResult = components["schemas"]["ChasePlanPayload"];

interface ChaseWindow {
  start: string;
  end: string;
}

function defaultChaseWindow(index: number): ChaseWindow {
  const start = new Date();
  start.setHours(start.getHours() + 1 + index * 2, 0, 0, 0);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  return { start: start.toISOString().slice(0, 16), end: end.toISOString().slice(0, 16) };
}

function spotStatus(spot: FavoriteSpotRecord): string {
  if (spot.coordinate_state === "missing_coordinates") return "无坐标，不能参与计算";
  if (spot.coordinate_state === "unknown_system") return "坐标系待确认，不能参与计算";
  if (spot.coordinate_state === "unsupported_system") return "当前坐标系不能参与 WGS84 计算";
  if (!spot.tz_offset) return "时区未填，不能参与本次规划";
  return "可参与规划";
}

export function D2Page() {
  const initialUrl = useRef(parsePlanUrl(window.location.hash)).current;
  const importRef = useRef<HTMLInputElement>(null);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState(initialUrl.error);
  const [spots, setSpots] = useState<FavoriteSpotRecord[]>([]);
  const [planningSpots, setPlanningSpots] = useState<FavoriteSpotRecord[]>([]);
  const [planSpotId, setPlanSpotId] = useState(initialUrl.state.spotId);
  const [planDate, setPlanDate] = useState(initialUrl.state.date);
  const [planSubject, setPlanSubject] = useState(initialUrl.state.subject);
  const [routeSpots, setRouteSpots] = useState<string[]>([]);
  const [plan, setPlan] = useState<SpotPlan | null>(null);
  const [archive, setArchive] = useState<PlanArchive | null>(null);
  const [transferring, setTransferring] = useState(false);
  const [selectedSpots, setSelectedSpots] = useState<string[]>([]);
  const [transportMode, setTransportMode] = useState("driving");
  const [transitCity, setTransitCity] = useState("");
  const [transitionBuffer, setTransitionBuffer] = useState(18);
  const [siteWindows, setSiteWindows] = useState<Record<string, ChaseWindow>>({});
  const [chaseRunning, setChaseRunning] = useState(false);
  const [chaseResult, setChaseResult] = useState<ChasePlanResult | null>(null);
  const [chaseNotice, setChaseNotice] = useState("");
  useEffect(() => {
    void (async () => {
      try {
        const saved = await listFavoriteSpots();
        const validPlanningSpots = saved.items.filter((spot) =>
          spot.coordinate_state === "ready" && spot.coordinate_system === "WGS84" && Boolean(spot.tz_offset),
        );
        const routeNames = Array.from(new Set(validPlanningSpots.map((spot) => spot.name)));
        setSpots(saved.items);
        setPlanningSpots(validPlanningSpots);
        const activeId = initialUrl.error ? "" : initialUrl.state.spotId || getActiveSpotId();
        const activeSpot = validPlanningSpots.find((spot) => spot.id === activeId);
        setPlanSpotId(activeSpot?.id ?? "");
        if (activeId && !activeSpot) {
          if (initialUrl.state.spotId) setNotice("链接中的机位不属于当前用户或不可参与规划，请重新选择。");
          else setActiveSpotId("");
        }
        setRouteSpots(routeNames);
        setSelectedSpots(routeNames.slice(0, 2));
        setSiteWindows(Object.fromEntries(routeNames.slice(0, 2).map((name, index) => [name, defaultChaseWindow(index)])));
      } catch {
        setSpots([]);
        setPlanningSpots([]);
        setNotice("机位列表读取失败，请检查后端连接。");
      }
    })();
  }, []);

  async function runPlan(): Promise<void> {
    const spot = planningSpots.find((item) => item.id === planSpotId);
    if (!planDate || !planSubject.trim() || !spot || !spot.tz_offset) {
      setNotice("请先选择日期、已确认 WGS84 坐标和时区的机位及拍摄题材。");
      return;
    }
    const input: PlanningRequest = {
      subject_type: planSubject,
      target_date: planDate,
      tz_offset: spot.tz_offset,
      spot: { name: spot.name, site_id: spot.id },
    };
    setRunning(true);
    setPlan(null);
    setArchive(null);
    replacePlanUrl({ subject: planSubject, date: planDate, spotId: planSpotId });
    setNotice("正在计算本次机位的天文窗口…");
    try {
      const result = await createSpotPlan(input);
      setPlan(result);
      setNotice(result.status === "ready" ? "结构化计划已生成。" : "部分关键事实尚未核实，已列出缺口。");
    } catch (caught) {
      setNotice(`计划生成失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setRunning(false);
    }
  }

  async function downloadPlan(): Promise<void> {
    if (!plan) return;
    setTransferring(true);
    try {
      const payload = await exportPlanArchive(plan);
      const content = `${JSON.stringify(payload, null, 2)}\n`;
      const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `lighttrail-plan-${plan.selected_date ?? "draft"}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice("计划 JSON 已导出，坐标字段已移除；分享前请检查自填名称和来源。");
    } catch (caught) {
      setNotice(`计划导出失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setTransferring(false);
    }
  }

  async function importPlan(file: File | undefined): Promise<void> {
    if (!file) return;
    if (file.size > 1024 * 1024) {
      setNotice("计划文件超过 1 MB，无法导入。");
      return;
    }
    setTransferring(true);
    try {
      const payload: unknown = JSON.parse(await file.text());
      const validated = await replayPlanArchive(payload);
      setPlan(null);
      setArchive(validated);
      setNotice("已载入冻结计划，未重新获取实时数据。");
    } catch {
      setNotice("计划文件无效或版本不受支持，请检查 JSON 内容。");
    } finally {
      if (importRef.current) importRef.current.value = "";
      setTransferring(false);
    }
  }

  const replayPlan: DisplayPlan | null = archive
    ? { ...archive.plan, request: archive.request }
    : null;

  function toggleSpot(name: string): void {
    setSelectedSpots((current) => {
      if (current.includes(name)) return current.filter((item) => item !== name);
      if (current.length >= 4) return current;
      setSiteWindows((windows) => ({ ...windows, [name]: defaultChaseWindow(current.length) }));
      return [...current, name];
    });
  }

  async function runChasePlan(): Promise<void> {
    if (selectedSpots.length < 2) {
      setChaseNotice("请至少选择两个已保存坐标的机位。");
      return;
    }
    if (transportMode === "transit" && !transitCity.trim()) {
      setChaseNotice("公共交通规划需要填写所在城市，例如“上海”。");
      return;
    }
    setChaseRunning(true);
    setChaseNotice("正在计算机位间通勤与时间余量…");
    try {
      const sites = selectedSpots.map((name) => {
        const window = siteWindows[name] ?? defaultChaseWindow(0);
        return { name, window_start: new Date(window.start).toISOString(), window_end: new Date(window.end).toISOString() };
      });
      const firstStart = new Date(sites[0].window_start);
      const result = await postJson<ChasePlanResult>("/api/chase-plans", {
        sites,
        transport_mode: transportMode,
        depart_at: firstStart.toISOString(),
        transition_buffer_minutes: transitionBuffer,
        min_shoot_minutes: 15,
        transit_city: transitCity.trim(),
      });
      setChaseResult(result);
      setChaseNotice(result.feasible ? "赶场顺序已生成。" : "当前窗口下无法完成赶场，请减少机位或调整时间。");
    } catch (caught) {
      setChaseResult(null);
      setChaseNotice(`赶场规划失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setChaseRunning(false);
    }
  }

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">规划</h1>
          <p className="page-sub">机位 × 天象匹配，把「什么时候、去哪、拍什么」排成一条线。</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => void runPlan()} disabled={running || !planDate || !planSpotId}>
          {running ? "规划中…" : "运行规划"}
        </button>
      </header>

      <section className="d2-selection" aria-labelledby="d2-selection-title">
        <h2 id="d2-selection-title">本次拍摄</h2>
        <div className="d2-selection-fields">
          <label className="gear-field">
            <span>拍摄日期</span>
            <input type="date" value={planDate} onChange={(event) => {
              setPlanDate(event.target.value); setPlan(null); setArchive(null);
              replacePlanUrl({ subject: planSubject, date: event.target.value, spotId: planSpotId });
            }} />
          </label>
          <label className="gear-field">
            <span>拍摄题材</span>
            <select value={planSubject} onChange={(event) => {
              setPlanSubject(event.target.value); setPlan(null); setArchive(null);
              replacePlanUrl({ subject: event.target.value, date: planDate, spotId: planSpotId });
            }}>
              {PLAN_SUBJECTS.map((subject) => (
                <option value={subject} key={subject}>{subject}</option>
              ))}
            </select>
          </label>
          <label className="gear-field">
            <span>目标机位</span>
            <select value={planSpotId} onChange={(event) => {
              setPlanSpotId(event.target.value);
              setActiveSpotId(event.target.value);
              setPlan(null);
              setArchive(null);
              replacePlanUrl({ subject: planSubject, date: planDate, spotId: event.target.value });
            }}>
              <option value="">请选择可计算的机位</option>
              {planningSpots.map((spot) => <option value={spot.id} key={spot.id}>{spot.name}</option>)}
            </select>
          </label>
        </div>
        {planningSpots.length === 0 ? (
          <p className="page-sub">暂无带 WGS84 坐标和时区的机位，请先到「我的记忆」补充。</p>
        ) : null}
      </section>

      {notice ? <p className="d2-notice" role="status">{notice}</p> : null}
      <div className="d2-plan-actions">
        <button type="button" className="btn btn-secondary" disabled={!plan || transferring} onClick={() => void downloadPlan()}>导出 JSON</button>
        <button type="button" className="btn btn-secondary" disabled={transferring} onClick={() => importRef.current?.click()}>导入 JSON</button>
        <input ref={importRef} type="file" accept=".json,application/json" aria-label="选择计划 JSON 文件" hidden
          onChange={(event) => void importPlan(event.target.files?.[0])} />
      </div>
      {plan ? <SpotPlanView plan={plan} /> : replayPlan ? <SpotPlanView plan={replayPlan} frozen /> : null}

      <section className="d2-spots" aria-labelledby="d2-spots-title">
        <div className="d2-section-head"><h2 id="d2-spots-title">收藏机位</h2><a href="#/m1">管理机位</a></div>
        {spots.length === 0 ? <p className="trace-empty">档案中暂无收藏机位。</p> : (
          <div className="d2-spots-layout">
          <div className="d2-spot-list">
            {spots.map((spot) => {
              const usable = spot.coordinate_state === "ready" && Boolean(spot.tz_offset);
              return <button type="button" className={planSpotId === spot.id ? "d2-spot is-selected" : "d2-spot"}
                key={spot.id} disabled={!usable} aria-pressed={usable && planSpotId === spot.id}
                onClick={() => {
                  setPlanSpotId(spot.id); setActiveSpotId(spot.id); setPlan(null); setArchive(null);
                  replacePlanUrl({ subject: planSubject, date: planDate, spotId: spot.id });
                }}>
                <strong>{spot.name}</strong><span className={usable ? "d2-spot-status is-ready" : "d2-spot-status"}>{spotStatus(spot)}</span>
                <small>{spot.coordinate_system ?? "坐标系未确认"} · {spot.tz_offset ?? "时区未填"}{spot.direction_deg == null ? "" : ` · 朝向 ${spot.direction_deg}°`}</small>
                {spot.subject_types && spot.subject_types.length > 0 ? <small>题材：{spot.subject_types.join("、")}</small> : null}
                <small>来源：{spot.source}</small>
              </button>;
            })}
          </div>
          <SpotMap spots={spots} selectedId={planSpotId} onSelect={(id) => {
            setPlanSpotId(id);
            setActiveSpotId(id);
            setPlan(null);
            setArchive(null);
            replacePlanUrl({ subject: planSubject, date: planDate, spotId: id });
          }} />
          </div>
        )}
      </section>

      <section className="d2-chase">
        <h2>多机位赶场</h2>
        <p className="page-sub">选择 2–4 个机位，分别设定拍摄窗口；系统根据通勤时间计算可行顺序。</p>
        <div className="chip-row" style={{ margin: "12px 0" }}>
          {routeSpots.map((name) => (
            <button key={name} type="button" className="chip" aria-pressed={selectedSpots.includes(name)} onClick={() => toggleSpot(name)}>
              {selectedSpots.includes(name) ? "✓ " : ""}{name}
            </button>
          ))}
        </div>
        <label className="gear-field" style={{ maxWidth: 280 }}>
          <span>交通方式</span>
          <select value={transportMode} onChange={(event) => setTransportMode(event.target.value)}>
            <option value="driving">驾车（自驾/打车）</option>
            <option value="cycling">骑行</option>
            <option value="walking">步行</option>
            <option value="transit">公共交通</option>
          </select>
        </label>
        {transportMode === "transit" ? (
          <label className="gear-field" style={{ maxWidth: 280, marginTop: 10 }}>
            <span>公共交通所在城市</span>
            <input value={transitCity} onChange={(event) => setTransitCity(event.target.value)} placeholder="如 上海" />
          </label>
        ) : null}
        <label className="gear-field" style={{ maxWidth: 280, marginTop: 10 }}>
          <span>每次换机位缓冲（分钟）</span>
          <input type="number" min={0} max={180} value={transitionBuffer} onChange={(event) => setTransitionBuffer(Number(event.target.value))} />
        </label>
        {selectedSpots.map((name) => {
          const window = siteWindows[name] ?? defaultChaseWindow(0);
          return (
            <div className="spot-card" key={`window-${name}`} style={{ marginTop: 10 }}>
              <strong>{name}</strong>
              <label className="gear-field"><span>开始</span><input type="datetime-local" value={window.start} onChange={(event) => setSiteWindows((all) => ({ ...all, [name]: { ...window, start: event.target.value } }))} /></label>
              <label className="gear-field"><span>结束</span><input type="datetime-local" value={window.end} onChange={(event) => setSiteWindows((all) => ({ ...all, [name]: { ...window, end: event.target.value } }))} /></label>
            </div>
          );
        })}
        <div className="chip-row" style={{ marginTop: 12 }}>
          <button type="button" className="btn btn-primary" onClick={() => void runChasePlan()} disabled={chaseRunning || selectedSpots.length < 2}>
            {chaseRunning ? "计算中…" : "生成赶场计划"}
          </button>
          <span className="page-sub">已选 {selectedSpots.length}/4 个机位</span>
        </div>
        {chaseNotice ? <p className="page-sub" style={{ marginTop: 12 }}>{chaseNotice}</p> : null}
        {chaseResult ? (
          <div className="event-list" style={{ marginTop: 12 }}>
            <strong>{chaseResult.explanation}</strong>
            {chaseResult.legs.map((leg) => (
              <div key={`${leg.from_site}-${leg.to_site}`} className="spot-card">
                <div><strong>{leg.from_site} → {leg.to_site}</strong><p className="page-sub">{leg.route.distance_m == null ? "距离未知" : `${Math.round(leg.route.distance_m / 100) / 10} km`} · 预计通勤 {leg.route.duration_minutes} 分钟 · 最晚离开 {new Date(leg.latest_leave_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</p></div>
                <span className="chip">余量 {leg.margin_minutes} 分钟</span>
                <p className="page-sub">来源：{leg.route.provider}{leg.route.degraded_reason ? ` · ${leg.route.degraded_reason}` : ""}</p>
              </div>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}
