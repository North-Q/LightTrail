/** 规划页：运行一次规划决策 → 解析结果更新天象时间线与月相；机位来自「我的记忆」档案。 */

import { useEffect, useState } from "react";
import { sendDecide } from "../api/client";
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
  const [lastCard, setLastCard] = useState<DecisionCard | null>(null);
  const { addSource } = useSources();

  useEffect(() => {
    void (async () => {
      try {
        const resp = await fetch("/api/profile");
        if (resp.ok) {
          const profile = (await resp.json()) as ProfilePayload;
          const spotNames = (profile.favorite_spots ?? []).map((spot) => asText(spot["name"]));
          const names = [...(profile.common_locations ?? []), ...spotNames];
          setSpots(Array.from(new Set(names)));
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
    </div>
  );
}
