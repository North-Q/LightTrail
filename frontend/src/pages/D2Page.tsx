/** D2 规划页（E7-8）：跑一次规划决策 → 解析 tool_result 更新天象时间线/月相；机位与赶场轴为示例数据。 */

import { useState } from "react";
import { sendDecide } from "../api/client";
import type { SSEEvent } from "../api/events";
import { useSources } from "../context/SourceContext";

interface Spot {
  name: string;
  score: number;
  aspect: string;
  distance: string;
  note: string;
}

interface SunTimes {
  sunrise: string;
  sunset: string;
}

interface MoonInfo {
  name: string;
  advice: string;
}

const FAKE_SPOTS: Spot[] = [
  { name: "西湖断桥", score: 92, aspect: "朝西 265°", distance: "4.2km", note: "逆光位无遮挡，日落直射湖面" },
  { name: "崇明东滩", score: 87, aspect: "朝西 258°", distance: "38km", note: "滩涂倒影，退潮期更佳" },
  { name: "天荒坪", score: 81, aspect: "银河东南", distance: "120km", note: "光害低，适合银河拱桥" },
];

const FALLBACK_MARKS = [
  { time: "05:42", label: "日出" },
  { time: "18:06", label: "日落" },
  { time: "18:12", label: "蓝调" },
  { time: "21:40", label: "银河" },
];

function parseTime(raw: string, key: string): string {
  const match = raw.match(new RegExp(`${key}\\s*[:：]\\s*(\\d{1,2}:\\d{2})`));
  return match ? match[1] : "";
}

export function D2Page() {
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState("本页机位与赶场轴为示例数据；时间线可运行一次规划决策获取真实天文/天气时刻。");
  const [sun, setSun] = useState<SunTimes | null>(null);
  const [moon, setMoon] = useState<MoonInfo | null>(null);
  const [galaxy, setGalaxy] = useState("");
  const { addSource } = useSources();

  async function runPlan(): Promise<void> {
    setRunning(true);
    setNotice("运行规划管线中（天气 7 天 + 月相 + 太阳时刻 + 机位匹配）…");
    let parsedSun = false;
    try {
      await sendDecide("周末两天三机位对比，帮我把机位与天象窗口排一下", "", (event: SSEEvent) => {
        if (event.type === "tool_result") {
          const summary = event.result ?? "";
          if (event.name === "sun_times") {
            const sunrise = parseTime(summary, "日出");
            const sunset = parseTime(summary, "日落");
            if (sunrise && sunset) {
              parsedSun = true;
              setSun({ sunrise, sunset });
            }
          }
          if (event.name === "moon_phase") {
            const moonMatch = summary.match(/月相名称\s*[:：]\s*([^,，]+)/);
            const adviceMatch = summary.match(/月光影响建议\s*[:：]\s*([^,，]+)/);
            if (moonMatch) {
              setMoon({ name: moonMatch[1].trim(), advice: adviceMatch ? adviceMatch[1].trim() : "" });
            }
          }
          if (event.name === "galaxy_visibility") {
            const windowMatch = summary.match(/可见窗口\s*[:：]\s*(\[[^\]]*\])/);
            if (windowMatch) {
              setGalaxy(windowMatch[1].replace(/"/g, "").slice(0, 60));
            }
          }
          if (event.data_source) {
            addSource({ name: event.data_source, note: `${event.name} · ${event.field ?? "结果"}` });
          }
        } else if (event.type === "error") {
          setNotice(`管线失败：${event.message}（时间线保持示例数据）`);
        } else if (event.type === "done") {
          setNotice(
            parsedSun
              ? "已从本次决策的 tool_result 更新时间线与月相（其余板块仍为示例数据）。"
              : "决策完成；未解析到天文时刻（检查后端/数据源），时间线保持示例数据。",
          );
        }
      });
    } catch (caught) {
      setNotice(`请求失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setRunning(false);
    }
  }

  const marks = sun
    ? [
        { time: sun.sunrise, label: "日出 ✦" },
        { time: sun.sunset, label: "日落 ✦" },
        ...FALLBACK_MARKS.filter((mark) => mark.label !== "日出" && mark.label !== "日落"),
      ]
    : FALLBACK_MARKS;

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">D2 · 规划</h1>
          <p className="page-sub">机位 × 天象匹配、多云候补日与赶场编排。</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => void runPlan()} disabled={running}>
          {running ? "规划中…" : "运行一次规划决策"}
        </button>
      </header>

      <p className="page-sub" style={{ margin: "0 0 16px" }}>{notice}</p>

      <section className="grid-2">
        <div className="card">
          <span className="card-kicker">机位列表<span className="fake-tag" style={{ marginLeft: 8 }}>示例数据</span></span>
          <div className="spot-list">
            {FAKE_SPOTS.map((spot) => (
              <div className="spot-card" key={spot.name}>
                <div>
                  <strong>{spot.name}</strong>
                  <p className="page-sub" style={{ margin: "4px 0 0" }}>
                    {spot.aspect} · {spot.distance} · {spot.note}
                  </p>
                </div>
                <div className="spot-score">
                  <span className="score">{spot.score}</span>
                  <span className="page-sub">得分</span>
                </div>
              </div>
            ))}
          </div>
          <p className="page-sub" style={{ margin: "12px 0 0" }}>机位来自档案常去机位 + 评分规则（真实接入随 favorit 数据完善）。</p>
        </div>

        <div className="card">
          <span className="card-kicker">月相 · 银河可见</span>
          <ul className="event-list">
            <li>
              月相：{moon ? moon.name : "新月 · 示例"}（{moon ? "实时" : "示例"}）
              {moon && moon.advice ? ` · ${moon.advice}` : ""}
            </li>
            <li>银河窗口：{galaxy ? galaxy : "20:10–23:50 · 示例"}</li>
            <li>月光影响：低，整晚可拍（示例）</li>
          </ul>
        </div>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">
          天象时间线（sky-band）
          <span className="fake-tag" style={{ marginLeft: 8 }}>{sun ? "日出/日落来自实时决策" : "示例数据（可运行上方规划决策刷新）"}</span>
        </span>
        <div className="sky-timeline">
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
        </div>
        <p className="page-sub" style={{ marginTop: 8 }}>移动端可横向滚动（min-width 680px）。✦ 标记来自本次决策的真实 tool_result。</p>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">赶场时间轴<span className="fake-tag" style={{ marginLeft: 8 }}>示例数据</span></span>
        <div className="chip-row">
          {["16:40 到达机位A", "18:06 日落 · 火烧云", "18:40 蓝调", "21:40 银河", "23:30 收工"].map((step) => (
            <span className="chip" key={step}>{step}</span>
          ))}
        </div>
      </section>
    </div>
  );
}
