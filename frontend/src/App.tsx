/** 应用外壳（E7-5）：三核心页导航（对话 / 会话列表 / DecisionCard 渲染），hash 路由。 */

import { useEffect, useState } from "react";
import { CardPage } from "./pages/CardPage";
import { ChatPage } from "./pages/ChatPage";
import { SessionsPage } from "./pages/SessionsPage";

type Route = "chat" | "sessions" | "card";

function routeFromHash(hash: string): Route {
  if (hash.startsWith("#/sessions")) {
    return "sessions";
  }
  if (hash.startsWith("#/card")) {
    return "card";
  }
  return "chat";
}

const NAV_ITEMS: { route: Route; hash: string; label: string }[] = [
  { route: "chat", hash: "#/chat", label: "对话" },
  { route: "sessions", hash: "#/sessions", label: "会话" },
  { route: "card", hash: "#/card", label: "决策卡片" },
];

export default function App() {
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));

  useEffect(() => {
    const onHashChange = (): void => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#/chat">
          <span className="brand-glow">◆</span> LightTrail · 光迹
        </a>
        <nav className="main-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.route}
              href={item.hash}
              className={route === item.route ? "active" : ""}
              aria-current={route === item.route ? "page" : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
      </header>
      <main className="page-body">
        {route === "chat" ? <ChatPage /> : null}
        {route === "sessions" ? <SessionsPage /> : null}
        {route === "card" ? <CardPage /> : null}
      </main>
    </div>
  );
}
