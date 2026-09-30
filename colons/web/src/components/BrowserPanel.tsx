import { useCallback, useEffect, useRef, useState } from 'react'
import { Globe, Camera, ExternalLink } from 'lucide-react'
import { api } from '../lib/api'

export function BrowserPanel({ userId, agentId }: { userId: string; agentId: string }) {
  const [status, setStatus] = useState<any>(null)
  const [url, setUrl] = useState('')
  const [snapshot, setSnapshot] = useState<any>(null)
  const [image, setImage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const imageRef = useRef('')
  const revision = useRef(0)
  const refresh = useCallback(async () => {
    const current = revision.current
    try { const result = await api.browserStatus(userId, agentId); if (current === revision.current) setStatus(result) }
    catch (err: any) { if (current === revision.current) setError(err.message) }
  }, [userId, agentId])
  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => { clearInterval(timer); revision.current++; if (imageRef.current) URL.revokeObjectURL(imageRef.current) }
  }, [refresh])
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await operation(); await refresh() } catch (err: any) { setError(err.message) } finally { setBusy(false) }
  }
  const open = () => run(async () => {
    const result = await api.executeTool('browser_open', { url: url.trim() }, userId, agentId)
    if (result.status !== 'ok') throw new Error(result.error || 'Could not open page')
    setSnapshot(result.output)
    if (imageRef.current) URL.revokeObjectURL(imageRef.current)
    imageRef.current = ''; setImage('')
  })
  const capture = (pageId: string) => run(async () => {
    const blob = await api.browserScreenshot(userId, agentId, pageId)
    if (imageRef.current) URL.revokeObjectURL(imageRef.current)
    imageRef.current = URL.createObjectURL(blob)
    setImage(imageRef.current)
  })
  return <div className="space-y-5">
    <div className="flex gap-3"><Globe size={24} className="text-colons-accent" /><div><h2 className="font-medium">Native browser</h2><p className="text-sm text-slate-500 mt-1">A warm Chromium session for this agent. Fast page reads, stable element references, and visual inspection.</p></div></div>
    {error && <p role="alert" className="text-sm text-red-600 whitespace-pre-wrap">{error}</p>}
    {status && <p className="text-xs text-slate-500">{status.running ? 'Browser running' : status.available ? 'Ready to launch' : 'Browser support needs installation'} · {status.pages.length} open tabs · isolated agent session</p>}
    {status && !status.available && <div className="bg-slate-50 border border-slate-200 p-4 rounded-xl text-sm"><p>Install the browser extra and Chromium on your server:</p><code className="block mt-2 text-xs">pip install 'colons[browser]' &amp;&amp; playwright install chromium</code></div>}
    <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); open() }}><input aria-label="Browser URL" value={url} onChange={(e) => setUrl(e.target.value)} type="url" placeholder="https://example.com" required className="flex-1 min-w-0 text-sm rounded-lg border border-slate-200 px-3 py-2" /><button disabled={busy || !url.trim()} className="bg-colons-accent text-white rounded-lg px-4 py-2 text-sm disabled:opacity-40">Open</button></form>
    <div className="space-y-2">{status?.pages.map((page: any) => <div key={page.id} className="flex flex-wrap items-center gap-3 border border-slate-200 rounded-xl p-3 text-xs">
      <span className="flex-1 min-w-0 break-all">{page.url}</span>
      <button disabled={busy} onClick={() => capture(page.id)} className="flex items-center gap-1.5 text-colons-accent hover:underline"><Camera size={15} /> Capture</button>
      <a href={page.url.startsWith('http') ? page.url : undefined} target="_blank" rel="noreferrer" aria-label="Open page in your browser"><ExternalLink size={15} /></a>
      <button disabled={busy} onClick={() => run(async () => {
        const result = await api.executeTool('browser_close_tab', { page_id: page.id }, userId, agentId)
        if (result.status !== 'ok') throw new Error(result.error)
        setSnapshot(null); setImage('')
      })} className="text-slate-500 hover:text-red-600">Close</button>
    </div>)}</div>
    {image && <img src={image} alt="Native browser page capture" className="w-full rounded-xl border border-slate-200" />}
    {snapshot && <section className="border border-slate-200 rounded-xl p-4"><h3 className="text-sm font-medium">{snapshot.title || snapshot.url}</h3><pre className="mt-3 text-xs font-sans text-slate-600 whitespace-pre-wrap max-h-96 overflow-y-auto">{snapshot.text}</pre><p className="text-xs text-slate-500 mt-3">{snapshot.controls?.length || 0} actionable elements</p></section>}
    <p className="text-xs text-slate-500 leading-relaxed">Ask your agent to browse in chat. It can navigate, read pages, fill fields, click controls, press keys, and capture screenshots. Actions that interact with a page ask for approval in chat.</p>
  </div>
}
