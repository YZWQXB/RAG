import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert } from "../components/Alert";
import { FormField } from "../components/FormField";
import { ApiError, api } from "../lib/api";
import { setTenant, setToken } from "../lib/auth";

type RegisterResponse = {
  token: string;
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
};

export function Register() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrors({});
    setFormError("");

    const nextErrors: Record<string, string> = {};
    if (!email.trim()) nextErrors.email = "请输入邮箱";
    else if (!/^[\w.+-]+@[\w-]+\.[\w.]{2,}$/.test(email.trim())) nextErrors.email = "邮箱格式不正确";
    if (!password) nextErrors.password = "请输入密码";
    else if (password.length < 8 || !/[A-Za-z]/.test(password) || !/\d/.test(password)) {
      nextErrors.password = "密码至少 8 位，且需同时包含字母和数字";
    }
    if (!tenantName.trim()) nextErrors.tenantName = "请输入空间名称";

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }

    setSubmitting(true);
    try {
      const result = await api.post<RegisterResponse>("/api/v1/auth/register", {
        email: email.trim(),
        password,
        tenant_name: tenantName.trim(),
      });
      setToken(result.token);
      setTenant({
        id: result.tenant_id,
        slug: result.tenant_slug,
        name: result.tenant_name,
        role: "owner",
      });
      navigate("/kbs", { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === "EMAIL_TAKEN") setErrors({ email: error.message });
        else if (error.code === "SLUG_TAKEN") setErrors({ tenantName: error.message });
        else if (error.code === "WEAK_PASSWORD") setErrors({ password: error.message });
        else setFormError(error.message);
      } else {
        setFormError("网络异常，请稍后重试");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-6">
        <h1 className="mb-1 text-base font-medium text-zinc-900">创建空间</h1>
        <p className="mb-5 text-xs text-zinc-500">注册后你将成为该空间的所有者</p>

        {formError ? <Alert tone="error">{formError}</Alert> : null}

        <form onSubmit={submit} noValidate>
          <FormField
            label="邮箱"
            type="email"
            autoComplete="email"
            value={email}
            error={errors.email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <FormField
            label="密码"
            type="password"
            autoComplete="new-password"
            value={password}
            error={errors.password}
            hint="至少 8 位，包含字母和数字"
            onChange={(event) => setPassword(event.target.value)}
          />
          <FormField
            label="空间名称"
            value={tenantName}
            error={errors.tenantName}
            onChange={(event) => setTenantName(event.target.value)}
          />
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-60"
          >
            {submitting ? "处理中…" : "创建空间"}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-zinc-500">
          已有账号？
          <Link to="/login" className="ml-1 text-blue-700 underline">
            去登录
          </Link>
        </p>
      </div>
    </div>
  );
}
