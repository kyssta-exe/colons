import { BotAvatar } from './BotAvatar'
import { UsagePanel } from './UsagePanel'
import { PermissionsPanel } from './PermissionsPanel'
import { X, Cpu, Palette, AudioLines, CalendarClock, MessagesSquare, KeyRound, ChartNoAxesColumn, Check, LoaderCircle, Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { SetupTab } from './Dashboard'
import { api } from '../lib/api'
import type { AvatarInfo, ProviderInfo, UsageInfo } from '../lib/types'
import { MessagingPanel, SchedulesPanel } from './SchedulesPanel'

interface Props {
  initialTab?: SetupTab; open: boolean; onClose: () => void; userId: string; agentId: string
  currentAvatar?: AvatarInfo; onAvatarChange: (a: AvatarInfo) => void; onSpeakVoiceChange: (voice: string) => void
}
type Tab = 'permissions' | 'provider' | 'avatar' | 'voice' | 'schedules' | 'messaging' | 'key' | 'usage'
const sections = [
  { id: 'provider', label: 'Models', icon: Cpu, title: 'Choose your intelligence', description: 'Connect a provider and choose the model Colons uses.' },
  { id: 'avatar', label: 'Appearance', icon: Palette, title: 'A little more you', description: 'The same Colons identity, in your agent’s color.' },
  { id: 'voice', label: 'Voice', icon: AudioLines, title: 'Give Colons a voice', description: 'Choose how replies sound when you listen to them.' },
  { id: 'schedules', label: 'Schedules', icon: CalendarClock, title: 'Make room for routines', description: 'Set up recurring work for this agent.' },
  { id: 'messaging', label: 'Messaging', icon: MessagesSquare, title: 'Meet Colons where you work', description: 'Connect your messaging services and talk to Colons anywhere.' },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck, title: 'Decide how Colons acts', description: 'Set the default tool permission mode for all agents on this server.' },
  { id: 'key', label: 'Server access', icon: KeyRound, title: 'Your server, connected', description: 'Manage the credential this browser uses to access Colons.' },
  { id: 'usage', label: 'Usage', icon: ChartNoAxesColumn, title: 'Your activity at a glance', description: 'Token and tool usage for the current agent.' },
] as const
const field = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-sm text-slate-800 focus:border-colons-accent outline-none'
const primary = 'inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed'

export function SettingsPanel({ initialTab, open, onClose, userId, agentId, currentAvatar, onAvatarChange, onSpeakVoiceChange }: Props) {
  const [tab, setTab] = useState<Tab>('provider')
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [active, setActive] = useState<any>(null)
  const [provider, setProvider] = useState('')
  const [avatars, setAvatars] = useState<AvatarInfo[]>([])
  const [avatarMotion, setAvatarMotion] = useState(localStorage.getItem('colons_motion') !== 'off')
  const [voices, setVoices] = useState<any[]>([])
  const [ttsEngine, setTtsEngine] = useState<string | null>(null)
  const [selectedVoice, setSelectedVoice] = useState(localStorage.getItem('colons_voice') || '')
  const [apiKey, setApiKey] = useState(localStorage.getItem('colons_api_key') || '')
  const [providerKey, setProviderKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null)
  const [loadErrors, setLoadErrors] = useState<Partial<Record<Tab, string>>>({})
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [usage, setUsage] = useState<UsageInfo | null>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return
    let disposed = false
    setLoading(true); setNotice(null); setLoadErrors({}); setProviderKey(''); setShowKey(false)
    const requests = [api.listProviders(), api.listAvatars(), api.listVoices(), api.usage(userId, agentId)]
    Promise.allSettled(requests).then((results) => {
      if (disposed) return
      const errors: Partial<Record<Tab, string>> = {}
      results.forEach((result, i) => { if (result.status === 'rejected') errors[(['provider', 'avatar', 'voice', 'usage'] as Tab[])[i]] = result.reason?.message || 'Could not load this section.' })
      const [p, a, v, u] = results
      if (p.status === 'fulfilled') {
        const data = p.value as Awaited<ReturnType<typeof api.listProviders>>
        setProviders(data.providers); setActive(data.active); setProvider(data.active?.name || data.providers[0]?.name || '')
        const selected = data.providers.find((item) => item.name === data.active?.name) || data.providers[0]
        setModel(data.active?.model || selected?.default_model || ''); setBaseUrl(data.active?.base_url || selected?.base_url || '')
      }
      if (a.status === 'fulfilled') setAvatars((a.value as Awaited<ReturnType<typeof api.listAvatars>>).avatars)
      if (v.status === 'fulfilled') { const data = v.value as Awaited<ReturnType<typeof api.listVoices>>; setVoices(data.voices); setTtsEngine(data.engine) }
      if (u.status === 'fulfilled') setUsage(u.value as UsageInfo)
      setLoadErrors(errors); setLoading(false)
    })
    return () => { disposed = true }
  }, [open, userId, agentId])

  useEffect(() => { if (open) { setTab(initialTab || 'provider'); setNotice(null) } }, [open, initialTab])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current()
      if (event.key !== 'Tab') return
      const elements = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]') || [])].filter((el) => el.getClientRects().length)
      const first = elements[0], last = elements[elements.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [open])

  if (!open) return null
  const section = sections.find((s) => s.id === tab)!
  const selectedProvider = providers.find((p) => p.name === provider)
  const chooseProvider = (name: string) => {
    const p = providers.find((item) => item.name === name)
    setProvider(name); setProviderKey(''); setModel(p?.default_model || ''); setBaseUrl(p?.base_url || ''); setNotice(null)
  }
  const applyProvider = async () => {
    setBusy(true); setNotice(null)
    try {
      await api.switchProvider(provider, providerKey.trim() || undefined, baseUrl.trim() || undefined, model.trim() || undefined)
      const data = await api.listProviders(); setActive(data.active); setProviders(data.providers); setProviderKey('')
      setNotice({ error: false, text: 'Model settings applied.' })
    } catch (e: any) { setNotice({ error: true, text: e.message }) }
    finally { setBusy(false) }
  }
  const pickAvatar = async (id: string) => {
    setBusy(true); setNotice(null)
    try { const res = await api.selectAvatar(agentId, id, userId); onAvatarChange(res.selected); setNotice({ error: false, text: 'Agent appearance updated.' }) }
    catch (e: any) { setNotice({ error: true, text: e.message }) }
    finally { setBusy(false) }
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center sm:p-5">
    <div className="absolute inset-0 bg-slate-900/25 backdrop-blur-sm" onClick={onClose} />
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1} className="relative flex h-full sm:h-[min(740px,90dvh)] w-full max-w-[960px] flex-col overflow-hidden bg-white sm:rounded-3xl border border-slate-200 shadow-2xl outline-none">
      <header className="flex shrink-0 items-center justify-between border-b border-slate-200/70 px-6 py-5">
        <div><h2 id="settings-title" className="text-lg font-semibold tracking-tight">Settings</h2><p className="mt-0.5 text-xs text-slate-500">Colons v0.0.2 beta · Your workspace, your way.</p></div>
        <button aria-label="Close settings" onClick={onClose} className="rounded-full p-2 text-slate-500 hover:bg-slate-100"><X size={20} /></button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav aria-label="Settings sections" className="flex shrink-0 gap-1 overflow-x-auto border-b md:border-b-0 md:border-r border-slate-200/70 bg-[#fafbfc] p-3 md:w-52 md:flex-col md:p-4">
          {sections.map(({ id, label, icon: Icon }) => <button key={id} onClick={() => { setTab(id); setNotice(null); setShowKey(false) }} aria-current={tab === id ? 'page' : undefined} className={`flex items-center gap-3 whitespace-nowrap rounded-xl px-3 py-3 text-sm transition-colors ${tab === id ? 'bg-[#eaf0f8] text-slate-900 font-medium' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'}`}><Icon size={18} strokeWidth={1.7} />{label}</button>)}
          <div className="hidden md:block mt-auto px-3 pt-6"><div className="colons-mark small mb-3" aria-hidden="true"><i /><i /></div><p className="text-xs text-slate-400 leading-relaxed">A calmer space to<br />think things through.</p></div>
        </nav>
        <main className="flex-1 min-w-0 overflow-y-auto px-6 py-7 sm:px-9 sm:py-8">
          <h3 className="text-xl font-semibold tracking-tight">{section.title}</h3><p className="text-sm text-slate-500 mt-2 mb-7 leading-relaxed">{section.description}</p>
          {notice && <div role={notice.error ? 'alert' : 'status'} className={`mb-5 rounded-xl px-4 py-3 text-sm ${notice.error ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'}`}>{notice.text}</div>}
          {loadErrors[tab] && <p role="alert" className="mb-5 rounded-xl bg-red-50 p-4 text-sm text-red-700">{loadErrors[tab]}</p>}
          {loading && !['key', 'messaging', 'schedules', 'permissions'].includes(tab) ? <div role="status" className="flex gap-2 items-center text-sm text-slate-500"><LoaderCircle size={18} className="animate-spin" /> Loading settings…</div> : <>
          {tab === 'provider' && !loadErrors.provider && <form onSubmit={(e) => { e.preventDefault(); void applyProvider() }} className="space-y-5">
            <div className="rounded-2xl border border-slate-200 bg-[#fafbfc] px-4 py-4 flex items-center gap-3"><div className="rounded-xl bg-white border border-slate-200 p-2.5"><Cpu size={20} strokeWidth={1.6} /></div><div className="min-w-0"><p className="text-[11px] uppercase tracking-wider text-slate-400">Currently active</p><p className="text-sm font-medium mt-1 break-words">{providers.find((p) => p.name === active?.name)?.display || active?.name || 'No provider'} <span className="font-normal text-slate-500">· {active?.model || 'Default model'}</span></p></div></div>
            <label className="block text-sm font-medium">Provider<select aria-label="Provider" className={`${field} mt-2`} value={provider} disabled={busy} onChange={(e) => chooseProvider(e.target.value)}>{providers.map((p) => <option key={p.name} value={p.name}>{p.display}{p.local ? ' · Local' : p.configured ? ' · Key available' : ''}</option>)}</select></label>
            <label className="block text-sm font-medium">Model<input className={`${field} mt-2`} value={model} disabled={busy} onChange={(e) => setModel(e.target.value)} placeholder={selectedProvider?.default_model || 'Model name'} /></label>
            {!selectedProvider?.local && <label className="block text-sm font-medium">Provider API key<div className="relative mt-2"><input className={`${field} pr-12`} value={providerKey} disabled={busy} onChange={(e) => setProviderKey(e.target.value)} type={showKey ? 'text' : 'password'} autoComplete="off" placeholder={selectedProvider?.configured ? 'Leave blank to use the configured key' : 'Enter your provider key'} /><button type="button" aria-label={showKey ? 'Hide key' : 'Show key'} onClick={() => setShowKey(!showKey)} className="absolute right-3 top-3 text-slate-400">{showKey ? <EyeOff size={18} /> : <Eye size={18} />}</button></div><span className="block mt-2 text-xs font-normal text-slate-500">Used by your model provider. Separate from your Colons server key.</span></label>}
            <details className="rounded-xl border border-slate-200 px-4 py-3"><summary className="text-sm text-slate-600 cursor-pointer">Advanced connection</summary><label className="block text-xs font-medium mt-4 mb-1">API endpoint<input className={`${field} mt-2`} type="url" value={baseUrl} disabled={busy} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" /></label></details>
            <div className="border-t border-slate-100 pt-5 flex flex-col sm:flex-row sm:items-center gap-4"><p className="flex-1 text-xs text-slate-500 leading-relaxed">Applies to all agents until the server restarts.<br />For permanent settings, update colons.yaml.</p><button className={primary} disabled={busy || !provider}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Check size={16} />} Apply changes</button></div>
          </form>}
          {tab === 'avatar' && !loadErrors.avatar && <div className="space-y-6"><div className="flex gap-4 items-center rounded-2xl bg-[#fafbfc] border border-slate-200 p-5"><BotAvatar avatar={currentAvatar} size={60} /><div><p className="text-sm font-medium">Your Colons avatar</p><p className="text-xs text-slate-500 mt-1">Two dots. One familiar face.</p></div></div><label className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 p-4"><span><span className="block text-sm font-medium">Animated avatars</span><span className="block text-xs text-slate-500 mt-1">Subtle expressions for thinking, working, and speaking. Respects reduced motion.</span></span><input type="checkbox" checked={avatarMotion} onChange={(event) => { const enabled = event.target.checked; setAvatarMotion(enabled); localStorage.setItem('colons_motion', enabled ? 'on' : 'off'); document.documentElement.dataset.colonsMotion = enabled ? 'on' : 'off' }} className="h-4 w-4 accent-slate-700" /></label><div className="grid grid-cols-3 sm:grid-cols-4 gap-3">{avatars.map((a) => <button key={a.id} disabled={busy} aria-pressed={currentAvatar?.id === a.id} onClick={() => void pickAvatar(a.id)} className={`relative rounded-2xl border p-4 flex flex-col items-center gap-3 disabled:opacity-50 ${currentAvatar?.id === a.id ? 'bg-[#edf2f8] border-colons-accent' : 'border-slate-200 hover:bg-slate-50'}`}><BotAvatar avatar={a} size={42} /><span className="text-xs text-slate-600">{a.name}</span>{currentAvatar?.id === a.id && <Check size={12} className="absolute right-2 top-2 text-colons-accent" />}</button>)}</div></div>}
          {tab === 'voice' && !loadErrors.voice && <div className="space-y-6"><div className="rounded-2xl border border-slate-200 p-5 flex items-center gap-4"><AudioLines size={24} strokeWidth={1.5} /><div><p className="text-sm font-medium">Speech playback</p><p className="text-xs text-slate-500 mt-1">{ttsEngine ? `${ttsEngine} is available` : 'No speech engine is available on this server'}</p></div></div><label className="block text-sm font-medium">Reply voice<select className={`${field} mt-2`} value={selectedVoice} disabled={!ttsEngine} onChange={(e) => { setSelectedVoice(e.target.value); localStorage.setItem('colons_voice', e.target.value); onSpeakVoiceChange(e.target.value); setNotice({ error: false, text: 'Voice preference saved in this browser.' }) }}><option value="">Default voice</option>{voices.map((v) => <option key={v.id} value={v.id}>{v.id} ({v.locale})</option>)}</select></label><p className="text-xs text-slate-500 leading-relaxed">Use the speak control underneath a reply to listen. Your voice preference is saved automatically in this browser.</p></div>}
          {tab === 'permissions' && <PermissionsPanel />}
          {tab === 'schedules' && <SchedulesPanel userId={userId} agentId={agentId} />}
          {tab === 'messaging' && <MessagingPanel />}
          {tab === 'key' && <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); localStorage.setItem('colons_api_key', apiKey.trim()); setNotice({ error: false, text: 'Server key saved in this browser.' }) }}><div className="rounded-2xl border border-slate-200 bg-[#fafbfc] p-5"><KeyRound size={22} strokeWidth={1.5} className="mb-3" /><p className="text-sm font-medium">Colons server authentication</p><p className="text-xs text-slate-500 mt-2 leading-relaxed">If your server requires authentication, enter its API key here. This credential is stored in this browser and sent with requests to your Colons server.</p></div><label className="block text-sm font-medium">Server API key<input className={`${field} mt-2`} value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" autoComplete="off" placeholder="Enter your Colons server key" /></label><button className={primary}>Save server key</button></form>}
          {tab === 'usage' && usage && !loadErrors.usage && <UsagePanel usage={usage} onRefresh={async () => { setUsage(await api.usage(userId, agentId)) }} />}
          </>}
        </main>
      </div>
    </div>
  </div>
}
