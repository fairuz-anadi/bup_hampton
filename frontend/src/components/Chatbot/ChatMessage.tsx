import { useState, type ReactNode } from 'react';
import type { ChatMessage as ChatMessageType } from './chatService';

interface ChatMessageProps {
  message: ChatMessageType;
  onRetry?: (text: string) => void;
}

export function ChatMessage({ message, onRetry }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const isError = message.source === 'error';

  return (
    <div className={`fg-msg-row ${isUser ? 'user' : 'assistant'}`}>
      {!isUser && (
        <div className="fg-avatar" title="FuelGuard AI">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2a4 4 0 0 1 4 4v2h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2V6a4 4 0 0 1 4-4z" />
            <circle cx="9" cy="13" r="1" fill="currentColor" />
            <circle cx="15" cy="13" r="1" fill="currentColor" />
            <path d="M10 17h4" />
          </svg>
        </div>
      )}

      <div className={`fg-bubble ${isUser ? 'user' : 'assistant'} ${isError ? 'error' : ''}`}>
        <div className="fg-msg-content">
          {renderFormattedText(message.content)}
        </div>

        <div className="fg-msg-meta">
          <span className="fg-time">{message.timestamp}</span>
          {!isUser && message.source && (
            <span className={`fg-source-tag ${message.source}`}>
              {message.source === 'llm'
                ? 'AI Live'
                : message.source === 'knowledge_base'
                ? 'Knowledge Base'
                : message.source}
            </span>
          )}
          {isError && onRetry && (
            <button
              type="button"
              className="fg-retry-btn"
              onClick={() => onRetry(message.content)}
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="fg-code-block">
      <div className="fg-code-header">
        <span className="fg-code-lang">{lang || 'text'}</span>
        <button type="button" className="fg-copy-btn" onClick={handleCopy}>
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

function renderFormattedText(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const lines = text.split('\n');
  let inCodeBlock = false;
  let codeBuffer: string[] = [];
  let codeLang = '';

  lines.forEach((line, index) => {
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        nodes.push(
          <CodeBlock
            key={`code-${index}`}
            code={codeBuffer.join('\n')}
            lang={codeLang}
          />
        );
        codeBuffer = [];
        inCodeBlock = false;
        codeLang = '';
      } else {
        inCodeBlock = true;
        codeLang = line.trim().replace(/^```/, '').trim();
      }
      return;
    }

    if (inCodeBlock) {
      codeBuffer.push(line);
      return;
    }

    const trimmed = line.trim();
    if (!trimmed) {
      nodes.push(<div key={`sp-${index}`} className="fg-msg-space" />);
      return;
    }

    // Heading ###
    if (line.startsWith('### ')) {
      nodes.push(
        <h4 key={`h-${index}`} className="fg-msg-h">
          {formatInline(line.replace(/^### /, ''))}
        </h4>
      );
      return;
    }

    // Bullet line
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      nodes.push(
        <div key={`li-${index}`} className="fg-msg-bullet">
          <span className="fg-bullet-dot" />
          <span>{formatInline(trimmed.replace(/^[-*]\s+/, ''))}</span>
        </div>
      );
      return;
    }

    nodes.push(
      <p key={`p-${index}`} className="fg-msg-p">
        {formatInline(line)}
      </p>
    );
  });

  if (inCodeBlock && codeBuffer.length > 0) {
    nodes.push(
      <CodeBlock
        key="code-end"
        code={codeBuffer.join('\n')}
        lang={codeLang}
      />
    );
  }

  return nodes;
}

function formatInline(text: string): ReactNode {
  // Regex to split on bold **...** and inline code `...`
  const parts = text.split(/(\*\*.*?\*\*|`.*?`)/g);

  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} className="fg-inline-code">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}
