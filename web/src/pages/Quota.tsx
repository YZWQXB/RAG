import { useEffect, useState } from "react";
import { Alert } from "../components/Alert";
import { ApiError, api } from "../lib/api";

type Usage = {
  used: number;
  limit: number;
  remaining?: number;
  ratio?: number;
  state?: "ok" | "warning" | "exceeded";
};
type Report = { plan: string; usage: Record<string, Usage> };

const LABELS: Record<string, string> = {
  documents: "文档数量",
  storage_bytes: "存储空间",
  queries_per_month: "本月查询次数",
  llm_tokens_per_month: "本月生成额度",
};

function format(metric: string, value: number): string {
  if (metric === "storage_bytes") return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${value}`;
}

const BAR_COLOR = {
  ok: "bg-blue-600",
  warning: "bg-amber-500",
  exceeded: "bg-red-600",
} as const;

const TEXT_COLOR = {
  ok: "text-zinc-600",
  warning: "text-amber-700",
  exceeded: "text-red-700",
} as const;

export function Quota() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        setReport(await api.get<Report>("/api/v1/quota"));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "加载失败，请稍后重试");
      }
    })();
  }, []);

  const warnings =
    report === null
      ? []
      : Object.entries(report.usage).filter(([, item]) => item.state === "warning");

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-1 text-base font-medium text-zinc-900">用量与额度</h1>
      <p className="mb-6 text-xs text-zinc-500">
        当前套餐：{report?.plan ?? "—"}。超限后相关操作会被直接拒绝，避免产生不可控成本。
      </p>

      {error ? <Alert tone="error">{error}</Alert> : null}

      {warnings.length > 0 ? (
        <div className="mb-4">
          <Alert tone="warning">
            {warnings
              .map(([metric]) => `${LABELS[metric] ?? metric}已用超过 80%`)
              .join("、")}
            ，建议尽快扩容或清理。
          </Alert>
        </div>
      ) : null}

      {report === null ? (
        <div className="h-40 animate-pulse rounded-lg border border-zinc-200 bg-zinc-100" />
      ) : (
        <div className="space-y-4">
          {Object.entries(report.usage).map(([metric, usage]) => {
            const state = usage.state ?? (usage.ratio !== undefined && usage.ratio >= 0.9 ? "exceeded" : "ok");
            const percent =
              usage.limit > 0 ? Math.min(100, (usage.used / usage.limit) * 100) : 0;
            const remaining =
              usage.remaining !== undefined ? usage.remaining : Math.max(0, usage.limit - usage.used);

            return (
              <div key={metric} className="rounded-lg border border-zinc-200 bg-white p-4">
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="text-zinc-800">{LABELS[metric] ?? metric}</span>
                  <span className={TEXT_COLOR[state]}>
                    {format(metric, usage.used)} / {format(metric, usage.limit)}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-zinc-100">
                  <div
                    className={`h-full rounded-full ${BAR_COLOR[state]}`}
                    style={{ width: `${percent}%` }}
                  />
                </div>
                <p className="mt-2 text-xs text-zinc-500">
                  {usage.limit > 0 ? `剩余 ${format(metric, remaining)}` : "不限量"}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
