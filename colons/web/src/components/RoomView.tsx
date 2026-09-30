import { ArrowLeft } from 'lucide-react'
import { BotAvatar } from './BotAvatar'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { BotInfo, RoomInfo, RoomMessage } from '../lib/types'
import { Composer } from './Composer'

interface Props {
  room: RoomInfo
  bots: BotInfo[]
  onBack: () => void
  onChanged: () => void
}

function speakerInfo(speaker: string, bots: BotInfo[], room: RoomInfo) {
  if (speaker === 'user') return { name: 'You', bot: null as BotInfo | null }
  const bot = bots.find((b) => b.id === speaker)
    || room.member_defs.find((b) => b.id === speaker)
    || null
  return { name: bot?.name || `@${speaker}`, bot }
}

export function RoomView({ room, bots, onBack, onChanged }: Props) {
  const [messages, setMessages] = useState<RoomMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  const load = () => {
    api.roomMessages(room.id).then((d) => setMessages(d.messages)).catch((e) => setError(e.message))
  }

  useEffect(() => {
    load()
    api.roomSeen(room.id).then(onChanged).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.id])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  const send = async (text: string) => {
    setBusy(true)
    setError('')
    const optimistic: RoomMessage = {
      room_id: room.id, speaker: 'user', content: text, created_at: Date.now() / 1000, metadata: {},
    }
    setMessages((prev) => [...prev, optimistic])
    try {
      const d = await api.sendRoomMessage(room.id, text)
      // Replace optimistic + append real messages
      setMessages((prev) => [
        ...prev.filter((m) => m !== optimistic),
        ...d.messages,
      ])
      onChanged()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const mentionables = room.member_defs.map((b) => ({ id: b.id, name: b.name, title: b.title }))

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <header className="flex items-center gap-3 px-3 py-2.5 border-b border-slate-200/70">
        <button onClick={onBack} className="text-slate-600 hover:text-slate-800 p-1 md:hidden">
          <ArrowLeft size={18} strokeWidth={1.6} />
        </button>
        <div className="min-w-0">
          <div className="text-sm font-medium flex items-center gap-2">
            👥 {room.name}
            {room.needs_user && (
              <span className="text-[10px] bg-amber-500/20 text-amber-300 border border-amber-600/40 rounded-full px-2 py-0.5">
                needs you
              </span>
            )}
          </div>
          <div className="text-[11px] text-slate-500 truncate">
            {room.members.map((m) => `@${m}`).join(' · ')}
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto py-5 space-y-4 px-3 md:px-0">
          {messages.length === 0 && (
            <div className="text-center text-sm text-slate-500 py-10">
              Say something to start the room. Members reply in rounds, pass when they
              have nothing to add, and can pull each other in with @mentions.
            </div>
          )}
          {messages.map((m, i) => {
            const { name, bot } = speakerInfo(m.speaker, bots, room)
            const isUser = m.speaker === 'user'
            return (
              <div key={m.id ?? `optimistic-${i}`} className="flex gap-3 animate-fade-in">
                <div className="shrink-0 mt-0.5">
                  {isUser ? (
                    <div className="w-7 h-7 rounded-full bg-slate-200 flex items-center justify-center text-xs">U</div>
                  ) : (
                    <BotAvatar avatar={bot?.avatar_def} size={28} />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] text-slate-500 mb-0.5">{name}</div>
                  <div className="text-[14px] whitespace-pre-wrap leading-relaxed text-slate-800">
                    {m.content}
                  </div>
                </div>
              </div>
            )
          })}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="w-1.5 h-1.5 rounded-full bg-colons-accent animate-pulse" />
              members are deliberating…
            </div>
          )}
          {error && <div className="text-xs text-red-600">{error}</div>}
          <div ref={endRef} />
        </div>
      </main>

      <Composer
        onSend={send}
        onCancel={() => {}}
        streaming={busy}
        status={busy ? 'room round in progress' : 'idle'}
        mentionables={mentionables}
        placeholder="Message the room…"
      />
    </div>
  )
}
