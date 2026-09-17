/** 规划页：运行一次规划决策 → 解析结果更新天象时间线与月相；机位来自「我的记忆」档案。 */

import { useEffect, useState } from "react";
import { postJson, sendDecide } from "../api/client";
import type { DecisionCard, ProfilePayload, SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";

interface SunTimes {
  sunrise: string;
  sunset: string;
}

interface MoonInfo {
  name: string;
  advice: string;
}

interface ChasePlanResult {
  ordered_site_names: string[];
  feasible: boolean;
  risk_level: string;
  explanation: string;
  legs: Array<{
    from_site: string;
    to_site: string;
    leave_at: string;
    arrive_at: string;
    latest_leave_at: string;
    margin_minutes: number;
    route: { distance_m: number; duration_minutes: number; provider: string; degraded_reason: string };
  }>;
}

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

/** 结构化结果的字符串字段读取（缺字段返回空串；不再对摘要文本做正则解析，B4-4）。 */
function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** 结构化结果里的首个对象元素（如银河可见窗口的第一段）。 */
function firstRecord(value: unknown): Record<string, unknown> | null {
  if (!Array.isArray(value) || value.length === 0) {
    return null;
  }
  const [head] = value as unknown[];
  return typeof head === "object" && head !== null ? (head as Record<string, unknown>) : null;
}

export function D2Page() {
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState("运行一次规划决策，获取当日天文与天气时刻。");
  const [sun, setSun] = useState<SunTimes | null>(null);
  const [moon, setMoon] = useState<MoonInfo | null>(null);
  const [galaxy, setGalaxy] = useState("");
  const [spots, setSpots] = useState<string[]>([]);
  const [routeSpots, setRouteSpots] = useState<string[]>([]);
  const [lastCard, setLastCard] = useState<DecisionCard | null>(null);
  const [selectedSpots, setSelectedSpots] = useState<string[]>([]);
  const [transportMode, setTransportMode] = useState("driving");
  const [transitCity, setTransitCity] = useState("");
  const [transitionBuffer, setTransitionBuffer] = useState(18);
  const [siteWindows, setSiteWindows] = useState<Record<string, ChaseWindow>>({});
  const [chaseRunning, setChaseRunning] = useState(false);
  const [chaseResult, setChaseResult] = useState<ChasePlanResult | null>(null);
  const [chaseNotice, setChaseNotice] = useState("");
  const { addSource } = useSources();

  useEffect(() => {
    void (async () => {
      try {
        const resp = await fetch("/api/profile");
        if (resp.ok) {
          const profile = (await resp.json()) as ProfilePayload;
          const spotNames = (profile.favorite_spots ?? []).map((spot) => asText(spot["name"]));
          const names = [...(profile.common_locations ?? []), ...spotNames];
          const uniqueNames = Array.from(new Set(names));
          setSpots(uniqueNames);
          setRouteSpots(spotNames);
          setSelectedSpots(spotNames.slice(0, 2));
          setSiteWindows(Object.fromEntries(spotNames.slice(0, 2).map((name, index) => [name, defaultChaseWindow(index)])));
        }
      } catch {
        setSpots([]);
      }
    })();
    try {
      const raw = localStorage.getItem("lt.last_card");
      if (raw) {
        setLastCard((JSON.parse(raw) as { card: DecisionCard }).card);
      }
    } catch {
      setLastCard(null);
    }
  }, []);

  async function runPlan(): Promise<void> {
    setRunning(true);
    let parsedSun = false;
    setNotice("正在整理各机位与天象窗口…");
    try {
      await sendDecide("周末两天三机位对比，帮我把机位与天象窗口排一下", "", (event: SSEEvent) => {
        if (event.type === "tool_result") {
          const data = event.data ?? null;
          if (data) {
            if (event.name === "sun_times") {
              const sunrise = asText(data["日出"]);
              const sunset = asText(data["日落"]);
              if (sunrise && sunset) {
                parsedSun = true;
                setSun({ sunrise, sunset });
              }
            }
            if (event.name === "moon_phase") {
              const name = asText(data["月相名称"]);
              if (name) {
                setMoon({ name, advice: asText(data["月光影响建议"]) });
              }
            }
            if (event.name === "galaxy_visibility") {
              const window = firstRecord(data["可见窗口"]);
              const start = window ? asText(window["开始"]) : "";
              const end = window ? asText(window["结束"]) : "";
              if (start && end) {
                setGalaxy(`${start}–${end}`);
              }
            }
          }
          if (event.data_source) {
            addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
          }
        } else if (event.type === "error") {
          setNotice(`本次查询未完成：${event.message}（可稍后再试）`);
        } else if (event.type === "done") {
          setNotice(parsedSun ? "已根据本次查询更新太阳时刻与月相。" : "查询完成，但未取到天文时刻（请检查后端与数据源）。");
        }
      });
    } catch (caught) {
      setNotice(`查询失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setRunning(false);
    }
  }

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

  const marks = sun
    ? [
        { time: sun.sunrise, label: "日出" },
        { time: sun.sunset, label: "日落" },
      ]
    : [];

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">规划</h1>
          <p className="page-sub">机位 × 天象匹配，把「什么时候、去哪、拍什么」排成一条线。</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => void runPlan()} disabled={running}>
          {running ? "规划中…" : "运行规划"}
        </button>
      </header>

      <p className="page-sub" style={{ margin: "0 0 16px" }}>{notice}</p>

      <section className="grid-2">
        <div className="card">
          <span className="card-kicker">机位（来自我的记忆）</span>
          {spots.length === 0 ? (
            <p className="trace-empty">档案里还没有常去机位——去「我的记忆」页添加。</p>
          ) : (
            <div className="spot-list">
              {spots.map((name) => (
                <div className="spot-card" key={name}>
                  <div>
                    <strong>{name}</strong>
                    <p className="page-sub" style={{ margin: "4px 0 0" }}>候选机位 · 已加入档案</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <span className="card-kicker">月相 · 银河可见</span>
          <ul className="event-list">
            <li>月相：{moon ? `${moon.name}${moon.advice ? ` · ${moon.advice}` : ""}` : "运行规划后更新"}</li>
            <li>银河窗口：{galaxy || "运行规划后更新"}</li>
          </ul>
        </div>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">天象时间线</span>
        <div className="sky-timeline">
          {marks.length > 0 ? (
            <div className="sky-band">
              {marks.map((mark) => (
                <span
                  className="sky-mark"
                  key={mark.label}
                  style={{ left: `${(parseInt(mark.time.slice(0, 2), 10) * 60 + parseInt(mark.time.slice(3), 10)) / 4.8}%` }}
                >
                  {mark.time} {mark.label}
                </span>
              ))}
            </div>
          ) : (
            <p className="trace-empty">运行一次「规划」后，这里会标出日出与日落时刻（移动端可横向滚动查看）。</p>
          )}
        </div>
      </section>

      {lastCard?.time_window ? (
        <section className="card" style={{ marginTop: 20 }}>
          <span className="card-kicker">最近计划窗口</span>
          <div className="chip-row">
            <span className="chip">{lastCard.time_window}</span>
            {(lastCard.locations ?? []).slice(0, 3).map((location) => (
              <span className="chip" key={`${location.name}`}>{location.name}</span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">多机位赶场</span>
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
                <div><strong>{leg.from_site} → {leg.to_site}</strong><p className="page-sub">{Math.round(leg.route.distance_m / 100) / 10} km · 预计通勤 {leg.route.duration_minutes} 分钟 · 最晚离开 {new Date(leg.latest_leave_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</p></div>
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
