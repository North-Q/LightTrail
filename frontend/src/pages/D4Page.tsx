/** D4 复盘页（E7-6 骨架：批量上传 + 4 维分析 + 处方；E7-10 接通 /api/photos/review）。 */

const FAKE_DIMENSIONS = [
  { key: "曝光", value: "准确 · 高光略压", note: "直方图右缘有 2% 裁剪，建议-0.3EV" },
  { key: "构图", value: "三分法 · 前景丰富", note: "水平线略斜，可旋转 0.8°" },
  { key: "色彩", value: "暖调统一", note: "阴影偏青，白平衡可再校 200K" },
  { key: "时间", value: "黄金时刻后 12 分钟", note: "蓝调未完全出现，稍晚更佳" },
];

const FAKE_PRESCRIPTIONS = [
  { level: "high", text: "下次使用包围曝光 ±1EV，保证高光细节" },
  { level: "mid", text: "换 14mm 定焦收缩到 f/8 提升星芒与边缘" },
  { level: "low", text: "后期先校白平衡，再做局部提亮" },
];

export function D4Page() {
  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">D4 · 复盘</h1>
          <p className="page-sub">上传实拍照片 → 四维分析 + 可执行处方（EXIF 与画面多模态）。</p>
        </div>
        <span className="fake-tag">◆ 上传与分析在 E7-10 接通 /api/photos/review</span>
      </header>

      <div className="upload-zone">
        <p style={{ margin: 0 }}>＋ 批量上传照片（jpg/png，单张 ≤10MB）</p>
        <p className="page-sub" style={{ margin: "8px 0 0" }}>E7-10 接入后，上传即跑分析管线（EXIF + 多模态）。</p>
      </div>

      <section className="analysis-grid" style={{ marginTop: 20 }}>
        {FAKE_DIMENSIONS.map((item) => (
          <div className="analysis-card" key={item.key}>
            <span className="card-kicker">{item.key}</span>
            <p style={{ margin: "4px 0" }}><strong>{item.value}</strong></p>
            <p className="page-sub" style={{ margin: 0 }}>{item.note}</p>
          </div>
        ))}
      </section>

      <section className="card" style={{ marginTop: 20 }}>
        <span className="card-kicker">可执行处方（高 / 中 / 低优先级）</span>
        <div className="prescription-list">
          {FAKE_PRESCRIPTIONS.map((item) => (
            <div className="prescription" key={item.text}>
              <span className={`level level-${item.level}`}>{item.level === "high" ? "高" : item.level === "mid" ? "中" : "低"}</span>
              <span>{item.text}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
