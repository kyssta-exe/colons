import { useEffect, useState } from 'react'
import { Check, LoaderCircle, ShieldCheck, ShieldQuestion, Zap, Folder } from 'lucide-react'
import { api } from '../lib/api'

const modes = [
  { id: 'auto', label: 'Auto', icon: ShieldCheck, description: 'Read files, search, and calculate automatically. Ask before file changes, commands, and browser actions.', detail: 'Balanced default' },
  { id: 'approval', label: 'Approval', icon: ShieldQuestion, description: 'Ask before every agent tool call, including reads. Allow or decline individual actions in chat.', detail: 'You review each action' },
  { id: 'full_access', label: 'Full access', icon: Zap, description: 'Run available tools without approval prompts, including file changes, shell commands, and browser actions.', detail: 'For trusted, unattended work' },
]
export function PermissionsPanel() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.permissionSettings>> | null>(null)
  const [mode, setMode] = useState('auto')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let disposed = false
    api.permissionSettings().then((value) => { if (!disposed) { setData(value); setMode(value.mode) } }).catch((e) => { if (!disposed) setError(e.message) })
    return () => { disposed = true }
  }, [])
  const save = async () => {
    setBusy(true); setError(''); setNotice('')
    try { const value = await api.setPermissionMode(mode); setData(value); setNotice('Default saved. Applies to the next tool calls for existing and new agents.') }
    catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  return <div className="space-y-5">
    {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {notice && <p role="status" className="rounded-xl bg-emerald-50 p-4 text-sm text-emerald-700">{notice}</p>}
    {!data && !error && <p role="status" className="flex items-center gap-2 text-sm text-slate-500"><LoaderCircle size={16} className="animate-spin" />Loading permissions…</p>}
    {data && <><fieldset disabled={busy} className="space-y-3"><legend className="sr-only">Default permission mode</legend>{modes.map(({ id, label, icon: Icon, description, detail }) => <label key={id} className={`flex gap-3 sm:gap-4 cursor-pointer rounded-2xl border p-4 sm:p-5 transition-colors ${mode === id ? 'border-colons-accent bg-[#edf2f8]' : 'border-slate-200 hover:bg-slate-50'}`}><input type="radio" name="permission-mode" value={id} checked={mode === id} onChange={() => { setMode(id); setNotice('') }} className="mt-1 accent-[#718aae] shrink-0" /><div className="min-w-0 flex-1"><div className="flex gap-2 items-center"><Icon size={18} strokeWidth={1.6} /><span className="text-sm font-medium">{label}</span>{data.mode === id && <span className="ml-auto text-[10px] text-colons-accent">Active</span>}</div><p className="mt-2 text-xs text-slate-500 leading-relaxed">{description}</p><p className="mt-2 text-[11px] text-slate-400">{detail}</p></div></label>)}</fieldset>
    <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 space-y-3"><div className="flex items-start gap-2 text-xs text-slate-600"><Folder size={15} className="shrink-0" /><span className="break-all">File workspace: {data.workspace}</span></div><p className="text-xs text-slate-500 leading-relaxed">File tools stay inside this workspace. Shell commands run with the server account’s access. Disabled tools and actions reserved for the user remain unavailable in every mode.</p>{data.disabled_tools.length > 0 && <p className="text-xs text-slate-500">Disabled tools: {data.disabled_tools.join(', ')}</p>}<p className="text-xs text-slate-500 leading-relaxed">Approval prompts appear in chat. Background jobs decline actions that need approval. Commands you submit directly in Terminal are explicitly authorized by you.</p></div>
    <div className="flex flex-col sm:flex-row sm:items-center gap-4 border-t border-slate-100 pt-5"><p className="flex-1 text-xs text-slate-500">Saved on this server, including across restarts.</p><button onClick={() => void save()} disabled={busy || data.mode === mode} className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-40">{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Check size={16} />}Save default</button></div></>}
  </div>
}
