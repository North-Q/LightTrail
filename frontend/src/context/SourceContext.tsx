/** 全局数据源上下文：解释中心（铁律③）唯一数据源清单。 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface SourceEntry {
  /** 数据源名（如工具名 / 数据源标签）。 */
  name: string;
  /** 说明（如来源渠道、覆盖字段）。 */
  note: string;
  /** 更新时间（可为空）。 */
  time?: string;
}

interface SourceContextValue {
  sources: SourceEntry[];
  addSource: (entry: SourceEntry) => void;
  clearSources: () => void;
}

const SourceContext = createContext<SourceContextValue | null>(null);

export function SourceProvider({ children }: { children: ReactNode }) {
  const [sources, setSources] = useState<SourceEntry[]>([]);

  const addSource = useCallback((entry: SourceEntry) => {
    setSources((prev) => {
      if (prev.some((item) => item.name === entry.name)) {
        return prev;
      }
      return [...prev, entry];
    });
  }, []);

  const clearSources = useCallback(() => setSources([]), []);

  const value = useMemo(() => ({ sources, addSource, clearSources }), [sources, addSource, clearSources]);
  return <SourceContext.Provider value={value}>{children}</SourceContext.Provider>;
}

export function useSources(): SourceContextValue {
  const value = useContext(SourceContext);
  if (!value) {
    throw new Error("useSources 必须在 SourceProvider 内使用");
  }
  return value;
}
