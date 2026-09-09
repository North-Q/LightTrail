/** 环图组件（真源 §4.4）：SVG ring + dashoffset 动画；主值居中（铁律① 的主值层）。 */

import { useId } from "react";

export function Ring({
  value,
  size = 120,
  label,
}: {
  /** 0-100 的百分比值。 */
  value: number;
  size?: number;
  label?: string;
}) {
  const gradientId = useId();
  const clamped = Math.max(0, Math.min(100, value));
  const radius = (size - 10) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);
  const center = size / 2;
  return (
    <div className="ring-wrap" style={{ position: "relative", width: size, height: size }}>
      <svg className="ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label ?? "进度"} ${clamped}%`}>
        <circle className="ring-bg" cx={center} cy={center} r={radius} fill="none" strokeWidth={8} />
        <circle
          className="ring-fill"
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={8}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          stroke={`url(#${gradientId})`}
        />
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#E8A23B" />
            <stop offset="0.45" stopColor="#C06B4A" />
            <stop offset="1" stopColor="#5B6EDB" />
          </linearGradient>
        </defs>
      </svg>
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <span className="confidence-display" style={{ fontSize: 30 }}>
          {Math.round(clamped)}
          <small style={{ fontSize: 14 }}>%</small>
        </span>
        {label ? <span className="confidence-sub" style={{ fontSize: 12 }}>{label}</span> : null}
      </div>
    </div>
  );
}
