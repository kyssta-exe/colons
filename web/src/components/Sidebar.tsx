/**
 * Sidebar Component - ChatGPT-like sidebar
 */
import React from 'react';
import { Plus, Settings, LogOut } from 'lucide-react';
import { cn } from '../utils/cn';

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
  onNewChat: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ isOpen, onClose, onNewChat }) => {
  return (
    <>
      {/* Overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          'fixed lg:relative inset-y-0 left-0 z-50',
          'w-64 bg-secondary border-r border-border',
          'flex flex-col',
          'transform transition-transform duration-300',
          isOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h1 className="text-xl font-semibold">Colons</h1>
          <button
            onClick={onClose}
            className="lg:hidden p-1 rounded hover:bg-tertiary"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* New Chat Button */}
        <button
          onClick={onNewChat}
          className="m-4 p-3 bg-primary/10 hover:bg-primary/20 rounded-lg border border-border transition-colors flex items-center gap-3"
        >
          <Plus size={18} />
          <span className="text-sm font-medium">New Chat</span>
        </button>

        {/* Chat History */}
        <div className="flex-1 overflow-y-auto px-2">
          <p className="px-4 py-2 text-xs text-secondary uppercase tracking-wide">
            Recent Chats
          </p>
          {/* Chat history items would go here */}
          <div className="space-y-1">
            <button className="w-full text-left p-2 rounded-lg hover:bg-tertiary transition-colors text-sm">
              No chats yet
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-border p-2">
          <button className="w-full flex items-center gap-3 p-2 rounded-lg hover:bg-tertiary transition-colors text-sm">
            <Settings size={18} />
            <span>Settings</span>
          </button>
        </div>
      </aside>
    </>
  );
};