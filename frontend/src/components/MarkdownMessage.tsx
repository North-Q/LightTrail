/** 助手文本的安全 Markdown/GFM 渲染；外部图片不自动加载。 */

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownMessageProps {
  content: string;
}

export function MarkdownMessage({ content }: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ children, href }) => href
            ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
            : <span>{children}</span>,
          img: ({ alt }) => <span>{alt || "图片"}（外部图片未自动加载）</span>,
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}
