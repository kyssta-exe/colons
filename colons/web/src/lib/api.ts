/**
 * REST client + WebSocket streaming client for Colons.
 */
import type {
  Attachment, TaskInfo,
  AgentEvent, AvatarInfo, BotInfo, ProviderInfo, RoomInfo, RoomMessage,
  SessionInfo, ToolInfo, UsageInfo,
} from './types'

const API_BASE = (import.meta as any).env?.VITE_API_URL || ''

function apiKey(): string | null {
  return localStorage.getItem('colons_api_key')
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  const key = apiKey()
  if (key) h['Authorization'] = `Bearer ${key}`
  return h
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers: { ...headers(), ...(options.headers || {}) } })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    throw new Error(`${res.status}: ${detail}`)
  }
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json() as Promise<T>
  return res as unknown as T
}

export const api = {
  // Meta
  health: () => request<any>('/health'),
  config: () => request<any>('/api/config'),

  // Sessions
  listSessions: (userId = 'default', agentId?: string) =>
    request<{ sessions: SessionInfo[] }>(
      `/api/sessions?user_id=${userId}${agentId ? `&agent_id=${agentId}` : ''}`),
  getSession: (id: string, userId = 'default', agentId?: string) =>
    request<any>(`/api/sessions/${id}?user_id=${userId}${agentId ? `&agent_id=${agentId}` : ''}`),
  deleteSession: (id: string, userId = 'default', agentId?: string) =>
    request<any>(`/api/sessions/${id}?user_id=${userId}${agentId ? `&agent_id=${agentId}` : ''}`,
      { method: 'DELETE' }),

  // Memory
  searchMemory: (q: string, userId = 'default', agentId?: string) =>
    request<{ results: any[] }>(`/api/memory/search?q=${encodeURIComponent(q)}&user_id=${encodeURIComponent(userId)}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ''}`),
  memoryHistory: (userId = 'default', limit = 50) =>
    request<{ entries: any[] }>(`/api/memory/history?user_id=${userId}&limit=${limit}`),

  // Tools
  listTools: (userId = 'default') =>
    request<{ tools: ToolInfo[]; stats: Record<string, any> }>(`/api/tools?user_id=${userId}`),
  executeTool: (tool: string, args: Record<string, unknown>, userId = 'default', agentId?: string) =>
    request<any>('/api/tools/execute', {
      method: 'POST',
      body: JSON.stringify({ tool, arguments: args, user_id: userId, agent_id: agentId }),
    }),

  terminalCommand: (command: string, cwd: string | undefined, userId: string, agentId: string) =>
    request<any>('/api/terminal/command', { method: 'POST', body: JSON.stringify({ command, cwd, user_id: userId, agent_id: agentId }) }),

  permissionSettings: () => request<{ mode: string; workspace: string; disabled_tools: string[] }>('/api/settings/permissions'),
  setPermissionMode: (mode: string) => request<{ mode: string; workspace: string; disabled_tools: string[] }>('/api/settings/permissions', { method: 'PUT', body: JSON.stringify({ mode }) }),

  // Avatars
  listAvatars: () => request<{ avatars: AvatarInfo[] }>('/api/avatars'),
  selectAvatar: (agentId: string, avatarId: string, userId = 'default') =>
    request<any>('/api/avatars/select', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, avatar_id: avatarId, user_id: userId }),
    }),

  // Voice
  listVoices: (locale?: string) =>
    request<{ engine: string | null; voices: any[] }>(`/api/voice/voices${locale ? `?locale=${locale}` : ''}`),
  tts: async (text: string, voice?: string): Promise<Blob> => {
    const res = await fetch(`${API_BASE}/api/voice/tts`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ text, voice }),
    })
    if (!res.ok) throw new Error(`TTS failed: ${res.status}`)
    return res.blob()
  },

  // Providers
  listProviders: () =>
    request<{ providers: ProviderInfo[]; active: any }>('/api/providers'),
  switchProvider: (name: string, apiKey?: string, baseUrl?: string, model?: string) =>
    request<any>('/api/providers/switch', {
      method: 'POST',
      body: JSON.stringify({ name, api_key: apiKey, base_url: baseUrl, model }),
    }),

  // Usage & status
  usage: (userId = 'default', agentId?: string) => request<UsageInfo>(`/api/usage?user_id=${encodeURIComponent(userId)}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ''}`),
  agentStatus: (agentId: string, userId = 'default') =>
    request<any>(`/api/agents/${agentId}/status?user_id=${userId}`),

  // Tasks
  createTask: (description: string, userId = 'default', agentId?: string) =>
    request<any>('/api/tasks', { method: 'POST', body: JSON.stringify({ description, user_id: userId, agent_id: agentId }) }),
  listTasks: (userId = 'default', agentId?: string) => request<{ tasks: TaskInfo[] }>(`/api/tasks?user_id=${encodeURIComponent(userId)}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ''}`),
  runTask: (id: string, userId: string, agentId: string) => request<any>(`/api/tasks/${id}/run?user_id=${encodeURIComponent(userId)}&agent_id=${encodeURIComponent(agentId)}`, { method: 'POST' }),
  setAgentPaused: (id: string, paused: boolean, userId: string) => request<any>(`/api/agents/${id}/${paused ? 'pause' : 'resume'}?user_id=${encodeURIComponent(userId)}`, { method: 'POST' }),

  // Schedules (cronjobs)
  listSchedules: () => request<{ schedules: any[]; status: any }>('/api/schedules'),
  createSchedule: (cron: string, prompt: string, name?: string, userId = 'default', agentId?: string) =>
    request<any>('/api/schedules', {
      method: 'POST',
      body: JSON.stringify({ cron, prompt, name: name || '', user_id: userId, agent_id: agentId }),
    }),
  updateSchedule: (id: string, changes: Record<string, unknown>) =>
    request<any>(`/api/schedules/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  deleteSchedule: (id: string) =>
    request<any>(`/api/schedules/${id}`, { method: 'DELETE' }),
  runSchedule: (id: string) =>
    request<any>(`/api/schedules/${id}/run`, { method: 'POST' }),

  // Messaging
  messagingConfig: () => request<{ services: any[] }>('/api/messaging/config'),
  configureMessaging: (service: string, settings: Record<string, unknown>) =>
    request<{ services: any[] }>(`/api/messaging/config/${service}`, { method: 'PUT', body: JSON.stringify(settings) }),
  messagingStatus: () =>
    request<{ enabled: boolean; adapters: any[] }>('/api/messaging/status'),

  // Native browser
  browserStatus: (userId: string, agentId: string) => request<any>(`/api/browser?user_id=${encodeURIComponent(userId)}&agent_id=${encodeURIComponent(agentId)}`),
  browserScreenshot: async (userId: string, agentId: string, pageId: string): Promise<Blob> => {
    const res = await fetch(`${API_BASE}/api/browser/screenshot?user_id=${encodeURIComponent(userId)}&agent_id=${encodeURIComponent(agentId)}&page_id=${encodeURIComponent(pageId)}`, { headers: headers() })
    if (!res.ok) throw new Error(`Screenshot failed: ${res.status}`)
    return res.blob()
  },

  // Debug
  debugStats: () => request<any>('/api/debug/stats'),
  doctor: () => request<any>('/api/doctor?check_provider=false'),

  // Bots
  listBots: () => request<{ bots: BotInfo[] }>('/api/bots'),
  createBot: (data: Partial<BotInfo>) =>
    request<BotInfo>('/api/bots', { method: 'POST', body: JSON.stringify(data) }),
  updateBot: (id: string, changes: Partial<BotInfo>) =>
    request<BotInfo>(`/api/bots/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  deleteBot: (id: string) => request<any>(`/api/bots/${id}`, { method: 'DELETE' }),

  // Rooms
  listRooms: (userId = 'default') =>
    request<{ rooms: RoomInfo[] }>(`/api/rooms?owner=${userId}`),
  createRoom: (name: string, members: string[], userId = 'default') =>
    request<RoomInfo>('/api/rooms', {
      method: 'POST',
      body: JSON.stringify({ name, members, owner: userId }),
    }),
  deleteRoom: (id: string) => request<any>(`/api/rooms/${id}`, { method: 'DELETE' }),
  roomMessages: (id: string, limit = 200) =>
    request<{ messages: RoomMessage[] }>(`/api/rooms/${id}/messages?limit=${limit}`),
  sendRoomMessage: (id: string, message: string) =>
    request<{ messages: RoomMessage[] }>(`/api/rooms/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),
  roomSeen: (id: string) =>
    request<any>(`/api/rooms/${id}/seen`, { method: 'POST' }),
}

/** WebSocket streaming chat */
export class ChatSocket {
  private ws: WebSocket | null = null
  private url: string

  constructor() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const base = API_BASE || `${proto}//${location.host}`
    const wsBase = base.replace(/^http/, 'ws').replace(/^https/, 'wss')
    const key = apiKey()
    this.url = `${wsBase}/ws/chat${key ? `?api_key=${encodeURIComponent(key)}` : ''}`
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url)
      this.ws.onopen = () => resolve()
      this.ws.onerror = () => reject(new Error('Could not connect to Colons'))
      this.ws.onclose = () => reject(new Error('Connection closed before chat started'))
    })
  }

  send(payload: Record<string, unknown>) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error('Socket not connected')
    this.ws.send(JSON.stringify(payload))
  }

  async *stream(message: string, opts: { sessionId?: string; userId?: string; agentId?: string; attachments?: Attachment[] } = {}): AsyncGenerator<AgentEvent> {
    await this.connect()
    const ws = this.ws!
    const queue: AgentEvent[] = []
    let done = false
    let error: unknown = null
    let notify: (() => void) | null = null

    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as AgentEvent
        queue.push(ev)
        if (ev.type === 'done' || ev.type === 'error') done = true
      } catch {
        /* ignore malformed frames */
      }
      notify?.()
    }
    ws.onclose = () => {
      if (!done) error = new Error('Connection interrupted before the reply finished')
      done = true
      notify?.()
    }
    ws.onerror = () => { error = new Error('Chat connection failed'); done = true; notify?.() }

    this.send({ type: 'chat', message, session_id: opts.sessionId,
      user_id: opts.userId || 'default', agent_id: opts.agentId, attachments: opts.attachments, stream: true })
    while (!done || queue.length > 0) {
      if (queue.length === 0) {
        await new Promise<void>((r) => { notify = r })
        notify = null
        continue
      }
      yield queue.shift()!
    }
    if (error) throw error
    this.close()
  }

  approve(approvalId: string, approved: boolean) {
    this.send({ type: 'approval', approval_id: approvalId, approved })
  }

  cancel(requestId: string) {
    if (this.ws?.readyState === WebSocket.OPEN) this.send({ type: 'cancel', request_id: requestId })
  }

  close() {
    this.ws?.close()
    this.ws = null
  }
}
