import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkGfm from "remark-gfm";

type CitationNode = {
  type: string;
  value?: string;
  url?: string;
  children?: CitationNode[];
};

/**
 * 把正文里的 [1]、[2] 变成可点击的引用。
 *
 * 用 remark 插件而不是字符串替换：代码块（inlineCode / code）里的 [n]
 * 不是 text 节点，天然不会被改动；字符串替换则会把代码示例一起改坏。
 */
function remarkCitations() {
  return (tree: CitationNode) => {
    const walk = (node: CitationNode) => {
      if (!node.children) return;
      const next: CitationNode[] = [];
      for (const child of node.children) {
        if (child.type === "text" && typeof child.value === "string") {
          const parts = child.value.split(/(\[\d{1,2}\])/g);
          for (const part of parts) {
            if (!part) continue;
            const match = /^\[(\d{1,2})\]$/.exec(part);
            if (match) {
              next.push({
                type: "link",
                url: `#cite-${match[1]}`,
                children: [{ type: "text", value: part }],
              });
            } else {
              next.push({ type: "text", value: part });
            }
          }
        } else {
          walk(child);
          next.push(child);
        }
      }
      node.children = next;
    };
    walk(tree);
  };
}

type Props = {
  content: string;
  onCitationClick: (index: number) => void;
};

/** 面向阅读的排版：标题、列表、代码块、表格、引用块、链接都按知识库问答场景调过间距。 */
const BASE_CLASSES =
  "text-sm leading-relaxed text-zinc-900 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0";

/**
 * remarkCjkFriendly：中文里 `**加粗：**正文` 这种写法按 CommonMark 不算强调
 * （闭合符前是中文标点、后是汉字，不满足 right-flanking），不加这个插件会显示成字面 **。
 */
export function Markdown({ content, onCitationClick }: Props) {
  const components: Components = {
    h1: ({ children }) => (
      <h2 className="mb-2 mt-4 text-base font-semibold text-zinc-900">{children}</h2>
    ),
    h2: ({ children }) => (
      <h3 className="mb-2 mt-4 text-[15px] font-semibold text-zinc-900">{children}</h3>
    ),
    h3: ({ children }) => (
      <h4 className="mb-1.5 mt-3 text-sm font-semibold text-zinc-900">{children}</h4>
    ),
    h4: ({ children }) => (
      <h5 className="mb-1.5 mt-3 text-sm font-medium text-zinc-800">{children}</h5>
    ),
    p: ({ children }) => <p className="my-2 whitespace-pre-wrap break-words">{children}</p>,
    ul: ({ children }) => (
      <ul className="my-2 list-disc space-y-1 pl-5 marker:text-zinc-400">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-zinc-400">{children}</ol>
    ),
    li: ({ children }) => <li className="break-words">{children}</li>,
    strong: ({ children }) => (
      <strong className="font-semibold text-zinc-900">{children}</strong>
    ),
    em: ({ children }) => <em className="italic text-zinc-800">{children}</em>,
    del: ({ children }) => <del className="text-zinc-500">{children}</del>,
    a: ({ href, children }) => {
      if (href?.startsWith("#cite-")) {
        const index = href.replace("#cite-", "");
        return (
          <button
            type="button"
            onClick={() => onCitationClick(Number(index))}
            className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-blue-100 px-1 align-super text-[11px] font-medium text-blue-700 hover:bg-blue-200"
            aria-label={`查看第 ${index} 条引用原文`}
            title="查看引用原文"
          >
            {index}
          </button>
        );
      }
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-700 underline underline-offset-2 hover:text-blue-800"
        >
          {children}
        </a>
      );
    },
    pre: ({ children }) => (
      <pre className="my-3 overflow-x-auto rounded-lg bg-zinc-900 p-3 text-[13px] leading-relaxed text-zinc-100 [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-inherit">
        {children}
      </pre>
    ),
    code: ({ className, children }) => {
      // 语言标记在 fenced code 的 className 上；行内 code 没有
      const isBlock = typeof className === "string" && className.includes("language-");
      if (isBlock) {
        return <code className="font-mono">{children}</code>;
      }
      return (
        <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[13px] text-zinc-800">
          {children}
        </code>
      );
    },
    blockquote: ({ children }) => (
      <blockquote className="my-3 border-l-2 border-zinc-300 pl-3 text-zinc-600">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-4 border-zinc-200" />,
    table: ({ children }) => (
      <div className="my-3 overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-zinc-50">{children}</thead>,
    th: ({ children }) => (
      <th className="border border-zinc-200 px-2 py-1 text-left font-medium text-zinc-800">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="border border-zinc-200 px-2 py-1 align-top">{children}</td>
    ),
    img: ({ src, alt }) => (
      <img src={src} alt={alt ?? ""} className="my-3 max-w-full rounded-md" />
    ),
  };

  return (
    <div className={BASE_CLASSES}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkCitations]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
