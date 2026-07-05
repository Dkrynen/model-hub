import { cn } from "@/lib/utils";

/** Tiny, dependency-free markdown renderer tuned for chat output. */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = renderBlocks(text);
  return <div className={cn("prose-chat", className)}>{blocks}</div>;
}

function renderBlocks(src: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const parts = src.split(/(```[\s\S]*?```)/g);
  parts.forEach((part, i) => {
    if (part.startsWith("```")) {
      const m = part.match(/^```(\w+)?\s*\n([\s\S]*?)```$/);
      const lang = m?.[1] || "";
      const code = m?.[2] ?? part.replace(/^```\w*\s*/, "").replace(/```$/, "");
      nodes.push(
        <pre key={`c${i}`} className={cn("overflow-x-auto")}>
          <code data-lang={lang}>{code.replace(/\n$/, "")}</code>
        </pre>
      );
    } else {
      part.split(/\n{2,}/).forEach((para, j) => {
        const t = para.trim();
        if (!t) return;
        nodes.push(<Paragraph key={`p${i}-${j}`} text={t} />);
      });
    }
  });
  return nodes;
}

function Paragraph({ text }: { text: string }) {
  const lines = text.split("\n");
  // Heading?
  const h = text.match(/^(#{1,3})\s+(.*)$/);
  if (h && lines.length === 1) {
    const level = h[1].length;
    const Tag = (`h${level}` as "h1" | "h2" | "h3");
    return <Tag>{inline(h[2])}</Tag>;
  }
  // List?
  const items = lines.filter((l) => /^\s*([-*]|\d+\.)\s+/.test(l));
  if (items.length >= 1 && items.length === lines.filter((l) => l.trim()).length) {
    const ordered = /^\s*\d+\.\s+/.test(items[0]);
    const Tag = ordered ? "ol" : "ul";
    return (
      <Tag>
        {items.map((l, i) => (
          <li key={i}>{inline(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>
        ))}
      </Tag>
    );
  }
  return <p>{inline(text)}</p>;
}

/** Render inline markdown to React nodes (bold, italic, code, links). */
function inline(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const regex = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = regex.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2]) out.push(<strong key={k++}>{m[2]}</strong>);
    else if (m[3]) out.push(<em key={k++}>{m[3]}</em>);
    else if (m[4]) out.push(<code key={k++}>{m[4]}</code>);
    else if (m[5] && m[6])
      out.push(
        <a key={k++} href={m[6]} target="_blank" rel="noreferrer">
          {m[5]}
        </a>
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length ? out : [text];
}
