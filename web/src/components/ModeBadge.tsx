import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

type ModeInfo = {
  mode: string;
  label: string;
  providers: { embedding: string; llm: string; reranker: string };
  llm_model: string;
  embedding_model: string;
  threshold: number;
  warning: string | null;
  can_switch: boolean;
};

const PROVIDER_LABEL: Record<string, string> = {
  fake: "离线替身",
  local: "本机模型",
  api: "远程 API",
};

const DOT_CLASS: Record<string, string> = {
  offline: "bg-zinc-400",
  online: "bg-emerald-500",
  custom: "bg-amber-500",
};

function providerLabel(value: string): string {
  return PROVIDER_LABEL[value] ?? value;
}

export function ModeBadge() {
  const [info, setInfo] = useState<ModeInfo | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<ModeInfo>("/api/v1/system/mode")
      .then((next) => {
        if (alive) setInfo(next);
      })
      .catch(() => {
        if (alive) setInfo(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  if (!info) return null;

  const dot = DOT_CLASS[info.mode] ?? DOT_CLASS.custom;

  const switchTo = async (mode: "offline" | "online") => {
    if (busy || mode === info.mode) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.post<ModeInfo>("/api/v1/system/mode", { mode });
      setInfo(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "切换失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-50"
        title="查看或切换运行模式"
      >
        <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
        {info.label}模式
      </button>

      {open && (
        <div className="absolute right-0 top-full z-20 mt-2 w-72 rounded-lg border border-zinc-200 bg-white p-3 text-xs shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-medium text-zinc-900">运行模式</span>
            <span className="text-zinc-400">{info.threshold.toFixed(2)} 阈值</span>
          </div>

          <dl className="space-y-1 text-zinc-600">
            <div className="flex justify-between gap-2">
              <dt>向量</dt>
              <dd className="truncate text-right text-zinc-900">
                {providerLabel(info.providers.embedding)} · {info.embedding_model}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt>生成</dt>
              <dd className="truncate text-right text-zinc-900">
                {providerLabel(info.providers.llm)} · {info.llm_model}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt>重排</dt>
              <dd className="text-right text-zinc-900">{providerLabel(info.providers.reranker)}</dd>
            </div>
          </dl>

          {info.warning && (
            <p className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 leading-relaxed text-amber-800">
              {info.warning}
            </p>
          )}

          {error && <p className="mt-2 text-red-600">{error}</p>}

          {info.can_switch ? (
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                disabled={busy || info.mode === "online"}
                onClick={() => void switchTo("online")}
                className="flex-1 rounded border border-zinc-300 px-2 py-1.5 text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                切到在线
              </button>
              <button
                type="button"
                disabled={busy || info.mode === "offline"}
                onClick={() => void switchTo("offline")}
                className="flex-1 rounded border border-zinc-300 px-2 py-1.5 text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                切到离线
              </button>
            </div>
          ) : (
            <p className="mt-3 text-zinc-400">仅空间所有者与管理员可切换</p>
          )}

          {busy && <p className="mt-2 text-zinc-500">切换中，正在重建连接…</p>}
        </div>
      )}
    </div>
  );
}
