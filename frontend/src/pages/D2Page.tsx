/** D2 规划页（E7-6 骨架：机位列表 + 天象时间线 + 月相/银河窗口；数据用结构正确的示例并标注）。 */

const FAKE_SPOTS = [
  { name: "西湖断桥", score: 92, aspect: "朝西 265°", distance: "4.2km", note: "逆光位无遮挡，日落直射湖面" },
  { name: "崇明东滩", score: 87, aspect: "朝西 258°", distance: "38km", note: "滩涂倒影，退潮期更佳" },
  { name: "天荒坪", score: 81, aspect: "银河东南", distance: "120km", note: "光害低，适合银河拱桥" },
];

const FAKE_MARKS = [
  { time: "05:42", label: "日出" },
  { time: "18:06", label: "日落" },
  { time: "18:12", label: "蓝调" },
  { time: "21:40", label: "银河" },
];

export function D2Page() {
  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">D2 · 规划</h1>
          <p className="page-sub">机位 × 天象匹配、多云候补日与赶场编排。</p>
        </div>
        <span className="fake-tag">◆ 本页为示例数据（真实数据随决策管线 tool_result 接入）</span>
      </header>

      <section className="grid-2">
        <div className="card">
          <span className="card-kicker">机位列表</span>
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
        </div>

        <div className="card">
          <span className="card-kicker">月相 · 银河可见</span>
          <ul className="event-list">
            <li>月相：新月（照亮 2%）· 银河窗口 20:10–23:50</li>
            <li>月光影响：低，整晚可拍</li>
            <li>推荐机位：天荒坪（光害 2 级）</li>
          </ul>
        </div>
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">天象时间线（sky-band）</span>
        <div className="sky-timeline">
          <div className="sky-band">
            {FAKE_MARKS.map((mark) => (
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
        <p className="page-sub" style={{ margin: 8 }}>
          移动端可横向滚动查看（min-width 680px）。数据示例：真实窗口随 /api/decide 的 tool_result 事件接入。
        </p>
      </section>
    </div>
  );
}
