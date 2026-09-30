import { BotAvatar } from './BotAvatar'
import { X } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import type { AvatarInfo, BotInfo } from '../lib/types'

interface Props {
  open: boolean
  onClose: () => void
  avatars: AvatarInfo[]
  onCreated: (bot: BotInfo) => void
}

export function BotModal({ open, onClose, avatars, onCreated }: Props) {
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [persona, setPersona] = useState('')
  const [avatar, setAvatar] = useState('')
  const [model, setModel] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!open) return null

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true)
    setError('')
    try {
      const bot = await api.createBot({
        name: name.trim(), title: title.trim(), description: description.trim(),
        persona: persona.trim(), avatar, model: model.trim(),
      })
      onCreated(bot)
      setName(''); setTitle(''); setDescription(''); setPersona(''); setAvatar(''); setModel('')
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-0 md:p-6">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative bg-white w-full md:max-w-lg md:rounded-2xl h-full md:h-auto md:max-h-[85vh] flex flex-col border border-slate-200">
        <div className="flex items-center justify-between p-4 border-b border-slate-200">
          <h2 className="font-semibold">New bot</h2>
          <button onClick={onClose} className="text-slate-600 hover:text-slate-800 p-1">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {error && <div className="text-xs text-red-600">{error}</div>}

          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name (e.g. Researcher)"
            autoFocus
            className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300"
          />
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (e.g. Deep Researcher)"
            className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What is this bot for?"
            rows={2}
            className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300 resize-none"
          />
          <textarea
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
            placeholder="Persona / standing instructions (optional)"
            rows={3}
            className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300 resize-none"
          />
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="Model override (optional, e.g. gpt-4o)"
            className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300"
          />

          <div>
            <div className="text-xs text-slate-500 mb-2">Avatar</div>
            <div className="grid grid-cols-6 gap-2">
              {avatars.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setAvatar(avatar === a.id ? '' : a.id)}
                  className={`p-2 rounded-lg border flex items-center justify-center transition-colors
                    ${avatar === a.id ? 'border-colons-accent bg-colons-accent/10' : 'border-slate-200 hover:bg-slate-100/50'}`}
                  title={a.name}
                >
                  <BotAvatar avatar={a} size={36} />
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-slate-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || !name.trim()}
            className="px-4 py-2 bg-colons-accent hover:bg-colons-accentHover disabled:bg-slate-200 rounded-lg text-sm font-medium transition-colors"
          >
            Create bot
          </button>
        </div>
      </div>
    </div>
  )
}

interface RoomModalProps {
  open: boolean
  onClose: () => void
  bots: BotInfo[]
  onCreated: (roomId: string) => void
}

export function RoomModal({ open, onClose, bots, onCreated }: RoomModalProps) {
  const [name, setName] = useState('')
  const [members, setMembers] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!open) return null

  const toggle = (id: string) => {
    setMembers((prev) => prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id])
  }

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      const room = await api.createRoom(name.trim() || 'Room', members)
      onCreated(room.id)
      setName(''); setMembers([])
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-0 md:p-6">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative bg-white w-full md:max-w-md md:rounded-2xl h-full md:h-auto flex flex-col border border-slate-200">
        <div className="flex items-center justify-between p-4 border-b border-slate-200">
          <h2 className="font-semibold">New room</h2>
          <button onClick={onClose} className="text-slate-600 hover:text-slate-800 p-1">
            <X size={18} />
          </button>
        </div>
        <div className="p-4 space-y-3">
          {error && <div className="text-xs text-red-600">{error}</div>}
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Room name (e.g. Launch squad)"
            className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300"
          />
          <div className="text-xs text-slate-500">Pick 2–6 bots</div>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {bots.map((bot) => (
              <label key={bot.id} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-100/50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={members.includes(bot.id)}
                  onChange={() => toggle(bot.id)}
                  className="accent-colons-accent"
                />
                <span className="text-sm">@{bot.id}</span>
                <span className="text-[11px] text-slate-500 truncate">{bot.title}</span>
              </label>
            ))}
          </div>
        </div>
        <div className="p-4 border-t border-slate-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || members.length < 2 || members.length > 6}
            className="px-4 py-2 bg-colons-accent hover:bg-colons-accentHover disabled:bg-slate-200 rounded-lg text-sm font-medium transition-colors"
          >
            Create room
          </button>
        </div>
      </div>
    </div>
  )
}
