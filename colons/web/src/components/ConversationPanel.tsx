import { X } from 'lucide-react'
import { BotAvatar } from './BotAvatar'
import type { BotInfo, Message, SessionInfo } from '../lib/types'

export function ConversationPanel({ bot, messages, session, status, onSettings, onClose }: {
  bot?: BotInfo; messages: Message[]; session?: SessionInfo; status: string; onSettings: () => void; onClose: () => void
}) {
  const calls = messages.flatMap((m) => m.toolCalls || [])
  return (
    <><div onClick={onClose} className="fixed inset-0 z-30 bg-slate-900/20 xl:hidden" />
    <aside aria-label="Conversation details" className="fixed right-0 top-0 bottom-0 z-40 xl:relative xl:z-auto flex w-64 shrink-0 border-l border-slate-200/70 bg-[#fafafa] p-6 flex-col gap-7 overflow-y-auto">
      <section className="border-b border-slate-200 pb-6"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Conversation</h2><button aria-label="Collapse conversation panel" onClick={onClose} className="p-1 text-slate-400 hover:text-slate-700"><X size={17} /></button></div><p className="text-xs text-slate-500 mt-2 break-words">{session?.title || 'A fresh start'}</p></section>
      <section><h3 className="text-xs font-semibold mb-3">Your agent</h3><div className="flex gap-3 items-center"><BotAvatar avatar={bot?.avatar_def} size={36} /><div><p className="text-sm font-medium">{bot?.name || 'Colons'}</p><p className="text-xs text-slate-500">{bot?.title || 'Always ready to help'}</p></div></div>{bot?.description && <p className="text-xs text-slate-500 mt-3 leading-relaxed">{bot.description}</p>}</section>
      <section><h3 className="text-xs font-semibold mb-3">At a glance</h3><dl className="text-xs space-y-3 text-slate-500"><div className="flex justify-between"><dt>Messages</dt><dd className="text-slate-700">{messages.filter((m) => !m.streaming || m.content).length}</dd></div><div className="flex justify-between"><dt>Tool calls</dt><dd className="text-slate-700">{calls.length}</dd></div><div className="flex justify-between gap-2"><dt>Status</dt><dd className="text-slate-700 text-right">{status === 'idle' ? 'Ready' : status}</dd></div></dl></section>
      {!!calls.length && <section><h3 className="text-xs font-semibold mb-3">Tools used</h3><div className="flex flex-wrap gap-2">{[...new Set(calls.map((c) => c.name))].map((tool) => <span key={tool} className="text-xs border border-slate-200 bg-white rounded-full px-3 py-1.5">{tool.replaceAll('_', ' ')}</span>)}</div></section>}
      <div className="mt-auto rounded-2xl p-4 bg-[#edf2f8]"><p className="text-sm font-medium mb-2">Think further together.</p><p className="text-xs text-slate-500 leading-relaxed">Give your agent a voice, a role, and the tools to help you make progress.</p><button className="text-xs text-[#58749b] mt-3 hover:underline" onClick={onSettings}>Customize Colons →</button></div>
    </aside></>
  )
}
