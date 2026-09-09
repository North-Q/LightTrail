/** 应用入口（E7-6）：6 页旅程路由（#/home #/d1 #/d2 #/d3 #/d4 #/m1）+ 旧 hash 别名兼容。 */

import { useEffect, useState } from "react";
import { AppShell } from "./components/AppShell";
import { SourceProvider } from "./context/SourceContext";
import { CardPageFallback } from "./pages/_cardFallback";
import { D1Page } from "./pages/D1Page";
import { D2Page } from "./pages/D2Page";
import { D3Page } from "./pages/D3Page";
import { D4Page } from "./pages/D4Page";
import { HomePage } from "./pages/HomePage";
import { M1Page } from "./pages/M1Page";

/** E7-5 旧 hash → 新 6 页路由（功能融合不丢）。 */
const ALIASES: [string, string][] = [
  ["#/chat", "#/d1"],
  ["#/sessions", "#/home"],
  ["#/card", "#/d3"],
];

function normalizeHash(hash: string): string {
  const target = ALIASES.find(([from]) => hash === from);
  return target ? target[1] : hash;
}

export default function App() {
  const [hash, setHash] = useState<string>(() => normalizeHash(window.location.hash || "#/home"));

  useEffect(() => {
    const onHashChange = (): void => {
      const raw = window.location.hash || "#/home";
      const normalized = normalizeHash(raw);
      if (normalized !== raw) {
        // 旧 hash 别名：替换地址（不留历史堆积），随后 hashchange 触发重渲染
        window.location.replace(normalized);
        return;
      }
      setHash(normalized);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const page = (() => {
    switch (hash) {
      case "#/d1":
        return <D1Page />;
      case "#/d2":
        return <D2Page />;
      case "#/d3":
        return <D3Page />;
      case "#/d4":
        return <D4Page />;
      case "#/m1":
        return <M1Page />;
      case "#/card":
        return <CardPageFallback />;
      default:
        return <HomePage />;
    }
  })();

  return (
    <SourceProvider>
      <AppShell>{page}</AppShell>
    </SourceProvider>
  );
}
