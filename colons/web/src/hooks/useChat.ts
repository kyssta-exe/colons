/**
 * Core chat state management with streaming agent events.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ChatSocket } from '../lib/api'
import type { AgentEvent, Attachment, Message, UsageInfo } from '../lib/types'

let msgCounter = 0
const nextId = () => `m${Date.now()}_${msgCounter++}`

export interface ChatState {
  messages: Message[]
  streaming: boolean
  sessionId: string | null
  status: string
  usage: UsageInfo | null
  error: string | null
}

export function useChat(userId = 'default', agentId?: string) {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [status, setStatus] = useState('idle')
  const [usage, setUsage] = useState<UsageInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  const socketRef = useRef<ChatSocket | null>(null)
  const requestIdRef = useRef<string>('')
  const generationRef = useRef(0)

  useEffect(() => () => { socketRef.current?.close() }, [])

  const send = useCallback(async (text: string, attachments: Attachment[] = []) => {
    if ((!text.trim() && !attachments.length) || socketRef.current) return
    setError(null)
    const generation = ++generationRef.current
    requestIdRef.current = ''

    const userMsg: Message = { id: nextId(), role: 'user', content: text, attachments, createdAt: Date.now() }
    const assistantMsg: Message = {
      id: nextId(), role: 'assistant', content: '', toolCalls: [], toolResults: [],
      streaming: true, createdAt: Date.now(),
    }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setStreaming(true)
    setStatus('thinking')

    const socket = new ChatSocket()
    socketRef.current = socket

    const updateAssistant = (patch: Partial<Message> | ((m: Message) => Partial<Message>)) => {
      setMessages((prev) => prev.map((m) => {
        if (m.id !== assistantMsg.id) return m
        const p = typeof patch === 'function' ? patch(m) : patch
        return { ...m, ...p }
      }))
    }

    try {
      for await (const ev of socket.stream(text, { sessionId: sessionId || undefined, userId, agentId, attachments })) {
        if (generation !== generationRef.current) break
        if (ev.type === 'start') requestIdRef.current = ev.request_id
        handleEvent(ev, updateAssistant, setSessionId, setStatus, setUsage, setError)
      }
    } catch (e: any) {
      if (generation !== generationRef.current) return
      setError(e?.message || 'Connection failed')
      updateAssistant({ streaming: false, error: e?.message })
    } finally {
      socket.close()
      if (generation === generationRef.current) {
        updateAssistant({ streaming: false })
        setStreaming(false)
        setStatus('idle')
        socketRef.current = null
        requestIdRef.current = ''
      }
    }
  }, [sessionId, userId, agentId])

  const cancel = useCallback(() => {
    generationRef.current++
    if (requestIdRef.current) socketRef.current?.cancel(requestIdRef.current)
    socketRef.current?.close()
    socketRef.current = null
    requestIdRef.current = ''
    setStreaming(false)
    setStatus('idle')
    setMessages((prev) => prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)))
  }, [])

  const approve = useCallback((id: string, approved: boolean) => {
    try {
      socketRef.current?.approve(id, approved)
      setMessages((prev) => prev.map((m) => ({ ...m, approvals: m.approvals?.map((a) => a.id === id ? { ...a, decision: approved } : a) })))
    } catch (err: any) { setError(err.message) }
  }, [])

  const newChat = useCallback(() => {
    cancel()
    setUsage(null)
    setMessages([])
    setSessionId(null)
    setError(null)
    setStatus('idle')
  }, [cancel])

  const loadSession = useCallback(async (id: string) => {
    cancel()
    const generation = generationRef.current
    try {
      const data = await api.getSession(id, userId, agentId)
      if (generation !== generationRef.current) return
      setSessionId(id)
      const loaded: Message[] = (data.messages || []).map((m: any) => ({
        id: nextId(),
        role: m.role,
        content: m.content,
        attachments: m.attachments,
        createdAt: (m.metadata?.timestamp || Date.now() / 1000) * 1000,
      }))
      setMessages(loaded)
      setError(null)
      setUsage(null)
    } catch (e: any) {
      if (generation === generationRef.current) setError(e.message || 'Could not load conversation')
    }
  }, [userId, agentId, cancel])

  return { messages, streaming, sessionId, status, usage, error, send, cancel, approve, newChat, loadSession }
}

function handleEvent(
  ev: AgentEvent,
  update: (patch: Partial<Message> | ((m: Message) => Partial<Message>)) => void,
  setSessionId: (id: string) => void,
  setStatus: (s: string) => void,
  setUsage: (u: UsageInfo) => void,
  setError: (e: string | null) => void,
) {
  switch (ev.type) {
    case 'start':
      setSessionId(ev.session_id)
      break
    case 'status':
      setStatus(ev.state)
      break
    case 'delta':
      update((m) => ({ content: m.content + ev.content }))
      break
    case 'reasoning':
      update((m) => ({ reasoning: (m.reasoning || '') + ev.content }))
      break
    case 'tool_call':
      update((m) => ({
        toolCalls: [...(m.toolCalls || []), { id: ev.tool_call_id, name: ev.tool, arguments: ev.arguments }],
      }))
      setStatus(`running ${ev.tool}`)
      break
    case 'tool_result':
      update((m) => ({
        toolResults: [...(m.toolResults || []), {
          id: ev.tool_call_id, tool: ev.tool, success: ev.success,
          output: ev.output, error: ev.error, duration_ms: ev.duration_ms,
        }],
      }))
      setStatus('thinking')
      break
    case 'approval_required':
      if (ev.approval_id) update((m) => ({ approvals: [...(m.approvals || []), { id: ev.approval_id!, tool: ev.tool, arguments: ev.arguments, reason: ev.reason }] }))
      setStatus(`approval needed: ${ev.tool}`)
      break
    case 'usage':
      setUsage({
        prompt_tokens: ev.prompt_tokens, completion_tokens: ev.completion_tokens,
        total_tokens: ev.total_tokens, cached_tokens: ev.cached_tokens,
      })
      break
    case 'error':
      setError(ev.message)
      update({ error: ev.message })
      break
    case 'done':
      update((m) => ({ streaming: false, content: ev.content || m.content }))
      setStatus('idle')
      break
    default:
      break
  }
}
