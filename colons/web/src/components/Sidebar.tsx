import { BotAvatar } from './BotAvatar'
import { Settings, X, Plus, Trash2, Users } from 'lucide-react'
import { Icon } from './Icon'
import { useState } from 'react'
import type { AvatarInfo, BotInfo, RoomInfo, SessionInfo } from '../lib/types'

export interface BotDraft {
  name: string
  title: string
  description: string
  persona: string
  avatar: string
  model: string
}

interface Props {
  view: 'dashboard' | 'chat'
  onDashboard: () => void
  onChat: () => void
  collapsed: boolean
  open: boolean
  onClose: () => void
  sessions: SessionInfo[]
  activeSession: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onOpenSettings: () => void
  bots: BotInfo[]
  activeBotId: string
  onSelectBot: (id: string) => void
  onCreateBot: () => void
  onDeleteBot: (id: string) => void
  rooms: RoomInfo[]
  activeRoomId: string | null
  onSelectRoom: (id: string) => void
  onCreateRoom: () => void
  avatars: AvatarInfo[]
  usage?: { total_tokens: number } | null
}

function Avatar({ bot, size = 30 }: { bot: BotInfo; size?: number }) {
  return <BotAvatar avatar={bot.avatar_def} size={size} title={`@${bot.id} — ${bot.title || bot.name}`} />
}

export function Sidebar({
  view, onDashboard, onChat, open, collapsed, onClose, sessions, activeSession, onSelect, onNew, onDelete, onOpenSettings,
  bots, activeBotId, onSelectBot, onCreateBot, onDeleteBot,
  rooms, activeRoomId, onSelectRoom, onCreateRoom, usage,
}: Props) {
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  return (
    <>
      {open && <div className="fixed inset-0 bg-black/60 z-30 md:hidden" onClick={onClose} />}
      <aside
        className={`fixed md:relative z-40 h-full w-[280px] bg-[#fafafa] border-r border-slate-200/70 flex flex-col
          transition-transform duration-200 ${open ? 'translate-x-0' : '-translate-x-full md:translate-x-0'} ${collapsed ? 'md:hidden' : 'md:shrink-0'}`}
      >
        <div className="p-3 flex items-center justify-between">
          <button
            onClick={onDashboard}
            className="flex items-center gap-2 hover:opacity-80 transition-opacity"
          >
            <span className="colons-mark small" aria-hidden="true"><i /><i /></span><span className="font-semibold text-xl tracking-tight">Colons</span>
          </button>
          <button onClick={onClose} aria-label="Collapse navigation" className="text-slate-500 hover:text-slate-800 p-1">
            <X size={18} />
          </button>
        </div>

        <nav aria-label="Main navigation" className="px-3 py-4 space-y-1 mb-2">
          <button aria-label="New chat" onClick={onNew} className="w-full flex items-center gap-3 text-left text-sm px-4 py-3 rounded-xl bg-[#eaf0f8] hover:bg-[#e0e9f5]"><Icon name="plus" /> New chat</button>
          <button aria-label="Dashboard" onClick={onDashboard} aria-current={view === 'dashboard' ? 'page' : undefined} className={`w-full flex items-center gap-3 text-left text-sm px-4 py-3 rounded-xl ${view === 'dashboard' ? 'bg-slate-100' : 'hover:bg-slate-100/70'}`}><Icon name="dashboard" /> Dashboard</button>
          <button aria-label="Conversations" onClick={onChat} aria-current={view === 'chat' ? 'page' : undefined} className="w-full flex items-center gap-3 text-left text-sm px-4 py-3 rounded-xl hover:bg-slate-100/70"><Icon name="history" /> Conversations</button>
        </nav>
        {/* Bot roster */}
        <div className="px-3 pb-2">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] uppercase tracking-wide text-slate-500">Bots</span>
            <button onClick={onCreateBot} className="text-slate-500 hover:text-slate-800 p-0.5" title="New bot">
              <Plus size={16} />
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {bots.map((bot) => (
              <div key={bot.id} className="relative group">
                <button
                  aria-label={`Chat with ${bot.name}`}
                  onClick={() => onSelectBot(bot.id)}
                  className={`rounded-full transition-all ${
                    activeBotId === bot.id
                      ? 'ring-2 ring-colons-accent ring-offset-2 ring-offset-[#fafafa]'
                      : 'opacity-60 hover:opacity-100'
                  }`}
                >
                  <Avatar bot={bot} />
                </button>
                {bot.id !== 'default' && (
                  <button
                    onClick={() => setConfirmDelete(bot.id)}
                    className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-red-600 text-white text-[10px]
                      items-center justify-center hidden group-hover:flex leading-none"
                    title={`Delete ${bot.name}`}
                  >
                    <X size={10} />
                  </button>
                )}
              </div>
            ))}
          </div>
          {confirmDelete && (
            <div className="mt-2 text-[11px] bg-red-50/40 border border-red-200/50 rounded-lg p-2">
              Delete <b>@{confirmDelete}</b> and its chats?{' '}
              <button
                className="text-red-700 underline"
                onClick={() => { onDeleteBot(confirmDelete); setConfirmDelete(null) }}
              >
                delete
              </button>{' '}
              <button className="text-slate-600 underline" onClick={() => setConfirmDelete(null)}>
                cancel
              </button>
            </div>
          )}
        </div>

        {/* Rooms */}
        <div className="px-3 pb-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] uppercase tracking-wide text-slate-500">Rooms</span>
            <button onClick={onCreateRoom} className="text-slate-500 hover:text-slate-800 p-0.5" title="New room (2-6 bots)">
              <Plus size={16} />
            </button>
          </div>
          {rooms.length === 0 && <div className="text-[11px] text-slate-500 px-1">No rooms yet</div>}
          {rooms.map((room) => (
            <button
              key={room.id}
              onClick={() => onSelectRoom(room.id)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-colors
                ${activeRoomId === room.id ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-100/50'}`}
            >
              <Users size={16} />
              <span className="flex-1 min-w-0 truncate text-[13px]">{room.name}</span>
              <span className="text-[10px] text-slate-500">{room.members.length}</span>
              {room.needs_user && <span className="w-2 h-2 rounded-full bg-amber-400" title="needs you" />}
            </button>
          ))}
        </div>

        {/* Chats */}
        <div className="flex-1 overflow-y-auto px-2 border-t border-slate-200/70 pt-2">
          <div className="px-2 text-[11px] uppercase tracking-wide text-slate-500 mb-1">Chats</div>
          {sessions.length === 0 && <div className="px-3 py-2 text-sm text-slate-500">No chats yet</div>}
          {sessions.map((s) => (
            <div key={s.id} className="group relative">
              <button
                onClick={() => { onSelect(s.id); onClose() }}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm truncate transition-colors
                  ${activeSession === s.id ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-100/50'}`}
              >
                {s.title || 'Untitled'}
              </button>
              <button
                onClick={() => onDelete(s.id)}
                className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-600 p-0.5 transition-opacity"
                title="Delete"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <div className="border-t border-slate-200/70 p-3 space-y-2">
          {usage && (
            <div className="text-[11px] text-slate-500 px-1">
              {usage.total_tokens.toLocaleString()} tokens used
            </div>
          )}
          <button
            onClick={onOpenSettings}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-slate-100/70 text-sm text-slate-700 transition-colors"
          >
            <Settings size={18} strokeWidth={1.6} />
            Settings
          </button>
        </div>
      </aside>
    </>
  )
}
