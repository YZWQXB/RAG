import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/Alert";
import { FormField } from "../components/FormField";
import { ApiError, api } from "../lib/api";

export type Kb = {
  id: string;
  name: string;
  description: string | null;
  status: string;
  document_count: number;
  updated_at: string | null;
};

export function KbList() {
  const [kbs, setKbs] = useState<Kb[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    try {
      setKbs(await api.get<Kb[]>("/api/v1/kbs"));
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载失败，请稍后重试");
      setKbs([]);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      setNameError("知识库名称不能为空");
      return;
    }
    setSubmitting(true);
    setNameError("");
    try {
      await api.post<Kb>("/api/v1/kbs", { name: name.trim() });
      setName("");
      setCreating(false);
      await load();
    } catch (err) {
      setNameError(err instanceof ApiError ? err.message : "创建失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-base font-medium text-zinc-900">知识库</h1>
        <button
          type="button"
          onClick={() => setCreating((value) => !value)}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          新建知识库
        </button>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}

      {creating ? (
        <form onSubmit={create} className="mb-6 rounded-lg border border-zinc-200 bg-white p-4">
          <FormField
            label="名称"
            value={name}
            error={nameError}
            onChange={(event) => setName(event.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={submitting}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-60"
            >
              {submitting ? "创建中…" : "创建"}
            </button>
            <button
              type="button"
              onClick={() => setCreating(false)}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700"
            >
              取消
            </button>
          </div>
        </form>
      ) : null}

      {kbs === null ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <div key={index} className="h-28 animate-pulse rounded-lg border border-zinc-200 bg-zinc-100" />
          ))}
        </div>
      ) : kbs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 bg-white px-6 py-12 text-center">
          <p className="text-sm text-zinc-700">还没有知识库，创建第一个开始上传文档</p>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="mt-4 rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white"
          >
            新建知识库
          </button>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {kbs.map((kb) => (
            <Link
              key={kb.id}
              to={`/kbs/${kb.id}/documents`}
              className="rounded-lg border border-zinc-200 bg-white p-4 transition hover:border-blue-400"
            >
              <h2 className="truncate text-sm font-medium text-zinc-900">{kb.name}</h2>
              <p className="mt-1 line-clamp-2 h-8 text-xs text-zinc-500">
                {kb.description || "暂无描述"}
              </p>
              <p className="mt-3 text-xs text-zinc-500">
                {kb.document_count} 个文档 · 更新于 {kb.updated_at?.slice(0, 10) ?? "—"}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
