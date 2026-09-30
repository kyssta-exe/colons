import { Paperclip, Send, Square, X, FileText } from 'lucide-react'
import type { Attachment } from '../lib/types'
import { useEffect, useMemo, useRef, useState } from 'react'

export interface Mentionable {
  id: string
  name: string
  title?: string
}

interface Props {
  onSend: (text: string, attachments?: Attachment[]) => void
  allowAttachments?: boolean
  onCancel: () => void
  streaming: boolean
  disabled?: boolean
  status?: string
  mentionables?: Mentionable[]
  placeholder?: string
}

export function Composer({
  onSend, onCancel, streaming, disabled, status, mentionables, placeholder, allowAttachments,
}: Props) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [attachmentError, setAttachmentError] = useState('')
  const [readingFiles, setReadingFiles] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const [mentionQuery, setMentionQuery] = useState<string | null>(null)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = 'auto'
      ref.current.style.height = Math.min(ref.current.scrollHeight, 200) + 'px'
    }
  }, [text])

  const suggestions = useMemo(() => {
    if (mentionQuery === null || !mentionables?.length) return []
    const q = mentionQuery.toLowerCase()
    return mentionables
      .filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
      .slice(0, 6)
  }, [mentionQuery, mentionables])

  const updateMentionState = (value: string, cursor: number) => {
    const upToCursor = value.slice(0, cursor)
    const match = upToCursor.match(/@([a-zA-Z0-9_-]*)$/)
    setMentionQuery(match ? match[1] : null)
  }

  const insertMention = (handle: string) => {
    const el = ref.current
    const cursor = el?.selectionStart ?? text.length
    const upToCursor = text.slice(0, cursor)
    const replaced = upToCursor.replace(/@([a-zA-Z0-9_-]*)$/, `@${handle} `)
    const next = replaced + text.slice(cursor)
    setText(next)
    setMentionQuery(null)
    requestAnimationFrame(() => {
      el?.focus()
      const pos = replaced.length
      el?.setSelectionRange(pos, pos)
    })
  }

  const addFiles = async (files: File[]) => {
    if (readingFiles || streaming) return
    setAttachmentError('')
    setReadingFiles(true)
    try {
      if (attachments.length + files.length > 3) throw new Error('Attach up to three files per message.')
      const next: Attachment[] = []
      for (const file of files) {
        if (file.size > 2 * 1024 * 1024) throw new Error(`${file.name}: maximum file size is 2 MB.`)
        if (['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
          const dataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader()
            reader.onload = () => resolve(reader.result as string)
            reader.onerror = () => reject(new Error(`Could not read ${file.name}`))
            reader.readAsDataURL(file)
          })
          next.push({ name: file.name, mime_type: file.type, data_url: dataUrl })
        } else {
          if (!/\.(txt|md|csv|tsv|json|yaml|yml|xml|html|css|js|jsx|ts|tsx|py|sh|sql|log|toml)$/i.test(file.name)) throw new Error(`${file.name}: choose an image, text, data, or code file.`)
          const text = await file.text()
          if (text.length > 100000 || text.includes('\0')) throw new Error(`${file.name}: text files must be under 100,000 characters.`)
          next.push({ name: file.name, mime_type: 'text/plain', text })
        }
      }
      setAttachments((prev) => [...prev, ...next])
    } catch (err: any) { setAttachmentError(err.message) }
    finally { setReadingFiles(false); if (fileRef.current) fileRef.current.value = '' }
  }

  const submit = () => {
    const t = text.trim()
    if ((!t && !attachments.length) || streaming || disabled || readingFiles) return
    onSend(t || 'Please review the attached files.', attachments)
    setText('')
    setAttachments([])
    setAttachmentError('')
    setMentionQuery(null)
    if (ref.current) ref.current.style.height = 'auto'
  }

  return (
    <div className="px-4 md:px-6 pb-5 pb-safe relative">
      {suggestions.length > 0 && (
        <div className="max-w-3xl mx-auto mb-1.5">
          <div className="inline-flex flex-col bg-white border border-slate-200 rounded-xl overflow-hidden shadow-lg">
            {suggestions.map((m) => (
              <button
                key={m.id}
                onClick={() => insertMention(m.id)}
                className="flex items-center gap-2 px-3 py-1.5 text-left hover:bg-slate-100 transition-colors"
              >
                <span className="text-[13px] text-slate-800">@{m.id}</span>
                <span className="text-[11px] text-slate-500">{m.name}{m.title ? ` · ${m.title}` : ''}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="max-w-3xl mx-auto">
        {status && status !== 'idle' && streaming && (
          <div className="text-[11px] text-slate-500 mb-1.5 px-1 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-colons-accent animate-pulse" />
            {status}
          </div>
        )}
        {attachmentError && <p role="alert" className="text-xs text-red-600 mb-2 px-2">{attachmentError}</p>}
        <div className="bg-white rounded-[28px] border border-slate-200 shadow-[0_3px_20px_rgba(25,40,65,0.06)] focus-within:border-slate-400 transition-colors p-2.5">
          {!!attachments.length && <div className="flex gap-2 flex-wrap px-2 pt-1 pb-2">{attachments.map((file, index) => <div key={index} className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl p-2 max-w-full">
            {file.data_url ? <img src={file.data_url} alt={file.name} className="w-10 h-10 object-cover rounded-lg" /> : <FileText size={18} className="text-slate-500" />}
            <span className="text-xs truncate max-w-[150px]">{file.name}</span>
            <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setAttachments((prev) => prev.filter((_, i) => i !== index))} className="p-1 text-slate-500 hover:text-slate-900"><X size={14} /></button>
          </div>)}</div>}
          <div className="flex items-end gap-2">
            {allowAttachments && <>
              <input ref={fileRef} type="file" multiple className="hidden" aria-label="Attach files" accept="image/png,image/jpeg,image/webp,image/gif,.txt,.md,.csv,.tsv,.json,.yaml,.yml,.xml,.html,.css,.js,.jsx,.ts,.tsx,.py,.sh,.sql,.log,.toml" onChange={(e) => addFiles(Array.from(e.target.files || []))} />
              <button type="button" onClick={() => fileRef.current?.click()} disabled={streaming || readingFiles} aria-label="Attach files or images" title="Attach files or images" className="w-10 h-10 shrink-0 flex items-center justify-center rounded-full text-slate-500 hover:bg-slate-100"><Paperclip size={19} strokeWidth={1.7} /></button>
            </>}
            <textarea ref={ref} aria-label="Message Colons" value={text}
              onChange={(e) => { setText(e.target.value); updateMentionState(e.target.value, e.target.selectionStart ?? 0) }}
              onPaste={(e) => { if (allowAttachments && e.clipboardData.files.length) { e.preventDefault(); addFiles(Array.from(e.clipboardData.files)) } }}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setMentionQuery(null)
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  if (suggestions.length > 0 && mentionQuery !== null) insertMention(suggestions[0].id)
                  else submit()
                }
              }} rows={1} placeholder={placeholder || 'Ask Colons anything…'}
              className="flex-1 min-w-0 bg-transparent py-2.5 px-1 resize-none outline-none text-sm leading-5 placeholder:text-slate-400 max-h-[200px]" style={{ minHeight: 40 }} />
            {streaming ? <button onClick={onCancel} aria-label="Stop response" title="Stop" className="w-10 h-10 rounded-full bg-slate-200 text-slate-800 flex items-center justify-center shrink-0"><Square size={15} fill="currentColor" /></button>
              : <button onClick={submit} disabled={(!text.trim() && !attachments.length) || disabled || readingFiles} aria-label="Send message" title="Send" className="w-10 h-10 rounded-full bg-colons-accent text-white disabled:bg-slate-100 disabled:text-slate-400 flex items-center justify-center shrink-0 hover:enabled:bg-colons-accentHover"><Send size={19} strokeWidth={1.7} className="-translate-x-px translate-y-px" /></button>}
          </div>
        </div>
        <div className="text-center text-[11px] text-slate-500 mt-2">
          {readingFiles ? 'Reading files…' : 'Enter to send · Shift+Enter for newline'}{mentionables?.length ? ' · @ to mention a teammate' : ''}
        </div>
      </div>
    </div>
  )
}
