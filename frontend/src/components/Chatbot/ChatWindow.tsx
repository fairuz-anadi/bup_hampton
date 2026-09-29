import { useState, useEffect, useRef, useCallback } from 'react';
import { chatService, type ChatMessage as ChatMessageType } from './chatService';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';

interface ChatWindowProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ChatWindow({ isOpen, onClose }: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [isBusy, setIsBusy] = useState(false);
  const [prompts, setPrompts] = useState<string[]>([]);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // Load starter prompts and stored messages on mount
  useEffect(() => {
    chatService.getStarterPrompts().then(setPrompts).catch(() => {});
    const stored = chatService.getStoredMessages();
    if (stored.length > 0) {
      setMessages(stored);
    }
  }, []);

  // Save messages to session storage on update
  useEffect(() => {
    if (messages.length > 0) {
      chatService.setStoredMessages(messages);
    }
  }, [messages]);

  const scrollToBottom = useCallback((smooth = true) => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({
        behavior: smooth ? 'smooth' : 'auto',
      });
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      scrollToBottom(false);
    }
  }, [isOpen, messages, scrollToBottom]);

  const handleScroll = () => {
    const el = scrollAreaRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    setShowScrollBottom(!isAtBottom);
  };

  const handleSend = async (text: string) => {
    if (isBusy || !text.trim()) return;

    const userMessage: ChatMessageType = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text.trim(),
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      source: 'system',
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsBusy(true);

    try {
      const res = await chatService.sendMessage(text);
      const botMessage: ChatMessageType = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: res.message,
        timestamp: res.timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        source: res.source,
      };
      setMessages((prev) => [...prev, botMessage]);
      if (res.suggested_prompts && res.suggested_prompts.length > 0) {
        setPrompts(res.suggested_prompts);
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : 'Failed to reach AI service';
      const errorMessage: ChatMessageType = {
        id: `e-${Date.now()}`,
        role: 'assistant',
        content: `**Error:** ${errMsg}. Please try again or check the System Health page.`,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        source: 'error',
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsBusy(false);
    }
  };

  const handleClear = async () => {
    await chatService.clearHistory();
    setMessages([]);
    chatService.getStarterPrompts().then(setPrompts).catch(() => {});
  };

  if (!isOpen) return null;

  return (
    <div className="fg-chat-window" role="dialog" aria-label="FuelGuard AI Chatbot">
      {/* Header */}
      <div className="fg-chat-header">
        <div className="fg-header-title">
          <div className="fg-bot-avatar">
            <span className="fg-bot-pulse" />
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2a4 4 0 0 1 4 4v2h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2V6a4 4 0 0 1 4-4z" />
              <circle cx="9" cy="13" r="1" fill="currentColor" />
              <circle cx="15" cy="13" r="1" fill="currentColor" />
              <path d="M10 17h4" />
            </svg>
          </div>
          <div>
            <div className="fg-header-name">FuelGuard AI</div>
            <div className="fg-header-sub">Operational Decision Copilot</div>
          </div>
        </div>

        <div className="fg-header-actions">
          <button
            type="button"
            className="fg-action-btn"
            onClick={handleClear}
            title="Clear conversation"
            aria-label="Clear conversation"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
          <button
            type="button"
            className="fg-action-btn close"
            onClick={onClose}
            title="Close chat"
            aria-label="Close chat"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* Suggested starter prompts (shown when conversation is new or idle) */}
      {messages.length === 0 && (
        <div className="fg-welcome-banner">
          <p className="fg-welcome-text">
            Ask me about fuel stockout risks, route disruptions, Decision Twin projections, or active simulation events.
          </p>
          <div className="fg-prompt-chips">
            {prompts.map((p, i) => (
              <button
                key={i}
                type="button"
                className="fg-chip"
                onClick={() => handleSend(p)}
                disabled={isBusy}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Message scroll area */}
      <div
        className="fg-chat-messages"
        ref={scrollAreaRef}
        onScroll={handleScroll}
      >
        {messages.map((msg) => (
          <ChatMessage
            key={msg.id}
            message={msg}
            onRetry={(text) => handleSend(text)}
          />
        ))}

        {isBusy && (
          <div className="fg-msg-row assistant">
            <div className="fg-avatar">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2a4 4 0 0 1 4 4v2h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2V6a4 4 0 0 1 4-4z" />
              </svg>
            </div>
            <div className="fg-bubble assistant loading">
              <div className="fg-typing-indicator">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Scroll to bottom button */}
      {showScrollBottom && (
        <button
          type="button"
          className="fg-scroll-bottom-btn"
          onClick={() => scrollToBottom(true)}
          aria-label="Scroll to bottom"
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      )}

      {/* Input bar */}
      <div className="fg-chat-footer">
        <ChatInput onSend={handleSend} disabled={isBusy} />
        <div className="fg-footer-note">
          Simulated network data · Press <kbd>Enter</kbd> to send
        </div>
      </div>
    </div>
  );
}
