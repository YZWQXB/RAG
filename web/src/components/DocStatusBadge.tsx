type Props = {
  status: string;
  stage?: string | null;
};

const STAGE_LABEL: Record<string, string> = {
  parsing: "解析中",
  chunking: "切分中",
  embedding: "向量化中",
};

export function DocStatusBadge({ status, stage }: Props) {
  if (status === "ready") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-green-700">
        <span className="h-2 w-2 rounded-full bg-green-600" />
        可检索
      </span>
    );
  }

  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-red-700">
        <span className="h-2 w-2 rounded-full bg-red-600" />
        失败
      </span>
    );
  }

  if (status === "processing") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-blue-700">
        <span className="dot-running h-2 w-2 rounded-full bg-blue-600" />
        {STAGE_LABEL[stage ?? ""] ?? "处理中"}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-zinc-600">
      <span className="h-2 w-2 rounded-full bg-zinc-400" />
      排队中
    </span>
  );
}
