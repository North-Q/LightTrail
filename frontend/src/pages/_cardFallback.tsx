/** 兼容占位：不应再被路由命中的旧页（#/card 已别名到 #/d3，双保险提示）。 */

import { go } from "../components/AppShell";

export function CardPageFallback() {
  return (
    <div className="container page-main">
      <p className="trace-empty">决策卡片已并入 D3 决策页。</p>
      <button type="button" className="btn btn-primary" onClick={() => go("#/d3")}>去 D3 决策</button>
    </div>
  );
}
