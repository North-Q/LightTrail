/** 将等待操作绑定到组件，卸载自动停止接收；React 重挂载可重新启用。 */
import { useEffect, useRef } from "react";
import { RequestScope } from "./requestScope";

export function useRequestScope(): RequestScope {
  const scope = useRef(new RequestScope()).current;
  useEffect(() => {
    scope.activate();
    return () => scope.dispose();
  }, [scope]);
  return scope;
}
