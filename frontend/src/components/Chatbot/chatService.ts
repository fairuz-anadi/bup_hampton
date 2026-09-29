/**
 * Frontend client service for FuelGuard AI Chatbot.
 */

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  source?: 'llm' | 'knowledge_base' | 'system' | 'error';
}

export interface ChatResponse {
  message: string;
  conversation_id: string;
  timestamp: string;
  source: 'llm' | 'knowledge_base' | 'system' | 'error';
  suggested_prompts: string[];
  live_facts: string[];
}

const STORAGE_KEY = 'fuelguard.chatbot.conversation_id';
const LOCAL_HISTORY_KEY = 'fuelguard.chatbot.local_history';

export const chatService = {
  getStoredConversationId(): string | null {
    try {
      return sessionStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  },

  setStoredConversationId(id: string): void {
    try {
      sessionStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* ignore */
    }
  },

  getStoredMessages(): ChatMessage[] {
    try {
      const data = sessionStorage.getItem(LOCAL_HISTORY_KEY);
      return data ? JSON.parse(data) : [];
    } catch {
      return [];
    }
  },

  setStoredMessages(messages: ChatMessage[]): void {
    try {
      sessionStorage.setItem(LOCAL_HISTORY_KEY, JSON.stringify(messages));
    } catch {
      /* ignore */
    }
  },

  async sendMessage(message: string, conversationId?: string | null): Promise<ChatResponse> {
    const activeConvId = conversationId ?? this.getStoredConversationId() ?? undefined;
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({
        message,
        conversation_id: activeConvId,
      }),
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => null);
      const errorMsg = errorData?.detail || `Server responded with HTTP ${res.status}`;
      throw new Error(errorMsg);
    }

    const data: ChatResponse = await res.json();
    if (data.conversation_id) {
      this.setStoredConversationId(data.conversation_id);
    }
    return data;
  },

  async getHistory(conversationId: string): Promise<ChatMessage[]> {
    try {
      const res = await fetch(`/api/chat/history/${encodeURIComponent(conversationId)}`);
      if (!res.ok) return [];
      const data = await res.json();
      return data.messages || [];
    } catch {
      return [];
    }
  },

  async clearHistory(conversationId?: string | null): Promise<void> {
    const id = conversationId ?? this.getStoredConversationId();
    if (id) {
      try {
        await fetch(`/api/chat/clear?conversation_id=${encodeURIComponent(id)}`, {
          method: 'POST',
        });
      } catch {
        /* ignore */
      }
    }
    try {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionStorage.removeItem(LOCAL_HISTORY_KEY);
    } catch {
      /* ignore */
    }
  },

  async getStarterPrompts(): Promise<string[]> {
    try {
      const res = await fetch('/api/chat/prompts');
      if (!res.ok) throw new Error('Prompts unavailable');
      const data = await res.json();
      return data.prompts || [];
    } catch {
      return [
        'What is the current network status and simulation tick?',
        'Explain the latest recommendation and proposed shipments',
        'Are any stations at risk of fuel stockout?',
        'How does the Autonomy Gate decide when to require human review?',
      ];
    }
  },
};
