import type { Citation } from "../lib/sse";

type Props = {
  citations: Citation[];
  activeIndex: number | null;
  onSelect: (index: number) => void;
};

export function CitationPanel({ citations, activeIndex, onSelect }: Props) {
  if (citations.length === 0) {
    return (
      <div className="p-4 text-sm text-zinc-500">
        回答中的引用会显示在这里，点击可定位到原文页码。
      </div>
    );
  }

  return (
    <div className="space-y-3 p-4">
      <h2 className="text-sm font-medium text-zinc-700">引用来源</h2>
      {citations.map((citation) => {
        const active = activeIndex === citation.index;
        return (
          <button
            type="button"
            key={citation.index}
            onClick={() => onSelect(citation.index)}
            className={`block w-full rounded-md border p-3 text-left transition ${
              active ? "border-blue-500 bg-blue-50" : "border-zinc-200 bg-white hover:border-zinc-300"
            }`}
          >
            <div className="flex items-center gap-2 text-sm text-zinc-900">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-zinc-200 text-xs text-zinc-700">
                {citation.index}
              </span>
              <span className="truncate font-medium">{citation.title}</span>
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              {citation.heading_path ?? "正文"} · 第 {citation.page_from} 页
            </p>
          </button>
        );
      })}
    </div>
  );
}
