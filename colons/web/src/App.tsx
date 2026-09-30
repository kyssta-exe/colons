import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './lib/api'
import type { AvatarInfo, BotInfo, RoomInfo, SessionInfo } from './lib/types'
import { useChat } from './hooks/useChat'
import { Sidebar } from './components/Sidebar'
import { Composer } from './components/Composer'
import { MessageBubble } from './components/MessageBubble'
import { SettingsPanel } from './components/SettingsPanel'
import { BotModal, RoomModal } from './components/Modals'
import { BotAvatar } from './components/BotAvatar'
import { PanelLeft, PanelRight, Terminal } from 'lucide-react'
import { TerminalPanel } from './components/TerminalPanel'
import { Icon, type IconName } from './components/Icon'
import { Dashboard, type SetupTab } from './components/Dashboard'
import { ConversationPanel } from './components/ConversationPanel'
import { RoomView } from './components/RoomView'

const USER_ID = localStorage.getItem('colons_user') || 'default'

export default function App() {
  const [view, setView] = useState<'dashboard' | 'chat'>('dashboard')
  const [setupTab, setSetupTab] = useState<SetupTab>('provider')
  const [bots, setBots] = useState<BotInfo[]>([])
  const [avatars, setAvatars] = useState<AvatarInfo[]>([])
  const [activeBotId, setActiveBotId] = useState('default')
  const [rooms, setRooms] = useState<RoomInfo[]>([])
  const [activeRoomId, setActiveRoomId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [leftCollapsed, setLeftCollapsed] = useState(localStorage.getItem('colons_left_collapsed') === 'true')
  const [rightCollapsed, setRightCollapsed] = useState(localStorage.getItem('colons_right_collapsed') === 'true')
  const [narrow, setNarrow] = useState(window.innerWidth < 1280)
  const [mobileDetailsOpen, setMobileDetailsOpen] = useState(false)
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1279px)')
    const change = () => { setNarrow(query.matches); setMobileDetailsOpen(false) }
    query.addEventListener('change', change)
    return () => query.removeEventListener('change', change)
  }, [])
  const [terminalOpen, setTerminalOpen] = useState(false)
  const toggleNavigation = () => {
    if (window.innerWidth < 768) setSidebarOpen((value) => !value)
    else setLeftCollapsed((value) => !value)
  }
  useEffect(() => { localStorage.setItem('colons_left_collapsed', String(leftCollapsed)) }, [leftCollapsed])
  useEffect(() => { localStorage.setItem('colons_right_collapsed', String(rightCollapsed)) }, [rightCollapsed])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [botModalOpen, setBotModalOpen] = useState(false)
  const [roomModalOpen, setRoomModalOpen] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [speakingMessageId, setSpeakingMessageId] = useState<string | null>(null)
  useEffect(() => {
    document.documentElement.dataset.colonsMotion = localStorage.getItem('colons_motion') === 'off' ? 'off' : 'on'
  }, [])
  const [voice, setVoice] = useState(localStorage.getItem('colons_voice') || '')
  const endRef = useRef<HTMLDivElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const audioUrlRef = useRef<string | null>(null)
  const agentIdRef = useRef('')

  const activeBot = bots.find((b) => b.id === activeBotId)
  const agentId = `bot-${activeBotId}`
  agentIdRef.current = agentId
  const { messages, streaming, sessionId, status, usage, error, send, cancel, approve, newChat, loadSession } =
    useChat(USER_ID, agentId)
  const openSetup = (tab: SetupTab = 'provider') => { setSetupTab(tab); setSettingsOpen(true); setSidebarOpen(false) }

  const refreshBots = useCallback(() => {
    api.listBots().then((d) => {
      setBots(d.bots)
      if (d.bots.length && !d.bots.some((b) => b.id === activeBotId)) {
        setActiveBotId(d.bots[0].id)
      }
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBotId])

  const refreshRooms = useCallback(() => {
    api.listRooms(USER_ID).then((d) => setRooms(d.rooms)).catch(() => {})
  }, [])

  const refreshSessions = useCallback(() => {
    api.listSessions(USER_ID, agentId).then((d) => { if (agentIdRef.current === agentId) setSessions(d.sessions) }).catch(() => {})
  }, [agentId])

  useEffect(() => {
    refreshBots()
    refreshRooms()
    api.listAvatars().then((d) => setAvatars(d.avatars)).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    newChat()
    setSessions([])
    refreshSessions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBotId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (!streaming) refreshSessions()
  }, [streaming, refreshSessions])

  useEffect(() => () => {
    audioRef.current?.pause()
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current)
  }, [])

  const speak = async (text: string, messageId: string) => {
    try {
      setSpeakingMessageId(messageId)
      setSpeaking(false)
      const blob = await api.tts(text, voice || undefined)
      audioRef.current?.pause()
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current)
      const url = URL.createObjectURL(blob)
      audioUrlRef.current = url
      const audio = audioRef.current || new Audio()
      audioRef.current = audio
      audio.src = url
      audio.onended = () => { setSpeaking(false); URL.revokeObjectURL(url); audioUrlRef.current = null }
      await audio.play()
      setSpeaking(true)
    } catch (e) {
      setSpeaking(false)
      if (audioUrlRef.current) { URL.revokeObjectURL(audioUrlRef.current); audioUrlRef.current = null }
      console.error('TTS failed', e)
    }
  }

  const selectBot = (id: string) => {
    setView('chat')
    setActiveBotId(id)
    setActiveRoomId(null)
    setSidebarOpen(false)
  }

  const deleteBot = async (id: string) => {
    await api.deleteBot(id).catch(() => {})
    if (activeBotId === id) setActiveBotId('default')
    refreshBots()
  }

  const deleteSession = async (id: string) => {
    await api.deleteSession(id, USER_ID, agentId).catch(() => {})
    if (sessionId === id) newChat()
    refreshSessions()
  }

  const activeRoom = rooms.find((r) => r.id === activeRoomId) || null
  const mentionables = bots.map((b) => ({ id: b.id, name: b.name, title: b.title }))

  return (
    <div className="app-shell flex h-full bg-[#fdfdfc]">
      <Sidebar
        view={view}
        onDashboard={() => { setView('dashboard'); setSidebarOpen(false); refreshBots(); refreshRooms() }}
        onChat={() => { setView('chat'); setActiveRoomId(null); setSidebarOpen(false) }}
        open={sidebarOpen}
        collapsed={leftCollapsed}
        onClose={() => { setSidebarOpen(false); if (window.innerWidth >= 768) setLeftCollapsed(true) }}
        sessions={sessions}
        activeSession={sessionId}
        onSelect={(id) => { setView('chat'); setActiveRoomId(null); loadSession(id) }}
        onNew={() => { setView('chat'); setActiveRoomId(null); newChat(); setSidebarOpen(false) }}
        onDelete={deleteSession}
        onOpenSettings={() => openSetup()}
        bots={bots}
        activeBotId={activeBotId}
        onSelectBot={selectBot}
        onCreateBot={() => setBotModalOpen(true)}
        onDeleteBot={deleteBot}
        rooms={rooms}
        activeRoomId={activeRoomId}
        onSelectRoom={(id) => { setView('chat'); setActiveRoomId(id); setSidebarOpen(false) }}
        onCreateRoom={() => setRoomModalOpen(true)}
        avatars={avatars}
        usage={usage}
      />

      <div className="flex flex-1 min-w-0 min-h-0 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200/60 px-4 py-2 bg-white/70 shrink-0">
          <button aria-label="Toggle navigation" title="Toggle navigation" aria-expanded={window.innerWidth < 768 ? sidebarOpen : !leftCollapsed} onClick={toggleNavigation} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"><PanelLeft size={19} strokeWidth={1.7} /></button>
          <div className="flex items-center gap-1">
            <button aria-label="Toggle terminal" aria-expanded={terminalOpen} onClick={() => setTerminalOpen(!terminalOpen)} className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs ${terminalOpen ? 'bg-slate-100 text-slate-900' : 'text-slate-500 hover:bg-slate-100'}`}><Terminal size={17} strokeWidth={1.7} /><span className="hidden sm:inline">Terminal</span></button>
            {view === 'chat' && !activeRoom && <button aria-label="Toggle conversation panel" title="Toggle conversation panel" aria-expanded={narrow ? mobileDetailsOpen : !rightCollapsed} onClick={() => narrow ? setMobileDetailsOpen(!mobileDetailsOpen) : setRightCollapsed(!rightCollapsed)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"><PanelRight size={19} strokeWidth={1.7} /></button>}
          </div>
        </header>
        <div className="flex flex-1 min-h-0 min-w-0">
      {view === 'dashboard' ? (
        <Dashboard userId={USER_ID} agentId={agentId} bots={bots} rooms={rooms}
          onChat={(id) => { if (id) selectBot(id); else { setView('chat'); setActiveRoomId(null) } }}
          onSetup={openSetup} onCreateBot={() => setBotModalOpen(true)} onCreateRoom={() => setRoomModalOpen(true)}
          onRoom={(id) => { setView('chat'); setActiveRoomId(id) }} onMenu={() => setSidebarOpen(true)} />
      ) : activeRoom ? (
        <RoomView
          room={activeRoom}
          bots={bots}
          onBack={() => setActiveRoomId(null)}
          onChanged={refreshRooms}
        />
      ) : (
        <div className="flex-1 flex min-w-0">
        <div className="flex-1 flex flex-col min-w-0">
          <header className="flex items-center justify-between px-5 md:px-8 py-5">
            <div className="text-sm text-slate-600 md:hidden">
              {activeBot?.name || 'Colons'}
            </div>
            <div className="hidden md:flex items-center gap-2 text-sm text-slate-600">
              <BotAvatar avatar={activeBot?.avatar_def} size={26} state={speaking ? 'speaking' : streaming ? 'thinking' : 'idle'} />
              <span>{activeBot?.name || 'Colons'}</span>
              {activeBot?.title && <span className="text-slate-500">· {activeBot.title}</span>}
            </div>
            <button onClick={() => openSetup('provider')} className="text-xs text-slate-600 bg-slate-100/70 border border-slate-200/60 rounded-full px-4 py-2">Model & settings ▾</button>
          </header>

          <main className="flex-1 overflow-y-auto">
            {error && messages.length === 0 && <div role="alert" className="mx-5 mt-3 text-sm text-red-600">{error}</div>}
            {messages.length === 0 ? (
              <EmptyState
                onPrompt={send}
                avatar={activeBot?.avatar_def || undefined}
                name={activeBot?.name}
                title={activeBot?.title}
                hasMentions={bots.length > 1}
              />
            ) : (
              <div className="max-w-3xl mx-auto px-4 md:px-6 py-8 space-y-7">
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} avatar={activeBot?.avatar_def || undefined}
                                 onSpeak={(text) => void speak(text, m.id)} speaking={speaking && speakingMessageId === m.id} onApprove={approve} />
                ))}
                {error && !messages.some((m) => m.error === error) && (
                  <div className="mx-3 md:mx-0 text-xs text-red-600 bg-red-50/30 border border-red-200/50 rounded-lg px-3 py-2">
                    {error}
                  </div>
                )}
                <div ref={endRef} />
              </div>
            )}
          </main>

          <Composer
            key={agentId}
            allowAttachments
            onSend={send}
            onCancel={cancel}
            streaming={streaming}
            status={status}
            mentionables={mentionables.length > 1 ? mentionables : undefined}
          />
        </div>
        {(narrow ? mobileDetailsOpen : !rightCollapsed) && <ConversationPanel onClose={() => narrow ? setMobileDetailsOpen(false) : setRightCollapsed(true)} bot={activeBot} messages={messages} session={sessions.find((s) => s.id === sessionId)} status={status} onSettings={() => openSetup('avatar')} />}
        </div>
      )}

        </div>
        <TerminalPanel key={agentId} open={terminalOpen} userId={USER_ID} agentId={agentId} onClose={() => setTerminalOpen(false)} />
      </div>
      <BotModal
        open={botModalOpen}
        onClose={() => setBotModalOpen(false)}
        avatars={avatars}
        onCreated={(bot) => { refreshBots(); selectBot(bot.id) }}
      />

      <RoomModal
        open={roomModalOpen}
        onClose={() => setRoomModalOpen(false)}
        bots={bots}
        onCreated={(roomId) => { refreshRooms(); setView('chat'); setActiveRoomId(roomId) }}
      />

      <SettingsPanel
        initialTab={setupTab}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        userId={USER_ID}
        agentId={agentId}
        currentAvatar={activeBot?.avatar_def || undefined}
        onAvatarChange={(a) => {
          if (activeBot) {
            api.updateBot(activeBot.id, { avatar: a.id }).then(refreshBots).catch(() => {})
          }
        }}
        onSpeakVoiceChange={setVoice}
      />
    </div>
  )
}

function EmptyState({
  onPrompt, avatar, name, title, hasMentions,
}: {
  onPrompt: (t: string) => void
  avatar?: AvatarInfo
  name?: string
  title?: string
  hasMentions: boolean
}) {
  const suggestions = [
    { icon: 'brainstorm' as IconName, title: 'Brainstorm', description: 'Explore ideas and new perspectives', text: 'Help me brainstorm ideas for my next project.' },
    { icon: 'write' as IconName, title: 'Write', description: 'Draft, edit, and improve content', text: 'Help me draft a clear announcement for my product.' },
    { icon: 'build' as IconName, title: 'Build', description: 'Get help with code, tech, and products', text: 'Help me plan and build a small useful app.' },
    { icon: 'plan' as IconName, title: 'Plan', description: 'Turn your goals into clear steps', text: 'Help me create a one-week study plan. Ask about my goals first.' },
  ]
  return (
    <div className="min-h-full flex flex-col items-center justify-center px-5 md:px-10 py-12">
      <div className="mb-7"><BotAvatar size={64} /></div>
      <h1 className="welcome-title text-center text-3xl lg:text-4xl mb-3">What can I help you think through today?</h1>
      <p className="text-slate-500 text-sm text-center max-w-lg leading-relaxed mb-8">A calmer space to explore ideas, solve problems, and make progress.</p>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 w-full max-w-3xl">
        {suggestions.map((s) => <button key={s.title} onClick={() => onPrompt(s.text)} className="text-left p-5 rounded-2xl border border-slate-200/80 bg-white/60 hover:bg-[#edf2f8] hover:border-[#cbd7e6] transition-colors"><span className="text-slate-800"><Icon name={s.icon} size={22} /></span><p className="text-sm font-semibold mt-4">{s.title}</p><p className="text-xs text-slate-500 mt-2 leading-relaxed">{s.description}</p></button>)}
      </div>
      {hasMentions && <p className="text-xs text-slate-400 mt-6">Mention a teammate with @ to work together.</p>}
    </div>
  )
}
