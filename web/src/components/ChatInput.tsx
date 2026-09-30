/**
 * ChatInput Component - ChatGPT-like input with streaming support
 */
import React, { useState, useRef, useCallback } from 'react';
import { Send, StopCircle, Paperclip, Plus, Square } from 'lucide-react';
import { cn } from '../utils/cn';

interface ChatInputProps {
  onSend: (message: string, stream: boolean) => void;
  onStop: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  placeholder?: string;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSend,
  onStop,
  disabled = false,
  isStreaming = false,
  placeholder = "Type a message...",
}) => {
  const [message, setMessage] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(() => {
    if (!message.trim() || disabled) return;
    onSend(message.trim(), !isStreaming);
    setMessage('');
    inputRef.current?.focus();
  }, [message, onSend, disabled, isStreaming]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleStop = useCallback(() => {
    onStop();
  }, [onStop]);

  return (
    <div className="w-full max-w-3xl mx-auto mb-4">
      <div
        className={cn(
          'relative bg-secondary rounded-2xl border-2 border-transparent',
          'focus-within:border-primary/50 focus-within:shadow-lg',
          disabled && 'opacity-50 pointer-events-none'
        )}
      >
        <textarea
          ref={inputRef}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          className={cn(
            'w-full bg-transparent px-4 py-3 pr-12 resize-none focus:outline-none',
            'text-primary dark:text-secondary',
            'placeholder:text-secondary'
          )}
          style={{ maxHeight: '200px', minHeight: '52px' }}
        />

        <div className="absolute right-3 bottom-3 flex items-center gap-2">
          <button
            type="button"
            className="p-1 text-secondary hover:text-primary transition-colors"
            title="Attach file"
          >
            <Paperclip size={18} />
          </button>

          {isStreaming ? (
            <button
              type="button"
              onClick={handleStop}
              className="p-1 text-red-500 hover:text-red-600 transition-colors"
              title="Stop generation"
            >
              <Square size={18} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!message.trim()}
              className={cn(
                'p-1 rounded-full transition-colors',
                message.trim()
                  ? 'bg-primary text-white hover:bg-primary/90'
                  : 'bg-secondary text-secondary'
              )}
              title="Send message"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center justify-center mt-2 gap-4 text-xs text-secondary">
        <span>Shift + Enter for new line</span>
        <span>•</span>
        <span>Enter to send</span>
      </div>
    </div>
  );
};