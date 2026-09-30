import { useState } from 'react'
import { ArrowDownLeft, ArrowUpRight, Check, Database, RefreshCw, Wrench } from 'lucide-react'
import type { UsageInfo } from '../lib/types'

const count = (value: number) => Number.isFinite(value) ? Math.max(0, value) : 0
const format = (value: number) => count(value).toLocaleString()
const percent = (value: number, total: number) => total ? Math.round(value / total * 100) : 0

export function UsagePanel({ usage, onRefresh }: { usage: UsageInfo; onRefresh: () => Promise<void> }) {
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [showAll, setShowAll] = useState(false)
  const input = count(usage.prompt_tokens), output = count(usage.completion_tokens)
  const tokenMix = input + output
  const tools = Object.entries(usage.tool_stats || {}).map(([name, stats]) => ({
    name, calls: count(stats.calls), errors: Math.min(count(stats.errors), count(stats.calls)), ms: count(stats.avg_ms),
  })).sort((a, b) => b.calls - a.calls || a.name.localeCompare(b.name))
  const calls = tools.reduce((total, tool) => total + tool.calls, 0)
  const errors = tools.reduce((total, tool) => total + tool.errors, 0)
  const refresh = async () => {
    setRefreshing(true); setError('')
    try { await onRefresh() } catch (e: any) { setError(e.message || 'Could not refresh usage.') }
    finally { setRefreshing(false) }
  }
  return <div className="space-y-5">
    <section aria-label="Token usage" className="rounded-2xl border border-slate-200/80 bg-[#fafbfc] p-5 sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <div><p className="text-xs font-medium text-slate-500">Total tokens</p><p className="mt-2 text-4xl font-semibold tracking-tight tabular-nums text-slate-900">{format(usage.total_tokens)}</p></div>
        <button aria-label="Refresh usage" disabled={refreshing} onClick={() => void refresh()} className="rounded-full border border-slate-200 bg-white p-2.5 text-slate-500 hover:text-slate-800 hover:bg-slate-50 disabled:opacity-50"><RefreshCw size={16} strokeWidth={1.7} className={refreshing ? 'animate-spin' : ''} /></button>
      </div>
      <p className="text-[11px] text-slate-400 mt-2">Current agent · Since the server started</p>
      <div role="img" aria-label={`Token mix: ${format(input)} input, ${format(output)} output`} className="mt-6 flex h-2.5 overflow-hidden rounded-full bg-slate-200/70">
        <div className="bg-[#7d95b8] transition-[width] duration-500" style={{ width: `${tokenMix ? input / tokenMix * 100 : 0}%` }} />
        <div className="bg-[#b8c7dc] transition-[width] duration-500" style={{ width: `${tokenMix ? output / tokenMix * 100 : 0}%` }} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4">
        {[{ label: 'Input', value: input, icon: ArrowDownLeft, color: '#7d95b8' }, { label: 'Output', value: output, icon: ArrowUpRight, color: '#b8c7dc' }].map(({ label, value, icon: Icon, color }) => <div key={label}>
          <div className="flex items-center gap-2 text-xs text-slate-500"><span className="h-2 w-2 rounded-full" style={{ background: color }} /><span>{label}</span><Icon size={13} strokeWidth={1.5} className="text-slate-400" /></div>
          <p className="mt-2 text-lg font-medium tabular-nums text-slate-800">{format(value)} <span className="ml-1 text-[11px] font-normal text-slate-400">{percent(value, tokenMix)}%</span></p>
        </div>)}
      </div>
    </section>
    {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-xs text-red-700">{error}</p>}
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <section className="rounded-2xl border border-slate-200/80 px-5 py-4"><div className="flex items-center gap-2 text-xs text-slate-500"><Database size={15} strokeWidth={1.6} />Cached tokens</div><p className="mt-3 text-2xl font-semibold tracking-tight tabular-nums">{format(usage.cached_tokens)}</p><p className="mt-1.5 text-[11px] text-slate-400">Reported separately by your provider</p></section>
      <section className="rounded-2xl border border-slate-200/80 px-5 py-4"><div className="flex items-center gap-2 text-xs text-slate-500"><Wrench size={15} strokeWidth={1.6} />Tool calls</div><div className="mt-3 flex items-baseline gap-3"><p className="text-2xl font-semibold tracking-tight tabular-nums">{format(calls)}</p>{calls > 0 && <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700"><Check size={11} />{percent(calls - errors, calls)}% successful</span>}</div><p className="mt-1.5 text-[11px] text-slate-400">{tools.filter((tool) => tool.calls > 0).length} tools used{errors > 0 ? ` · ${format(errors)} failed calls` : ''}</p></section>
    </div>
    <section aria-label="Tool activity" className="rounded-2xl border border-slate-200/80 p-5 sm:p-6">
      <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-semibold">Tool activity</h4>{calls > 0 && <span className="text-[11px] text-slate-400">Share of calls</span>}</div>
      {calls > 0 ? <>
        <div className="mt-5 space-y-5">{(showAll ? tools : tools.slice(0, 5)).filter((tool) => tool.calls > 0).map((tool) => <div key={tool.name}>
          <div className="flex justify-between items-baseline gap-3 text-xs"><span className="font-medium text-slate-700 break-all">{tool.name.replaceAll('_', ' ')}</span><span className="shrink-0 text-slate-500 tabular-nums">{format(tool.calls)} <span className="text-slate-400">calls</span></span></div>
          <div role="img" aria-label={`${tool.name.replaceAll('_', ' ')}: ${tool.calls} calls, ${tool.errors} errors, ${percent(tool.calls, calls)}% of calls`} className="mt-2.5 h-1.5 rounded-full overflow-hidden bg-slate-100">
            <div className="flex h-full overflow-hidden rounded-full transition-[width] duration-500" style={{ width: `${tool.calls / calls * 100}%` }}><div className="bg-[#9aadc9]" style={{ width: `${(tool.calls - tool.errors) / tool.calls * 100}%` }} /><div className="bg-[#d9a29c]" style={{ width: `${tool.errors / tool.calls * 100}%` }} /></div>
          </div>
          <div className="flex justify-between mt-2 gap-3 text-[11px] text-slate-400"><span>{Math.round(tool.ms).toLocaleString()} ms avg{tool.errors > 0 && <span className="text-[#aa716b]"> · {format(tool.errors)} failed</span>}</span><span className="tabular-nums">{percent(tool.calls, calls)}%</span></div>
        </div>)}</div>
        {tools.filter((tool) => tool.calls > 0).length > 5 && <button onClick={() => setShowAll(!showAll)} className="mt-5 text-xs text-colons-accent hover:underline">{showAll ? 'Show fewer tools' : `Show all ${tools.filter((tool) => tool.calls > 0).length} tools`}</button>}
        {errors > 0 && <div className="mt-5 border-t border-slate-100 pt-3 flex gap-4 text-[10px] text-slate-400"><span className="flex items-center gap-1.5"><i className="h-1.5 w-1.5 rounded-full bg-[#9aadc9]" />Successful</span><span className="flex items-center gap-1.5"><i className="h-1.5 w-1.5 rounded-full bg-[#d9a29c]" />Failed</span></div>}
      </> : <div className="mt-5 flex items-center gap-3 rounded-xl bg-slate-50 p-4"><Wrench size={18} strokeWidth={1.5} className="text-slate-400 shrink-0" /><div><p className="text-xs font-medium text-slate-600">A fresh start</p><p className="mt-1 text-xs text-slate-400 leading-relaxed">Tool activity will appear here as your agent gets to work.</p></div></div>}
    </section>
  </div>
}
