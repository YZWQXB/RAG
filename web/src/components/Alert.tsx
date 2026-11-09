type Props = {
  tone?: "info" | "warning" | "error" | "success";
  children: React.ReactNode;
  action?: React.ReactNode;
};

const TONES: Record<string, string> = {
  info: "border-blue-200 bg-blue-50 text-blue-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  error: "border-red-200 bg-red-50 text-red-900",
  success: "border-green-200 bg-green-50 text-green-900",
};

export function Alert({ tone = "info", children, action }: Props) {
  return (
    <div
      role="alert"
      className={`mb-4 flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-sm ${TONES[tone]}`}
    >
      <span>{children}</span>
      {action}
    </div>
  );
}
