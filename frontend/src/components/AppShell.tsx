/** 应用外壳（E7-6）：顶栏旅程导航（灵感→规划→决策→复盘）+ 汉堡抽屉 + 页脚解释中心。 */

import { useEffect, useState, type ReactElement, type ReactNode } from "react";
import { useSources } from "../context/SourceContext";
import { BookIcon, CloseIcon, DataIcon, MenuIcon, PinIcon, RefreshIcon, SettingsIcon, SparkIcon, TargetIcon } from "./icons";

type RouteKey = "home" | "d1" | "d2" | "d3" | "d4" | "m1";

interface NavStep {
  key: RouteKey;
  label: string;
  icon: (props: { className?: string }) => ReactElement;
}

const JOURNEY_STEPS: NavStep[] = [
  { key: "d1", label: "灵感", icon: SparkIcon },
  { key: "d2", label: "规划", icon: PinIcon },
  { key: "d3", label: "决策", icon: TargetIcon },
  { key: "d4", label: "复盘", icon: RefreshIcon },
];

const HASH_ROUTES: { hash: string; key: RouteKey }[] = [
  { hash: "#/d1", key: "d1" },
  { hash: "#/d2", key: "d2" },
  { hash: "#/d3", key: "d3" },
  { hash: "#/d4", key: "d4" },
  { hash: "#/m1", key: "m1" },
];

export function routeKey(hash: string): RouteKey {
  const found = HASH_ROUTES.find((item) => hash === item.hash);
  return found ? found.key : "home";
}

export function go(hash: string): void {
  window.location.hash = hash;
}

export function AppShell({ children }: { children: ReactNode }) {
  const [hash, setHash] = useState<string>(window.location.hash || "#/home");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dataOpen, setDataOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { sources } = useSources();

  useEffect(() => {
    const onHash = (): void => {
      setHash(window.location.hash || "#/home");
      setDrawerOpen(false);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const active = routeKey(hash);
  const stageActive = (key: string): boolean => active === key;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="container topbar-inner">
          <button type="button" className="brand" onClick={() => go("#/home")} aria-label="返回旅程总览">
            <span className="brand-mark" aria-hidden="true">
              <SparkIcon />
            </span>
            <span className="brand-name">
              LightTrail<span className="brand-sub">光迹</span>
            </span>
          </button>

          <nav className={`main-nav${drawerOpen ? " is-open" : ""}`} id="main-nav" aria-label="拍摄旅程">
            {JOURNEY_STEPS.map((step, index) => {
              const Icon = step.icon;
              return (
                <span key={step.key} style={{ display: "contents" }}>
                  {index > 0 ? <span className="nav-arrow" aria-hidden="true">→</span> : null}
                  <button
                    type="button"
                    className={`nav-step${stageActive(step.key) ? " active" : ""}`}
                    onClick={() => go(`#/${step.key}`)}
                  >
                    <Icon />
                    {step.label}
                  </button>
                </span>
              );
            })}
            <div className="nav-drawer-footer">
              <button type="button" className="btn btn-ghost" onClick={() => go("#/m1")}>
                <BookIcon />
                我的记忆
              </button>
              <button type="button" className="icon-btn" onClick={() => setSettingsOpen(true)}>
                <SettingsIcon />
                全局设置
              </button>
            </div>
          </nav>
          {drawerOpen ? <div className="menu-backdrop" onClick={() => setDrawerOpen(false)} aria-hidden="true" /> : null}

          <div className="topbar-actions">
            <button type="button" className="btn btn-ghost" onClick={() => go("#/m1")}>
              <BookIcon />
              我的记忆
            </button>
            <button type="button" className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="全局设置">
              <SettingsIcon />
            </button>
          </div>

          <button
            type="button"
            className="menu-btn icon-btn"
            onClick={() => setDrawerOpen((open) => !open)}
            aria-label="打开菜单"
            aria-expanded={drawerOpen}
            aria-controls="main-nav"
          >
            <MenuIcon />
          </button>
        </div>
      </header>

      <main className="page-body">{children}</main>

      <footer className="app-footer">
        <div className="container footer-inner">
          <button type="button" className="btn btn-ghost" onClick={() => setDataOpen(true)}>
            <DataIcon />
            数据与依据 · 解释中心
          </button>
          <p className="footer-note">LightTrail · Web 形态（原型即规格：docs/design/delivery/lighttrail-prototype.html）</p>
        </div>
      </footer>

      {/* 解释中心模态（铁律③：集中展示数据源清单） */}
      <div className={`modal${dataOpen ? " is-open" : ""}`} role="dialog" aria-modal="true" aria-label="数据与依据 · 解释中心">
        <div className="modal-backdrop" onClick={() => setDataOpen(false)} />
        <div className="modal-card">
          <button type="button" className="icon-btn modal-close" onClick={() => setDataOpen(false)} aria-label="关闭">
            <CloseIcon />
          </button>
          <h2>数据与依据 · 解释中心</h2>
          <p>每个决策都可溯源：查看当前会话用到的数据源与更新状态，确保可信、可复核。</p>
          <div className="data-sources">
            <h4>
              <DataIcon />
              数据源清单
            </h4>
            {sources.length === 0 ? (
              <p className="trace-empty">还没有数据源——去「灵感 / 决策」跑一次提问后回来查看。</p>
            ) : (
              <ul>
                {sources.map((entry) => (
                  <li key={entry.name}>
                    <span>
                      {entry.name} — {entry.note}
                      {entry.fake ? <span className="fake-tag" style={{ marginLeft: 8 }}>示例数据</span> : null}
                    </span>
                    {entry.time ? <span className="src-time">{entry.time}</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      {/* 全局设置（第二迭代；先给占位，不做假功能） */}
      <div className={`modal${settingsOpen ? " is-open" : ""}`} role="dialog" aria-modal="true" aria-label="全局设置">
        <div className="modal-backdrop" onClick={() => setSettingsOpen(false)} />
        <div className="modal-card">
          <button type="button" className="icon-btn modal-close" onClick={() => setSettingsOpen(false)} aria-label="关闭">
            <CloseIcon />
          </button>
          <h2>全局设置</h2>
          <p>第二迭代开发（路线图 E7-10 之后）。当前配置项：LLM_SERIAL_LLM / LLM_REASON_THINKING 见项目 .env。</p>
        </div>
      </div>
    </div>
  );
}
