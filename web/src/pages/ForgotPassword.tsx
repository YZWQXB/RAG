import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/Alert";
import { FormField } from "../components/FormField";

type Step = 1 | 2 | 3;

const CODE_TTL_SECONDS = 60;
const MIN_PASSWORD_LENGTH = 8;

const STEP_LABELS = ["验证身份", "设置新密码", "完成"];

/** 邮箱或手机号：前端只做形态判断，真实校验以服务端为准。 */
function classifyAccount(raw: string): "email" | "phone" | null {
  const value = raw.trim();
  if (!value) return null;
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return "email";
  if (/^1[3-9]\d{9}$/.test(value)) return "phone";
  return null;
}

function maskAccount(raw: string, kind: "email" | "phone"): string {
  const value = raw.trim();
  if (kind === "phone") {
    return `${value.slice(0, 3)}****${value.slice(-4)}`;
  }
  const [name, domain] = value.split("@");
  const head = name.slice(0, 1);
  return `${head}${"*".repeat(Math.max(2, name.length - 1))}@${domain}`;
}

function passwordProblem(password: string): string {
  if (password.length < MIN_PASSWORD_LENGTH) return `密码至少 ${MIN_PASSWORD_LENGTH} 位`;
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) return "密码需同时包含字母和数字";
  if (new Set(password).size < 4) return "密码过于简单，请使用更多不同的字符";
  return "";
}

function Stepper({ current }: { current: Step }) {
  return (
    <ol className="mb-6 flex items-center" aria-label="找回密码步骤">
      {STEP_LABELS.map((label, index) => {
        const step = (index + 1) as Step;
        const active = step === current;
        const done = step < current;
        return (
          <li key={label} className="flex flex-1 items-center last:flex-none">
            <div className="flex items-center gap-2">
              <span
                aria-current={active ? "step" : undefined}
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] ${
                  done
                    ? "bg-emerald-600 text-white"
                    : active
                      ? "bg-blue-600 text-white"
                      : "bg-zinc-100 text-zinc-500"
                }`}
              >
                {done ? "✓" : step}
              </span>
              <span
                className={`whitespace-nowrap text-xs ${
                  active ? "text-zinc-900" : "text-zinc-500"
                } ${step === 3 ? "hidden sm:inline" : ""}`}
              >
                {label}
              </span>
            </div>
            {index < STEP_LABELS.length - 1 ? (
              <span
                className={`mx-2 h-px flex-1 ${done ? "bg-emerald-500" : "bg-zinc-200"}`}
                aria-hidden="true"
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export function ForgotPassword() {
  const [step, setStep] = useState<Step>(1);
  const [account, setAccount] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [sending, setSending] = useState(false);

  const kind = useMemo(() => classifyAccount(account), [account]);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  const sendCode = () => {
    setError("");
    if (!kind) {
      setError("请输入有效的邮箱或手机号");
      return;
    }
    setSending(true);
    // 界面预览：这里只模拟发送成功与倒计时，不发起请求
    window.setTimeout(() => {
      setSending(false);
      setCountdown(CODE_TTL_SECONDS);
      setNotice(`验证码已发送至 ${maskAccount(account, kind)}（预览模式：任意 6 位数字可通过）`);
    }, 300);
  };

  const goReset = (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    if (!kind) {
      setError("请输入有效的邮箱或手机号");
      return;
    }
    if (!/^\d{6}$/.test(code.trim())) {
      setError("请输入 6 位数字验证码");
      return;
    }
    setNotice("");
    setStep(2);
  };

  const submitReset = (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    const problem = passwordProblem(password);
    if (problem) {
      setError(problem);
      return;
    }
    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    setStep(3);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 px-4 py-8">
      <div className="w-full max-w-md rounded-lg border border-zinc-200 bg-white p-5 sm:p-7">
        <h1 className="mb-1 text-base font-medium text-zinc-900">找回密码</h1>
        <p className="mb-5 text-xs text-zinc-500">
          通过邮箱或手机号验证身份，然后设置新密码
        </p>

        <Stepper current={step} />

        {error ? (
          <div aria-live="polite">
            <Alert tone="error">{error}</Alert>
          </div>
        ) : null}
        {notice ? (
          <div aria-live="polite">
            <Alert tone="info">{notice}</Alert>
          </div>
        ) : null}

        {step === 1 ? (
          <form onSubmit={goReset} noValidate>
            <FormField
              label="邮箱或手机号"
              type="text"
              inputMode="email"
              autoComplete="username"
              placeholder="name@company.com 或 13800138000"
              value={account}
              onChange={(event) => setAccount(event.target.value)}
              hint="支持注册时使用的邮箱或手机号"
            />

            <label htmlFor="verify-code" className="mb-1 block text-sm text-zinc-700">
              验证码
            </label>
            <div className="mb-1 flex flex-col gap-2 sm:flex-row">
              <input
                id="verify-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="6 位数字"
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm tracking-widest outline-none transition focus:border-blue-600 sm:flex-1"
              />
              <button
                type="button"
                onClick={sendCode}
                disabled={countdown > 0 || sending}
                className="w-full shrink-0 rounded-md border border-zinc-300 px-3 py-2 text-sm text-zinc-700 transition hover:bg-zinc-50 disabled:opacity-60 sm:w-32"
              >
                {countdown > 0 ? `${countdown}s 后重发` : sending ? "发送中…" : "获取验证码"}
              </button>
            </div>
            <p className="mb-4 text-xs text-zinc-500">验证码 10 分钟内有效，请勿转发给他人</p>

            <button
              type="submit"
              className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700"
            >
              下一步
            </button>
          </form>
        ) : null}

        {step === 2 ? (
          <form onSubmit={submitReset} noValidate>
            <p className="mb-4 rounded-md bg-zinc-50 px-3 py-2 text-xs text-zinc-600">
              正在为 <span className="font-medium text-zinc-800">{maskAccount(account, kind ?? "email")}</span>{" "}
              设置新密码
            </p>
            <FormField
              label="新密码"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              hint="至少 8 位，需同时包含字母和数字"
            />
            <FormField
              label="确认新密码"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />

            <div className="flex flex-col gap-2 sm:flex-row-reverse">
              <button
                type="submit"
                className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 sm:flex-1"
              >
                重置密码
              </button>
              <button
                type="button"
                onClick={() => {
                  setError("");
                  setStep(1);
                }}
                className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm text-zinc-700 transition hover:bg-zinc-50 sm:w-28"
              >
                上一步
              </button>
            </div>
          </form>
        ) : null}

        {step === 3 ? (
          <div className="text-center" aria-live="polite">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50">
              <svg
                viewBox="0 0 24 24"
                className="h-6 w-6 text-emerald-600"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M20 6L9 17l-5-5" />
              </svg>
            </div>
            <h2 className="text-sm font-medium text-zinc-900">密码已重置</h2>
            <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-zinc-500">
              请使用新密码重新登录。若账号在其他设备上保持登录，建议一并退出。
            </p>
            <Link
              to="/login"
              className="mt-5 block w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700"
            >
              返回登录
            </Link>
          </div>
        ) : null}

        <p className="mt-5 text-center text-xs text-zinc-500">
          想起来了？
          <Link to="/login" className="ml-1 text-blue-700 underline">
            返回登录
          </Link>
        </p>

        <p className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-900">
          界面预览：当前尚未接入验证码发送与密码重置接口，任何 6 位数字均可进入下一步。
        </p>
      </div>
    </div>
  );
}
