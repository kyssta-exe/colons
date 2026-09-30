export type Role = 'user' | 'assistant' | 'system' | 'tool'

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

export interface ToolResult {
  id: string
  tool: string
  success: boolean
  output?: unknown
  error?: string
  duration_ms?: number
}

export interface Message {
  id: string
  role: Role
  content: string
  reasoning?: string
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
  streaming?: boolean
  error?: string
  approvals?: ApprovalInfo[]
  attachments?: Attachment[]
  createdAt: number
}

export interface SessionInfo {
  id: string
  title: string
  created_at: number
  updated_at: number
  message_count: number
}

export interface AvatarInfo {
  id: string
  name: string
  kind: 'character' | 'pet'
  colors: string[]
  personality: string
  emoji: string
  description: string
}

export interface ToolInfo {
  name: string
  description: string
  category: string
  permission: string
  danger: boolean
  returns: string
}

export interface BotInfo {
  id: string
  name: string
  title: string
  description: string
  persona: string
  avatar: string
  model: string
  owner: string
  enabled: boolean
  created_at: number
  handle: string
  agent_id?: string
  state?: string
  avatar_def?: AvatarInfo | null
}

export interface RoomInfo {
  id: string
  name: string
  owner: string
  members: string[]
  created_at: number
  metadata: Record<string, unknown>
  message_count: number
  needs_user: boolean
  member_defs: BotInfo[]
}

export interface RoomMessage {
  id?: number
  room_id: string
  speaker: string
  content: string
  created_at: number
  metadata: Record<string, unknown>
}

export interface ProviderInfo {
  name: string
  display: string
  kind: string
  base_url: string
  default_model: string
  env: string[]
  configured: boolean
  local: boolean
}

export interface UsageInfo {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  tool_stats?: Record<string, { calls: number; errors: number; avg_ms: number }>
}

/** Events streamed from the agent over WebSocket */
export type AgentEvent =
  | { type: 'start'; request_id: string; session_id: string; timestamp: number }
  | { type: 'status'; state: string; message?: string; iteration?: number; max_iterations?: number; timestamp: number }
  | { type: 'delta'; content: string; timestamp: number }
  | { type: 'reasoning'; content: string; timestamp: number }
  | { type: 'tool_call'; tool: string; arguments: Record<string, unknown>; tool_call_id: string; timestamp: number }
  | { type: 'tool_result'; tool: string; success: boolean; output?: unknown; error?: string; duration_ms?: number; tool_call_id: string; timestamp: number }
  | { type: 'approval_required'; approval_id?: string; tool: string; arguments: Record<string, unknown>; reason: string; timestamp: number }
  | { type: 'usage'; prompt_tokens: number; completion_tokens: number; total_tokens: number; cached_tokens: number; iterations: number; tool_calls: number; timestamp: number }
  | { type: 'error'; message: string; timestamp: number }
  | { type: 'done'; session_id: string; content: string; request_id: string; timestamp: number }
  | { type: 'pong'; timestamp: number }

export interface TaskInfo {
  id: string
  description: string
  status: 'pending' | 'planning' | 'running' | 'completed' | 'failed' | 'cancelled'
  plan: string[]
  result: string | null
  error: string | null
  created_at: number
}

export interface Attachment {
  name: string
  mime_type: string
  text?: string
  data_url?: string
}

export interface ApprovalInfo { id: string; tool: string; arguments: Record<string, unknown>; reason: string; decision?: boolean }
