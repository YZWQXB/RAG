import { useEffect, useRef } from "react";

/** 条件轮询：enabled 为 false 时自动停止，组件卸载时清理。 */
export function usePolling(callback: () => void, intervalMs: number, enabled: boolean): void {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => saved.current(), intervalMs);
    return () => window.clearInterval(timer);
  }, [enabled, intervalMs]);
}
