import { BotAvatar } from './BotAvatar'
import { Volume2, FileText } from 'lucide-react'
import type { AvatarInfo, Message } from '../lib/types'
import { Markdown } from './Markdown'
import { ToolCallCard } from './ToolCallCard'

interface Props {
  message: Message
  avatar?: AvatarInfo
  onSpeak?: (text: string) => void
  speaking?: boolean
  onApprove?: (id: string, approved: boolean) => void
}

export function MessageBubble({ message, avatar, onSpeak, speaking, onApprove }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end px-3 md:px-0 animate-fade-in">
        <div className="max-w-[85%] md:max-w-[70%] bg-[#edf2f8] rounded-2xl px-5 py-3.5 text-[15px] whitespace-pre-wrap">
          {!!message.attachments?.length && <div className="flex flex-wrap gap-2 mb-2">{message.attachments.map((file, i) => <div key={i} className="bg-white/80 border border-slate-200 rounded-xl p-2 text-xs">{file.data_url ? <img src={file.data_url} alt={file.name} className="max-w-[200px] max-h-[140px] object-contain rounded-lg mb-1" /> : <FileText size={16} className="mb-1" />}<span>{file.name}</span></div>)}</div>}
          {message.content}
        </div>
      </div>
    )
  }

  const thinking = message.streaming && !message.content && (!message.toolCalls || message.toolCalls.length === 0)
  const waiting = message.approvals?.some((approval) => approval.decision === undefined)

  return (
    <div className="flex gap-3 px-3 md:px-0 animate-fade-in">
      <div className="shrink-0 mt-0.5">
        <BotAvatar avatar={avatar} size={32} state={speaking ? 'speaking' : message.streaming ? waiting ? 'waiting' : thinking ? 'thinking' : 'working' : 'resting'} />
      </div>

      <div className="flex-1 min-w-0 bg-white border border-slate-200/80 rounded-2xl p-5 shadow-[0_2px_10px_rgba(25,40,65,0.02)]">
        {(message.toolCalls || []).map((call) => (
          <ToolCallCard
            key={call.id}
            call={call}
            result={message.toolResults?.find((r) => r.id === call.id)}
          />
        ))}

        {message.approvals?.map((approval) => <div key={approval.id} className="border border-amber-200 bg-amber-50 rounded-xl p-3 my-3 text-xs">
          <p className="font-medium">Allow {approval.tool.replaceAll('_', ' ')}?</p>
          <pre className="mt-2 whitespace-pre-wrap break-all text-slate-600">{JSON.stringify(approval.arguments, null, 2)}</pre>
          {approval.decision !== undefined ? <p className="mt-2">{approval.decision ? 'Approved' : 'Declined'}</p> : message.streaming && onApprove ? <div className="flex gap-2 mt-3"><button className="bg-slate-900 text-white px-3 py-1.5 rounded-lg" onClick={() => onApprove(approval.id, true)}>Allow once</button><button className="border border-slate-300 px-3 py-1.5 rounded-lg" onClick={() => onApprove(approval.id, false)}>Decline</button></div> : <p className="mt-2">Approval expired</p>}
        </div>)}

        {message.reasoning && (
          <details className="mb-2 text-xs text-slate-500">
            <summary className="cursor-pointer select-none">reasoning</summary>
            <div className="mt-1 whitespace-pre-wrap border-l-2 border-slate-200 pl-2">{message.reasoning}</div>
          </details>
        )}

        {thinking ? (
          <div className="flex items-center gap-1.5 py-1.5">
            <span className="w-1.5 h-4 bg-slate-400 rounded animate-pulse-dot" />
            <span className="w-1.5 h-4 bg-slate-400 rounded animate-pulse-dot" style={{ animationDelay: '0.2s' }} />
            <span className="w-1.5 h-4 bg-slate-400 rounded animate-pulse-dot" style={{ animationDelay: '0.4s' }} />
          </div>
        ) : (
          message.content && <Markdown content={message.content} />
        )}

        {message.error && (
          <div className="mt-2 text-xs text-red-600 bg-red-50/30 border border-red-200/50 rounded-lg px-3 py-2">
            {message.error}
          </div>
        )}

        {message.content && onSpeak && !message.streaming && (
          <div className="mt-1.5 flex items-center gap-2">
            <button
              onClick={() => onSpeak(message.content)}
              className="text-[11px] text-slate-500 hover:text-slate-700 flex items-center gap-1 transition-colors"
              title="Read aloud"
            >
              <Volume2 size={14} strokeWidth={1.6} />
              {speaking ? 'speaking…' : 'speak'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
