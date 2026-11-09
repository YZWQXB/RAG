import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert } from "../components/Alert";
import { FormField } from "../components/FormField";
import { ApiError, api } from "../lib/api";
import { setTenant, setToken } from "../lib/auth";

type TenantInfo = { id: string; slug: string; name: string; role: string };

type LoginResponse = {
  token?: string;
  tenant?: TenantInfo;
  selection_required: boolean;
  tenants: TenantInfo[];
  user?: { id: string; email: string; name: string };
};

export function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [selection, setSelection] = useState<TenantInfo[] | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const enter = (token: string, tenant: TenantInfo) => {
    setToken(token);
    setTenant(tenant);
    navigate("/kbs", { replace: true });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const result = await api.post<LoginResponse>("/api/v1/auth/login", {
        email: email.trim(),
        password,
      });
      if (result.selection_required) {
        setSelection(result.tenants);
        return;
      }
      if (result.token && result.tenant) enter(result.token, result.tenant);
    } catch (err) {
      // 后端不区分"账号不存在"与"密码错误"，前端同样统一文案
      setError(err instanceof ApiError ? err.message : "网络异常，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  const chooseTenant = async (tenant: TenantInfo) => {
    setSubmitting(true);
    setError("");
    try {
      const result = await api.post<LoginResponse>("/api/v1/auth/token", {
        email: email.trim(),
        password,
        tenant_id: tenant.id,
      });
      if (result.token && result.tenant) enter(result.token, result.tenant);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "网络异常，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  if (selection) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-6">
          <h1 className="mb-4 text-base font-medium text-zinc-900">选择空间</h1>
          {error ? <Alert tone="error">{error}</Alert> : null}
          <div className="space-y-2">
            {selection.map((tenant) => (
              <button
                key={tenant.id}
                type="button"
                disabled={submitting}
                onClick={() => chooseTenant(tenant)}
                className="flex w-full items-center justify-between rounded-md border border-zinc-200 px-3 py-2 text-left text-sm hover:border-blue-500 disabled:opacity-60"
              >
                <span className="text-zinc-900">{tenant.name}</span>
                <span className="text-xs text-zinc-500">{tenant.role}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-6">
        <h1 className="mb-1 text-base font-medium text-zinc-900">登录</h1>
        <p className="mb-5 text-xs text-zinc-500">使用邮箱登录你的知识库空间</p>

        {error ? <Alert tone="error">{error}</Alert> : null}

        <form onSubmit={submit} noValidate>
          <FormField
            label="邮箱"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <FormField
            label="密码"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-60"
          >
            {submitting ? "处理中…" : "登录"}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-zinc-500">
          还没有空间？
          <Link to="/register" className="ml-1 text-blue-700 underline">
            立即创建
          </Link>
        </p>
      </div>
    </div>
  );
}
