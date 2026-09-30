import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Schedule {
  id: string
  name: string
  cron: string
  prompt: string
  enabled: boolean
  next_run?: number
  last_run?: number
  last_status?: string
  run_count: number
}

function fmt(ts?: number) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function SchedulesPanel({ userId = 'default', agentId }: { userId?: string; agentId?: string }) {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [status, setStatus] = useState<any>(null)
  const [cron, setCron] = useState('0 9 * * 1-5')
  const [prompt, setPrompt] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    api.listSchedules()
      .then((d) => { setSchedules(d.schedules.filter((s: any) => s.user_id === userId && (!agentId || s.agent_id === agentId))); setStatus(d.status) })
      .catch((e) => setError(e.message))
  }

  useEffect(refresh, [userId, agentId])

  const create = async () => {
    if (!cron.trim() || !prompt.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.createSchedule(cron.trim(), prompt.trim(), name.trim() || undefined, userId, agentId)
      setPrompt('')
      setName('')
      refresh()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (s: Schedule) => {
    await api.updateSchedule(s.id, { enabled: !s.enabled }).catch((e) => setError(e.message))
    refresh()
  }

  const remove = async (id: string) => {
    await api.deleteSchedule(id).catch((e) => setError(e.message))
    refresh()
  }

  const runNow = async (id: string) => {
    setBusy(true)
    await api.runSchedule(id).catch((e) => setError(e.message))
    setBusy(false)
    refresh()
  }

  return (
    <div className="space-y-4">
      {status && (
        <div className="text-[11px] text-slate-500">
          scheduler {status.running ? 'running' : 'stopped'} · tz {status.timezone} ·{' '}
          {status.enabled}/{status.total} enabled
        </div>
      )}
      {error && <div className="text-xs text-red-600">{error}</div>}

      <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 space-y-2">
        <div className="text-xs text-slate-600 font-medium">New schedule</div>
        <input
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          placeholder='cron: "0 9 * * 1-5" or "@every 30m"'
          className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono outline-none focus:border-slate-300"
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name (optional)"
          className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300"
        />
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="What should the agent do on schedule?"
          rows={2}
          className="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-slate-300 resize-none"
        />
        <button
          onClick={create}
          disabled={busy || !cron.trim() || !prompt.trim()}
          className="px-3 py-1.5 bg-colons-accent hover:bg-colons-accentHover disabled:bg-slate-200 rounded-lg text-sm font-medium transition-colors"
        >
          Add schedule
        </button>
      </div>

      {schedules.length === 0 && (
        <div className="text-sm text-slate-500">No schedules yet.</div>
      )}

      {schedules.map((s) => (
        <div key={s.id} className="border border-slate-200 rounded-xl p-3 space-y-1.5">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${s.enabled ? 'bg-emerald-400' : 'bg-slate-300'}`} />
            <span className="text-sm font-medium truncate">{s.name || s.cron}</span>
            <span className="text-[10px] font-mono text-slate-500 ml-auto">{s.cron}</span>
          </div>
          <div className="text-[11px] text-slate-500 truncate">{s.prompt}</div>
          <div className="flex items-center gap-3 text-[11px] text-slate-500">
            <span>next: {fmt(s.next_run)}</span>
            <span>runs: {s.run_count}</span>
            {s.last_status && (
              <span className={['ok', 'unchanged'].includes(s.last_status) ? 'text-emerald-700' : 'text-red-600'}>
                {s.last_status}
              </span>
            )}
          </div>
          <div className="flex gap-2 pt-1">
            <button onClick={() => runNow(s.id)} disabled={busy}
              className="text-[11px] px-2 py-1 rounded-md bg-slate-100 hover:bg-slate-200 transition-colors">
              Run now
            </button>
            <button onClick={() => toggle(s)}
              className="text-[11px] px-2 py-1 rounded-md bg-slate-100 hover:bg-slate-200 transition-colors">
              {s.enabled ? 'Disable' : 'Enable'}
            </button>
            <button onClick={() => remove(s.id)}
              className="text-[11px] px-2 py-1 rounded-md bg-red-50/50 text-red-600 hover:bg-red-100/50 transition-colors">
              Delete
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

export { MessagingPanel } from './MessagingPanel'
