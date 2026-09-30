/**
 * Main Chat Page - ChatGPT-like interface
 * Responsive design for desktop and mobile
 */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { MessageBubble, Message } from './MessageBubble';
import { ChatInput } from './ChatInput';
import { Sidebar } from './Sidebar';
import { api } from '../utils/api';
import { cn } from '../utils/cn';

export const ChatPage: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const handleSend = useCallback(async (content: string, stream: boolean) => {
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content,
      timestamp: new Date(),
    };

    const assistantMessage: Message = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isLoading: true,
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(stream);
    setError(null);

    try {
      if (stream) {
        await streamMessage(content, assistantMessage.id);
      } else {
        await sendNonStreaming(content, assistantMessage.id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessage.id
            ? { ...msg, isLoading: false, isError: true }
            : msg
        )
      );
    } finally {
      setIsStreaming(false);
    }
  }, []);

  const streamMessage = async (content: string, messageId: string) => {
    const response = await fetch('/ws/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: content, stream: true }),
    });

    if (!response.ok) throw new Error('Failed to stream message');

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let fullContent = '';

    if (reader) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        // Parse SSE chunks here
        fullContent += chunk;

        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === messageId ? { ...msg, content: fullContent } : msg
          )
        );
      }
    }

    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === messageId ? { ...msg, isLoading: false } : msg
      )
    );
  };

  const sendNonStreaming = async (content: string, messageId: string) => {
    const response = await api.post('/chat', { message: content, stream: false });
    const data = response.data;

    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === messageId
          ? { ...msg, content: data.content, isLoading: false }
          : msg
      )
    );
  };

  const handleStop = useCallback(() => {
    setIsStreaming(false);
  }, []);

  const handleNewChat = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return (
    <div className="flex h-screen w-screen bg-secondary">
      {/* Sidebar */}
      <Sidebar
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewChat={handleNewChat}
      />

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile Header */}
        <header className="lg:hidden flex items-center justify-between px-4 py-3 border-b border-border bg-secondary">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 rounded-lg hover:bg-tertiary transition-colors"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 12h18M3 6h18M3 18h18" />
            </svg>
          </button>
          <h1 className="font-semibold text-lg">Colons</h1>
          <div />
        </header>

        {/* Messages */}
        <main className="flex-1 overflow-y-auto py-6">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full px-4">
              <h2 className="text-2xl font-semibold mb-6">Colons</h2>
              <p className="text-secondary mb-8">
                What can I help you with today?
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl w-full">
                <Suggestion
                  icon="💻"
                  text="Build a REST API with Python"
                  onClick={(text) => handleSend(text, true)}
                />
                <Suggestion
                  icon="📊"
                  text="Analyze this data for trends"
                  onClick={(text) => handleSend(text, true)}
                />
                <Suggestion
                  icon="✍️"
                  text="Write a blog post about AI"
                  onClick={(text) => handleSend(text, true)}
                />
                <Suggestion
                  icon="🔧"
                  text="Debug this code for me"
                  onClick={(text) => handleSend(text, true)}
                />
              </div>
            </div>
          ) : (
            <div className="space-y-4 px-4">
              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </main>

        {/* Error Banner */}
        {error && (
          <div className="bg-red-500/10 border-b border-red-500/20 px-4 py-2">
            <p className="text-red-500 text-sm text-center">{error}</p>
          </div>
        )}

        {/* Input */}
        <ChatInput
          onSend={handleSend}
          onStop={handleStop}
          disabled={isStreaming}
          isStreaming={isStreaming}
        />
      </div>
    </div>
  );
};

const Suggestion: React.FC<{
  icon: string;
  text: string;
  onClick: (text: string) => void;
}> = ({ icon, text, onClick }) => {
  return (
    <button
      onClick={() => onClick(text)}
      className="text-left p-3 bg-primary/10 hover:bg-primary/20 rounded-lg border border-border transition-colors"
    >
      <span className="text-lg">{icon}</span>
      <p className="text-sm mt-1">{text}</p>
    </button>
  );
};