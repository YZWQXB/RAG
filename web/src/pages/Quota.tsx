import { useEffect, useState } from "react";
import { Alert } from "../components/Alert";
import { ApiError, api } from "../lib/api";

type Usage = { used: number; limit: number };
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

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-1 text-base font-medium text-zinc-900">用量与额度</h1>
      <p className="mb-6 text-xs text-zinc-500">
        当前套餐：{report?.plan ?? "—"}。超限后相关操作会被直接拒绝，避免产生不可控成本。
      </p>

      {error ? <Alert tone="error">{error}</Alert> : null}

      {report === null ? (
        <div className="h-40 animate-pulse rounded-lg border border-zinc-200 bg-zinc-100" />
      ) : (
        <div className="space-y-4">
          {Object.entries(report.usage).map(([metric, usage]) => {
            const percent = usage.limit > 0 ? Math.min(100, (usage.used / usage.limit) * 100) : 0;
            const danger = percent >= 90;
            return (
              <div key={metric} className="rounded-lg border border-zinc-200 bg-white p-4">
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="text-zinc-800">{LABELS[metric] ?? metric}</span>
                  <span className={danger ? "text-red-700" : "text-zinc-600"}>
                    {format(metric, usage.used)} / {format(metric, usage.limit)}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-zinc-100">
                  <div
                    className={`h-full rounded-full ${danger ? "bg-red-600" : "bg-blue-600"}`}
                    style={{ width: `${percent}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
