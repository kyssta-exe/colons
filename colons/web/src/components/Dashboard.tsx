import { useCallback, useEffect, useRef, useState } from 'react'
import { BrowserPanel } from './BrowserPanel'
import { BotAvatar } from './BotAvatar'
import { Menu } from 'lucide-react'
import { api } from '../lib/api'
import type { BotInfo, RoomInfo, TaskInfo } from '../lib/types'
import { SchedulesPanel } from './SchedulesPanel'

export type SetupTab = 'provider' | 'avatar' | 'voice' | 'schedules' | 'messaging' | 'key' | 'usage'
type Tab = 'overview' | 'tasks' | 'schedules' | 'memory' | 'browser' | 'system'
const button = 'px-3 py-2 text-sm rounded-lg border border-slate-200 hover:bg-slate-100 disabled:opacity-40 transition-colors'
const card = 'rounded-2xl border border-slate-200 bg-[#fafafa] p-5'

interface Props {
  userId: string
  agentId: string
  bots: BotInfo[]
  rooms: RoomInfo[]
  onChat: (botId?: string) => void
  onSetup: (tab: SetupTab) => void
  onCreateBot: () => void
  onCreateRoom: () => void
  onRoom: (id: string) => void
  onMenu: () => void
}

export function Dashboard({ userId, agentId, bots, rooms, onChat, onSetup, onCreateBot, onCreateRoom, onRoom, onMenu }: Props) {
  const [tab, setTab] = useState<Tab>('overview')
  const [data, setData] = useState<Record<string, any>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [description, setDescription] = useState('')
  const [query, setQuery] = useState('')
  const [memories, setMemories] = useState<any[]>([])
  const [searched, setSearched] = useState(false)
  const [searching, setSearching] = useState(false)
  const revision = useRef(0)
  const searchRevision = useRef(0)

  const refresh = useCallback(async () => {
    const current = ++revision.current
    const requests: Record<string, Promise<any>> = {
      health: api.health(), provider: api.listProviders(),
      agent: api.agentStatus(agentId, userId), tasks: api.listTasks(userId, agentId),
      messaging: api.messagingStatus(),
    }
    if (tab === 'system') requests.doctor = api.doctor()
    const entries = Object.entries(requests)
    const results = await Promise.allSettled(entries.map(([, request]) => request))
    if (current !== revision.current) return
    const next: Record<string, any> = {}
    const failed: Record<string, string> = {}
    results.forEach((result, i) => {
      const name = entries[i][0]
      if (result.status === 'fulfilled') next[name] = result.value
      else failed[name] = result.reason?.message || 'Could not load'
    })
    setData(next)
    setErrors(failed)
    setLoading(false)
  }, [agentId, userId, tab])

  useEffect(() => {
    setData({})
    setLoading(true)
    setMemories([])
    setSearched(false)
    setSearching(false)
    setActionError('')
    refresh()
    const timer = setInterval(refresh, 10000)
    return () => { clearInterval(timer); revision.current++; searchRevision.current++ }
  }, [refresh])

  const action = async (run: () => Promise<unknown>) => {
    setBusy(true)
    setActionError('')
    try { await run(); await refresh() }
    catch (err: any) { setActionError(err.message || 'Action failed') }
    finally { setBusy(false) }
  }

  const search = async () => {
    if (!query.trim()) return
    const current = ++searchRevision.current
    setSearching(true)
    setActionError('')
    try {
      const result = await api.searchMemory(query.trim(), userId, agentId)
      if (current !== searchRevision.current) return
      setMemories(result.results)
      setSearched(true)
    } catch (err: any) {
      if (current === searchRevision.current) setActionError(err.message)
    } finally { if (current === searchRevision.current) setSearching(false) }
  }

  const tasks: TaskInfo[] = data.tasks?.tasks || []
  const activeTasks = tasks.filter((t) => ['pending', 'planning', 'running'].includes(t.status))
  const provider = data.provider?.active
  const agent = data.agent
  const health = data.health

  return (
    <main className="flex-1 min-w-0 overflow-y-auto">
      <header className="flex items-center justify-between gap-3 px-4 md:px-8 py-5 border-b border-slate-200/70">
        <div className="flex items-center gap-3">
          <div><div className="text-xs text-emerald-700 mb-1">COLONS WORKSPACE</div><h1 className="text-xl font-semibold">Dashboard</h1></div>
        </div>
        <div className="flex gap-2">
          <button className={button} onClick={refresh} disabled={loading}>Refresh</button>
          <button className="px-3 py-2 text-sm bg-colons-accent hover:bg-colons-accentHover rounded-lg" onClick={() => onChat()}>Open chat</button>
        </div>
      </header>
      <div className="max-w-6xl mx-auto p-4 md:p-8 space-y-6">
        <nav aria-label="Dashboard sections" className="flex gap-2 overflow-x-auto pb-1">
          {(['overview', 'tasks', 'schedules', 'memory', 'browser', 'system'] as Tab[]).map((name) => (
            <button key={name} onClick={() => setTab(name)} aria-current={tab === name ? 'page' : undefined}
              className={`px-4 py-2 rounded-lg capitalize text-sm whitespace-nowrap ${tab === name ? 'bg-emerald-500/15 text-emerald-700' : 'text-slate-600 hover:bg-slate-100'}`}>{name}</button>
          ))}
        </nav>
        {(actionError || Object.keys(errors).length > 0) && <div role="alert" className="border border-red-200/60 bg-red-50/20 text-red-700 text-sm p-3 rounded-xl">
          {actionError && <p>{actionError}</p>}
          {Object.entries(errors).map(([name, message]) => <p key={name}>{name}: {message}</p>)}
          {Object.values(errors).some((e) => e.startsWith('401:')) && <button className="underline mt-2" onClick={() => onSetup('key')}>Set your server API key</button>}
        </div>}
        {loading && <p role="status" className="text-slate-600 text-sm">Loading workspace…</p>}
        {tab === 'overview' && <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              ['Server', health ? (health.healthy ? 'Online' : 'Starting') : 'Unavailable'],
              ['Bots', bots.length], ['Active tasks', data.tasks ? activeTasks.length : '—'],
              ['Schedules', health?.scheduler?.enabled ?? '—'],
            ].map(([label, value]) => <div className={card} key={label}><p className="text-xs text-slate-500">{label}</p><p className="text-2xl font-semibold mt-2">{value}</p></div>)}
          </div>
          <div className="grid lg:grid-cols-3 gap-5">
            <section className={`${card} lg:col-span-2 space-y-4`}>
              <div><h2 className="font-medium">Make Colons yours</h2><p className="text-sm text-slate-500 mt-1">Connect a model, give your bots a role, and put recurring work on a schedule.</p></div>
              {[
                { title: 'AI provider', detail: provider ? `${provider.name} · ${provider.model}` : 'Configure a local model or an API provider', tab: 'provider' as SetupTab },
                { title: 'Messaging channels', detail: data.messaging?.adapters?.length ? `${data.messaging.adapters.filter((a: any) => a.connected).length} connected channels` : 'Connect Telegram, Discord, Slack, or webhooks', tab: 'messaging' as SetupTab },
                { title: 'Voice & personality', detail: 'Choose an avatar and a voice for your agent', tab: 'voice' as SetupTab },
              ].map((item) => <button key={item.title} onClick={() => onSetup(item.tab)} className="flex w-full items-center justify-between text-left gap-3 p-3 bg-slate-50/60 hover:bg-slate-100 rounded-xl">
                <div><p className="text-sm text-slate-800">{item.title}</p><p className="text-xs text-slate-500 mt-1 break-all">{item.detail}</p></div><span className="text-slate-500">→</span>
              </button>)}
              <button className={button} onClick={() => setTab('schedules')}>Set up recurring work</button>
            </section>
            <section className={`${card} space-y-4`}>
              <h2 className="font-medium">Selected agent</h2>
              <p className="text-lg">{agent?.name || '—'}</p>
              <div className="text-sm text-slate-600 space-y-2"><p>State: {agent?.state || 'unavailable'}</p><p className="break-all">Model: {agent?.model || '—'}</p><p>{agent?.tools ?? '—'} tools · {agent?.usage?.total_tokens?.toLocaleString() ?? '—'} tokens</p></div>
              {agent && <button className={button} disabled={busy} onClick={() => action(() => api.setAgentPaused(agentId, !agent.paused, userId))}>{agent.paused ? 'Resume agent' : 'Pause background work'}</button>}
              <p className="text-xs text-slate-500">Pause stops new background tasks and reviews. Chat remains available.</p>
            </section>
          </div>
          <section className={card}>
            <div className="flex justify-between items-center mb-4"><h2 className="font-medium">Your team</h2><button className={button} onClick={onCreateBot}>Add bot</button></div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {bots.map((bot) => <button key={bot.id} onClick={() => onChat(bot.id)} className="p-4 rounded-xl bg-slate-50 hover:bg-slate-100 text-left flex gap-3">
                <BotAvatar avatar={bot.avatar_def} size={36} /><div className="min-w-0"><p className="text-sm font-medium truncate">{bot.name}</p><p className="text-xs text-slate-500 mt-1 truncate">{bot.title || bot.description || `@${bot.id}`}</p><p className="text-xs text-emerald-700 mt-2">Open chat →</p></div>
              </button>)}
            </div>
          </section>
          <section className={card}>
            <div className="flex justify-between items-center mb-4"><h2 className="font-medium">Group rooms</h2><button className={button} onClick={onCreateRoom} disabled={bots.length < 2}>Create room</button></div>
            {rooms.length === 0 ? <p className="text-sm text-slate-500">Add two bots to start a shared conversation.</p> : <div className="grid sm:grid-cols-2 gap-3">{rooms.map((room) => <button key={room.id} onClick={() => onRoom(room.id)} className="p-3 rounded-xl bg-slate-50 hover:bg-slate-100 text-left"><p className="text-sm">{room.name} {room.needs_user && <span className="text-amber-700 text-xs">· Needs you</span>}</p><p className="text-xs text-slate-500 mt-1">{room.members.length} bots · {room.message_count} messages</p></button>)}</div>}
          </section>
        </>}
        {tab === 'tasks' && <section className={`${card} space-y-5`}>
          <div><h2 className="font-medium">Background tasks</h2><p className="text-sm text-slate-500 mt-1">Assign work to {agent?.name || 'the selected agent'}. Results update here while you keep chatting.</p><p className="text-xs text-slate-500 mt-1">Tasks currently live in server memory and reset when the server restarts.</p></div>
          <form className="flex flex-col sm:flex-row gap-2" onSubmit={(e) => { e.preventDefault(); action(async () => { await api.createTask(description.trim(), userId, agentId); setDescription('') }) }}>
            <input aria-label="Task description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What should the agent accomplish?" className="flex-1 min-w-0 bg-white rounded-lg px-3 py-2 border border-slate-200 text-sm" required />
            <button className={button} disabled={busy || !description.trim()}>Queue task</button>
          </form>
          {data.tasks && tasks.length === 0 && <p className="text-sm text-slate-500">No tasks for this agent yet.</p>}
          {[...tasks].sort((a, b) => b.created_at - a.created_at).map((task) => <article key={task.id} className="border border-slate-200 rounded-xl p-4 space-y-3">
            <div className="flex justify-between items-start gap-3"><h3 className="text-sm font-medium whitespace-pre-wrap">{task.description}</h3><span className={`text-xs shrink-0 ${task.status === 'failed' ? 'text-red-600' : task.status === 'completed' ? 'text-emerald-700' : 'text-amber-700'}`}>{task.status}</span></div>
            {!!task.plan.length && <ol className="list-decimal pl-5 text-xs text-slate-600 space-y-1">{task.plan.map((step, i) => <li key={i}>{step}</li>)}</ol>}
            {task.result && <pre className="text-sm text-slate-700 whitespace-pre-wrap font-sans">{task.result}</pre>}
            {task.error && <p className="text-sm text-red-600">{task.error}</p>}
            {task.status === 'failed' && <button className={button} disabled={busy} onClick={() => action(() => api.runTask(task.id, userId, agentId))}>Retry task</button>}
          </article>)}
        </section>}
        {tab === 'schedules' && <section className={card}><h2 className="font-medium mb-1">Recurring work</h2><p className="text-sm text-slate-500 mb-5">Schedules use the selected agent. Times follow the server’s scheduler timezone.</p><SchedulesPanel userId={userId} agentId={agentId} /></section>}
        {tab === 'memory' && <section className={`${card} space-y-4`}>
          <div><h2 className="font-medium">Agent memory</h2><p className="text-sm text-slate-500 mt-1">Find what the selected agent remembers from earlier work.</p></div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); search() }}><input aria-label="Search memory" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search memories…" className="flex-1 min-w-0 bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm" /><button className={button} disabled={searching || !query.trim()}>{searching ? 'Searching…' : 'Search'}</button></form>
          {searched && !memories.length && <p className="text-sm text-slate-500">No matching memories found.</p>}
          {memories.map((memory, i) => <article key={memory.id || i} className="bg-slate-50 p-4 rounded-xl"><p className="text-xs text-slate-500 mb-2">{memory.kind || memory.role || 'Memory'}</p><p className="text-sm text-slate-700 whitespace-pre-wrap">{memory.content}</p></article>)}
        </section>}
        {tab === 'browser' && <section className={card}><BrowserPanel key={agentId} userId={userId} agentId={agentId} /></section>}
        {tab === 'system' && <section className={`${card} space-y-4`}>
          <div className="flex justify-between gap-3"><div><h2 className="font-medium">System diagnostics</h2><p className="text-sm text-slate-500 mt-1">Local environment and configuration checks. Provider connectivity is not tested here.</p></div><button className={button} onClick={() => onSetup('key')}>API access</button></div>
          {data.doctor?.checks?.map((check: any, i: number) => <div key={i} className="border-b border-slate-200 pb-3"><div className="flex justify-between gap-3"><p className="text-sm">{check.name}</p><span className={`text-xs ${check.status === 'pass' ? 'text-emerald-700' : check.status === 'fail' ? 'text-red-600' : 'text-amber-700'}`}>{check.status}</span></div><p className="text-xs text-slate-500 mt-1 whitespace-pre-wrap">{check.detail}</p>{check.hint && <p className="text-xs text-slate-600 mt-1">{check.hint}</p>}</div>)}
        </section>}
      </div>
    </main>
  )
}
