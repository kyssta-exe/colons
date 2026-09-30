import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import type { ToolCall, ToolResult } from '../lib/types'

function ToolResultView({ result }: { result: ToolResult | undefined }) {
  if (!result) {
    return <div className="text-xs text-slate-500 mt-1">running…</div>
  }
  const output = result.output !== undefined
    ? typeof result.output === 'string' ? result.output : JSON.stringify(result.output, null, 2)
    : result.error || '(no output)'
  return (
    <pre className={`mt-1 text-[11px] leading-snug max-h-56 overflow-auto whitespace-pre-wrap rounded-md p-2
      ${result.success ? 'bg-slate-50 text-slate-700' : 'bg-red-50/40 text-red-700'}`}>
      {output.length > 4000 ? output.slice(0, 4000) + '\n…' : output}
    </pre>
  )
}

export function ToolCallCard({ call, result }: { call: ToolCall; result?: ToolResult }) {
  const [open, setOpen] = useState(false)
  const pending = !result
  const ok = result?.success
  const args = JSON.stringify(call.arguments)

  return (
    <div className="my-2 border border-slate-200 rounded-lg overflow-hidden bg-slate-50/50">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-100/50 transition-colors"
      >
        <span className={`w-2 h-2 rounded-full ${pending ? 'bg-amber-400 animate-pulse' : ok ? 'bg-emerald-400' : 'bg-red-400'}`} />
        <span className="text-xs font-mono text-slate-700">{call.name}</span>
        <span className="text-[11px] text-slate-500 truncate flex-1 font-mono">
          {args.length > 80 ? args.slice(0, 80) + '…' : args}
        </span>
        {result?.duration_ms !== undefined && (
          <span className="text-[10px] text-slate-500">{Math.round(result.duration_ms)}ms</span>
        )}
        <ChevronDown size={14} strokeWidth={1.6} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="px-3 pb-3 border-t border-slate-200">
          <div className="text-[10px] uppercase tracking-wide text-slate-500 mt-2 mb-1">arguments</div>
          <pre className="text-[11px] bg-white rounded-md p-2 overflow-auto max-h-40 text-slate-600">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          <div className="text-[10px] uppercase tracking-wide text-slate-500 mt-2 mb-1">result</div>
          <ToolResultView result={result} />
        </div>
      )}
    </div>
  )
}
