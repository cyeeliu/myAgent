"use client";
import { useEffect, useState, type ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import { createHighlighter, type Highlighter } from "shiki";

// Lazy singleton Shiki highlighter. Created once on first code block, reused
// across all blocks. Resolves to null if the bundle/themes can't load (e.g. in
// the jsdom test env) — CodeBlock then falls back to a plain <pre>.
let hlPromise: Promise<Highlighter | null> | null = null;
function getHighlighter(): Promise<Highlighter | null> {
  if (hlPromise) return hlPromise;
  hlPromise = createHighlighter({
    themes: ["github-light"],
    langs: [
      "javascript", "typescript", "jsx", "tsx",
      "python", "bash", "shell", "json", "yaml", "markdown",
      "css", "html", "go", "rust", "sql", "diff",
    ],
  }).catch(() => null);
  return hlPromise;
}

const LANG_ALIASES: Record<string, string> = {
  ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx",
  py: "python", sh: "bash", shell: "shell", zsh: "bash",
  yml: "yaml", md: "markdown", plaintext: "text", text: "text",
};
function mapLang(lang: string): string {
  const l = (lang || "").toLowerCase();
  return LANG_ALIASES[l] || l || "text";
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getHighlighter().then((hl) => {
      if (cancelled || !hl) return;
      try {
        setHtml(hl.codeToHtml(code, { lang: mapLang(lang), theme: "github-light" }));
      } catch { /* keep fallback */ }
    });
    return () => { cancelled = true; };
  }, [code, lang]);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch { /* clipboard blocked — ignore */ }
  };

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-paper-300 bg-paper-150">
      <div className="flex items-center justify-between border-b border-paper-300/70 px-3.5 py-1.5 text-[11px] text-paper-600">
        <span className="font-mono">{lang || "text"}</span>
        <button onClick={copy} className="font-medium transition hover:text-paper-900">
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <div className="overflow-x-auto p-3.5 text-[13px] leading-relaxed">
        {html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="font-mono text-paper-800"><code>{code}</code></pre>
        )}
      </div>
    </div>
  );
}

// Render assistant text as markdown with syntax-highlighted code blocks.
// react-markdown v9: `pre` is a pass-through (the `code` handler builds the
// block), and `code` without a language class is inline.
export function Markdown({ content }: { content: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        components={{
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children, ...rest }: ComponentProps<"code">) => {
            const match = /language-(\w+)/.exec(className || "");
            const text = String(children ?? "").replace(/\n$/, "");
            if (match) return <CodeBlock code={text} lang={match[1]} />;
            return <code {...rest}>{children}</code>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
