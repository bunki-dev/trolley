import { Fragment, type ReactNode } from "react";
import type { Card } from "../types";

// Renders the agent's plain-text reply: paragraphs, "- " lists, **bold**, and [[code]] product references.
function inline(text: string, products: Record<string, Card>, key: string): ReactNode[] {
  const parts = text.split(/(\[\[\d{6,14}\]\]|\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => {
    const code = p.match(/^\[\[(\d+)\]\]$/)?.[1];
    if (code) return <mark key={`${key}${i}`} className="ref">{products[code]?.name ?? code}</mark>;
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={`${key}${i}`}>{p.slice(2, -2)}</strong>;
    return <Fragment key={`${key}${i}`}>{p}</Fragment>;
  });
}

export function Reply({ text, products }: { text: string; products: Record<string, Card> }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      const items = list;
      blocks.push(
        <ul key={`l${blocks.length}`}>
          {items.map((li, i) => (
            <li key={i}>{inline(li, products, `li${blocks.length}-${i}-`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
    if (m) {
      list.push(m[1]);
      continue;
    }
    flush();
    if (line.trim()) blocks.push(<p key={`p${blocks.length}`}>{inline(line, products, `p${blocks.length}-`)}</p>);
  }
  flush();
  return <div className="reply">{blocks}</div>;
}
