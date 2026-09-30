import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Play, Terminal, Trash2, X } from 'lucide-react'
import { api } from '../lib/api'

type Entry = { command: string; stdout: string; stderr: string; code?: number; error?: string }
export function TerminalPanel({ open, userId, agentId, onClose }: { open: boolean; userId: string; agentId: string; onClose: () => void }) {
  const [command, setCommand] = useState('')
  const [cwd, setCwd] = useState('')
  const [entries, setEntries] = useState<Entry[]>([])
  const [running, setRunning] = useState(false)
  const [historyIndex, setHistoryIndex] = useState(-1)
  const output = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { if (open) input.current?.focus() }, [open])
  useEffect(() => { output.current?.scrollTo({ top: output.current.scrollHeight }) }, [entries, running, open])
  const run = async () => {
    if (!command.trim() || running) return
    const submitted = command.trim()
    setRunning(true); setCommand(''); setHistoryIndex(-1)
    setEntries((old) => [...old.slice(-49), { command: submitted, stdout: '', stderr: '' }])
    try {
      const result = await api.terminalCommand(submitted, cwd.trim() || undefined, userId, agentId)
      setEntries((old) => [...old.slice(0, -1), { command: submitted, stdout: result.output?.stdout || '', stderr: result.output?.stderr || '', code: result.output?.returncode, error: result.status !== 'ok' ? result.error || 'Command failed' : undefined }])
    } catch (e: any) { setEntries((old) => [...old.slice(0, -1), { command: submitted, stdout: '', stderr: '', error: e.message }]) }
    finally { setRunning(false); input.current?.focus() }
  }
  if (!open) return null
  return <section aria-label="Workspace terminal" className="shrink-0 h-[min(340px,45dvh)] min-h-[190px] flex flex-col bg-[#18202d] text-slate-200 border-t border-slate-300">
    <header className="flex items-center justify-between px-4 py-2 border-b border-white/10"><div className="flex items-center gap-2 text-xs"><Terminal size={16} /><span>Terminal</span><span className="text-slate-400 hidden sm:inline">· Server workspace · {agentId}</span></div><div className="flex gap-2"><button aria-label="Clear terminal" disabled={running} onClick={() => setEntries([])} className="p-1.5 text-slate-400 hover:text-white disabled:opacity-30"><Trash2 size={15} /></button><button aria-label="Close terminal" onClick={onClose} className="p-1.5 text-slate-400 hover:text-white"><X size={17} /></button></div></header>
    <div ref={output} role="log" aria-live="polite" className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed">{!entries.length && <p className="text-slate-400">Run a command in your server workspace. Each command runs in a new shell; use the working directory field to change folders. Interactive programs are not supported.</p>}{entries.map((entry, i) => <div key={i} className="mb-3"><p className="text-blue-200 break-all">$ {entry.command}</p>{entry.stdout && <pre className="whitespace-pre-wrap break-all">{entry.stdout}</pre>}{entry.stderr && <pre className="whitespace-pre-wrap break-all text-amber-200">{entry.stderr}</pre>}{entry.error && <p className="text-red-300">{entry.error}</p>}{entry.code !== undefined && entry.code !== 0 && <p className="text-red-300">Exit code {entry.code}</p>}</div>)}{running && <div className="flex gap-2 items-center text-slate-400"><LoaderCircle size={14} className="animate-spin" />Running…</div>}</div>
    <form onSubmit={(e) => { e.preventDefault(); void run() }} className="border-t border-white/10 px-4 py-2 space-y-2"><label className="flex items-center gap-2 text-[11px] text-slate-400">Directory<input aria-label="Terminal working directory" value={cwd} disabled={running} onChange={(e) => setCwd(e.target.value)} placeholder="Default workspace" className="min-w-0 flex-1 bg-transparent text-slate-200 outline-none" /></label><div className="flex items-center gap-3"><span className="text-blue-200 font-mono">$</span><input ref={input} aria-label="Terminal command" value={command} disabled={running} onChange={(e) => setCommand(e.target.value)} onKeyDown={(e) => { if (!['ArrowUp', 'ArrowDown'].includes(e.key) || !entries.length) return; e.preventDefault(); const next = e.key === 'ArrowUp' ? Math.min(historyIndex + 1, entries.length - 1) : Math.max(historyIndex - 1, -1); setHistoryIndex(next); setCommand(next < 0 ? '' : entries[entries.length - 1 - next].command) }} placeholder="Enter a command…" autoComplete="off" spellCheck={false} className="flex-1 min-w-0 bg-transparent font-mono text-xs outline-none py-1" /><button aria-label="Run terminal command" disabled={running || !command.trim()} className="rounded-lg bg-white/10 px-3 py-1.5 hover:bg-white/20 disabled:opacity-30"><Play size={14} /></button></div></form>
  </section>
}
