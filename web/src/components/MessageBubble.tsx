/**
 * Message Bubble Component - ChatGPT-like message display
 */
import React from 'react';
import { cn } from '../utils/cn';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  isLoading?: boolean;
  isError?: boolean;
}

interface MessageBubbleProps {
  message: Message;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const isError = message.isError;
  const isLoading = message.isLoading;

  return (
    <div
      className={cn(
        "w-full max-w-3xl mx-auto px-4 py-2 animate-fadeIn",
        isUser ? 'text-right' : 'text-left'
      )}
    >
      <div
        className={cn(
          'inline-block max-w-[85%] rounded-xl px-4 py-3 transition-shadow',
          isUser
            ? 'bg-primary text-white rounded-r-xl'
            : isError
            ? 'bg-red-500/20 text-red-600 dark:bg-red-500/30'
            : 'bg-secondary text-primary border border-border rounded-l-xl'
        )}
      >
        {isLoading ? (
          <TypingIndicator />
        ) : (
          <MessageContent content={message.content} isCode={message.content.includes('\n')} />
        )}
        <div className="text-xs text-secondary mt-1 opacity-70">
          {formatTime(message.timestamp)}
        </div>
      </div>
    </div>
  );
};

const TypingIndicator: React.FC = () => {
  return (
    <div className="typing-indicator flex space-x-1 justify-center items-center">
      <span className="w-1 h-4 bg-secondary dark:bg-tertiary rounded-full" />
      <span className="w-1 h-4 bg-secondary dark:bg-tertiary rounded-full" />
      <span className="w-1 h-4 bg-secondary dark:bg-tertiary rounded-full" />
    </div>
  );
};

const MessageContent: React.FC<{ content: string; isCode: boolean }> = ({ content, isCode }) => {
  if (isCode) {
    return (
      <pre className="whitespace-pre-wrap font-mono text-sm bg-secondary/50 dark:bg-secondary/50 rounded-lg p-2 overflow-x-auto">
        <code>{content}</code>
      </pre>
    );
  }

  // Parse markdown-like content
  const parts = content.split(/```(\w+)?\n([\s\S]*?)```/g).flatMap((part, i) => {
    if (i % 3 === 1) {
      // Code block
      const lang = part;
      const code = arguments[i + 2];
      return [
        <code key={i} className="bg-secondary/50 dark:bg-secondary/50 rounded px-1 font-mono text-sm">
          {code}
        </code>
      ];
    } else if (i % 3 === 2) {
      // Code content
      return [
        <pre key={i} className="whitespace-pre-wrap font-mono text-sm bg-secondary/50 dark:bg-secondary/50 rounded-lg p-2 overflow-x-auto">
          <code>{part}</code>
        </pre>
      ];
    } else {
      // Regular text
      // Handle line breaks
      return part.split('\n').map((line, j) => (
        <React.Fragment key={i * 3 + j}>
          {line}
          <br />
        </React.Fragment>
      ));
    }
  });

  return <div className="whitespace-pre-wrap">{parts}</div>;
};

const formatTime = (date: Date): string => {
  const now = new Date();
  const diffMinutes = Math.floor((now.getTime() - date.getTime()) / 60000);

  if (diffMinutes < 1) return 'now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffMinutes < 1440) return `${Math.floor(diffMinutes / 60)}h ago`;
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};