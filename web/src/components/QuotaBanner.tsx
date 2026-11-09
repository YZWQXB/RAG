import { Link } from "react-router-dom";

type Props = {
  message: string;
  limit?: number;
  used?: number;
};

export function QuotaBanner({ message, limit, used }: Props) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
      <span>
        {message}
        {limit !== undefined && used !== undefined ? `（${used}/${limit}）` : ""}
      </span>
      <Link to="/quota" className="shrink-0 underline">
        了解套餐
      </Link>
    </div>
  );
}
