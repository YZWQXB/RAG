import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert } from "../components/Alert";
import { DocStatusBadge } from "../components/DocStatusBadge";
import { usePolling } from "../hooks/usePolling";
import { ApiError, api } from "../lib/api";

type Doc = {
  id: string;
  title: string;
  status: string;
  stage: string | null;
  error: string | null;
  chunk_count: number;
  size_bytes: number;
};

type DocPage = { items: Doc[]; total: number; page: number; size: number };

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function KbDocuments() {
  const { kbId = "" } = useParams();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const page = await api.get<DocPage>(`/api/v1/kbs/${kbId}/documents?size=50`);
      setDocs(page.items);
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载失败，请稍后重试");
      setDocs([]);
    }
  }, [kbId]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasPending = (docs ?? []).some(
    (doc) => doc.status === "pending" || doc.status === "processing",
  );
  usePolling(() => void load(), 2000, hasPending);

  const upload = async (file: File) => {
    setUploadError("");
    setUploading(true);
    try {
      await api.upload(`/api/v1/kbs/${kbId}/documents`, file);
      if (fileRef.current) fileRef.current.value = "";
      await load();
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "上传失败，请稍后重试");
    } finally {
      setUploading(false);
    }
  };

  const retry = async (docId: string) => {
    try {
      await api.post(`/api/v1/documents/${docId}/retry`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "重试失败，请稍后重试");
    }
  };

  const remove = async (docId: string) => {
    if (!window.confirm("删除后该文档不再参与检索，确定删除？")) return;
    try {
      await api.del(`/api/v1/documents/${docId}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "删除失败，请稍后重试");
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center gap-3">
        <Link to="/kbs" className="text-sm text-blue-700 underline">
          知识库
        </Link>
        <span className="text-zinc-400">/</span>
        <h1 className="text-base font-medium text-zinc-900">文档管理</h1>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {uploadError ? <Alert tone="error">{uploadError}</Alert> : null}

      <div className="mb-6 rounded-lg border border-dashed border-zinc-300 bg-white p-5">
        <label className="block cursor-pointer">
          <span className="text-sm text-zinc-700">选择文件上传</span>
          <p className="mt-1 text-xs text-zinc-500">
            支持 PDF、Word、Markdown、TXT，单个文件不超过 50MB
          </p>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.md,.markdown,.txt"
            className="mt-3 block w-full text-sm text-zinc-600"
            disabled={uploading}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
            }}
          />
        </label>
        {uploading ? <p className="mt-2 text-xs text-blue-700">上传中…</p> : null}
      </div>

      {docs === null ? (
        <div className="h-32 animate-pulse rounded-lg border border-zinc-200 bg-zinc-100" />
      ) : docs.length === 0 ? (
        <div className="rounded-lg border border-zinc-200 bg-white px-6 py-10 text-center text-sm text-zinc-600">
          还没有文档，先上传一份试试
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 text-left text-xs text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">标题</th>
                <th className="px-4 py-2 font-medium">大小</th>
                <th className="px-4 py-2 font-medium">状态</th>
                <th className="px-4 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr key={doc.id} className="border-t border-zinc-100 align-top">
                  <td className="px-4 py-3">
                    <div className="text-zinc-900">{doc.title}</div>
                    {doc.status === "ready" ? (
                      <div className="text-xs text-zinc-500">{doc.chunk_count} 个片段</div>
                    ) : null}
                    {doc.status === "failed" && doc.error ? (
                      <div className="mt-1 text-xs text-red-700">{doc.error}</div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-zinc-600">{formatSize(doc.size_bytes)}</td>
                  <td className="px-4 py-3">
                    <DocStatusBadge status={doc.status} stage={doc.stage} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-3 text-xs">
                      {doc.status === "failed" || doc.status === "pending" ? (
                        <button type="button" onClick={() => retry(doc.id)} className="text-blue-700 underline">
                          重试
                        </button>
                      ) : null}
                      <button type="button" onClick={() => remove(doc.id)} className="text-red-700 underline">
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
