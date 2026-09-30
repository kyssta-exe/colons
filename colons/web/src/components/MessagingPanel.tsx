import { useEffect, useState } from 'react'
import { Check, ChevronDown, LoaderCircle, MessageCircle, Send, Hash } from 'lucide-react'
import { api } from '../lib/api'

const services = [
  { name: 'telegram', label: 'Telegram', description: 'Chat with your bot directly. No public server URL needed.', icon: Send, docs: 'https://core.telegram.org/bots/tutorial', steps: ['Open @BotFather in Telegram, send /newbot, and follow the prompts.', 'Copy the bot token into the form below, then save and connect.', 'Open your new bot’s chat and send /start or a message.'] },
  { name: 'discord', label: 'Discord', description: 'Direct messages and mentions in your server.', icon: MessageCircle, docs: 'https://docs.discord.com/developers/quick-start/getting-started', steps: ['Create an application in the Discord Developer Portal. Open Bot and copy its token.', 'Enable Message Content Intent on the Bot page. Invite the bot with View Channels, Send Messages, and Read Message History permissions.', 'Save and connect below. Send a direct message or mention the bot in a channel.'] },
  { name: 'slack', label: 'Slack', description: 'Two-way conversations, or webhook notifications only.', icon: Hash, docs: 'https://docs.slack.dev/apis/events-api/using-http-request-urls', steps: ['Create a Slack app, add chat:write and the history scopes for the conversations you want (im:history, channels:history, or groups:history), and install it in your workspace.', 'Copy the Bot User OAuth Token and the Signing Secret into this form. Save and connect before enabling Event Subscriptions.', 'Set the Events Request URL to your public HTTPS Colons address followed by /api/messaging/slack/events. Subscribe to message.im and any message.channels or message.groups events you need. Invite the bot to those channels.', 'For outgoing notifications only, use an Incoming Webhook URL instead of a bot token. A webhook alone cannot receive messages.'] },
] as const
const field = 'w-full mt-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm font-normal outline-none focus:border-colons-accent'
export function MessagingPanel() {
  const [data, setData] = useState<{ services: any[] } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let disposed = false
    const load = () => api.messagingConfig().then((value) => { if (!disposed) { setData(value); setError('') } }).catch((e) => { if (!disposed) setError(e.message) })
    load()
    const interval = setInterval(load, 15000)
    return () => { disposed = true; clearInterval(interval) }
  }, [])
  return <div className="space-y-4">
    {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {!data && !error && <p role="status" className="text-sm text-slate-500">Loading messaging…</p>}
    {data && services.map((service) => <MessagingService key={service.name} service={service} config={data.services.find((item) => item.name === service.name)} onUpdate={setData} />)}
    {data && <p className="text-xs text-slate-500 leading-relaxed">Settings are saved privately on this server and restored after restart. Blank credential fields keep the existing value. Connected bots support /new, /status, and /help.</p>}
  </div>
}
function MessagingService({ service, config, onUpdate }: { service: typeof services[number]; config: any; onUpdate: (data: { services: any[] }) => void }) {
  const [editing, setEditing] = useState(false)
  const [token, setToken] = useState('')
  const [secret, setSecret] = useState('')
  const [webhook, setWebhook] = useState('')
  const [channel, setChannel] = useState('')
  const [users, setUsers] = useState('')
  const [chats, setChats] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const Icon = service.icon
  const split = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean)
  const openForm = () => {
    if (!editing) { setChannel(config.default_channel || ''); setUsers((config.allowed_users || []).join(', ')); setChats((config.allowed_chats || []).join(', ')); setError(''); setNotice('') }
    setEditing(!editing)
  }
  const save = async (enabled: boolean) => {
    setBusy(true); setError(''); setNotice('')
    try {
      const values: Record<string, unknown> = { enabled }
      if (enabled) {
        Object.assign(values, { bot_token: token, allowed_users: split(users), allowed_chats: split(chats) })
        if (service.name === 'slack') Object.assign(values, { signing_secret: secret, webhook_url: webhook, default_channel: channel })
      }
      const result = await api.configureMessaging(service.name, values)
      onUpdate(result); setToken(''); setSecret(''); setWebhook('')
      const updated = result.services.find((item) => item.name === service.name)
      if (enabled && !updated?.status.connected) setError(updated?.status.last_error || 'Settings saved, but connection failed. Check your credentials and try again.')
      else { setNotice(enabled ? 'Saved and connected.' : 'Disconnected. Credentials are kept for reconnecting.'); setEditing(false) }
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  return <section aria-label={`${service.label} messaging`} className="rounded-2xl border border-slate-200 p-5">
    <div className="flex items-center gap-3"><div className="rounded-xl bg-slate-50 p-2.5 text-slate-600"><Icon size={19} strokeWidth={1.7} /></div><div className="flex-1"><h4 className="text-sm font-medium">{service.label}</h4><p className="text-xs text-slate-500 mt-1">{service.description}</p></div></div>
    <div className="flex items-center justify-between mt-4 gap-3"><span className={`rounded-full px-2.5 py-1 text-[11px] ${config?.status.connected ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{config?.status.connected ? 'Connected' : config?.enabled ? 'Connection failed' : 'Not connected'}</span><button disabled={busy} aria-expanded={editing} onClick={openForm} className="flex gap-1.5 items-center text-xs text-colons-accent hover:underline disabled:opacity-50">{editing ? 'Close setup' : config?.enabled ? 'Manage' : 'Set up'}<ChevronDown size={14} className={editing ? 'rotate-180' : ''} /></button></div>
    {error && <p role="alert" className="rounded-xl bg-red-50 text-red-700 text-xs p-3 mt-4 break-words">{error}</p>}
    {!error && config?.status.last_error && config.enabled && <p className="text-xs text-red-600 mt-3 break-words">{config.status.last_error}</p>}
    {notice && <p role="status" className="text-xs text-emerald-700 mt-3">{notice}</p>}
    {editing && <div className="mt-5 border-t border-slate-100 pt-5 space-y-5">
      <details open={!config?.enabled} className="rounded-xl bg-slate-50 p-4"><summary className="text-xs font-medium text-slate-700 cursor-pointer">Setup instructions</summary><ol className="mt-3 space-y-2 list-decimal pl-4 text-xs text-slate-500 leading-relaxed">{service.steps.map((step) => <li key={step}>{step}</li>)}</ol><a className="inline-block text-xs text-colons-accent mt-3 hover:underline" href={service.docs} target="_blank" rel="noreferrer">Official setup guide ↗</a>{service.name === 'slack' && <p className="mt-3 text-xs text-slate-500 break-all">Events URL: {location.origin}/api/messaging/slack/events<br />Use your publicly reachable HTTPS address.</p>}</details>
      <form onSubmit={(e) => { e.preventDefault(); void save(true) }} className="space-y-4">
        <label className="block text-xs font-medium">Bot token{service.name === 'slack' && ' (for conversations)'}<input aria-label={`${service.label} bot token`} type="password" autoComplete="off" className={field} value={token} disabled={busy} onChange={(e) => setToken(e.target.value)} placeholder={config?.credentials.bot_token ? 'Token saved — leave blank to keep it' : 'Paste your bot token'} /></label>
        {service.name === 'slack' && <><label className="block text-xs font-medium">Signing secret<input aria-label="Slack signing secret" type="password" autoComplete="off" className={field} value={secret} disabled={busy} onChange={(e) => setSecret(e.target.value)} placeholder={config?.credentials.signing_secret ? 'Secret saved — leave blank to keep it' : 'Paste your app signing secret'} /></label><label className="block text-xs font-medium">Incoming webhook URL (notifications only)<input aria-label="Slack webhook URL" type="password" autoComplete="off" className={field} value={webhook} disabled={busy} onChange={(e) => setWebhook(e.target.value)} placeholder={config?.credentials.webhook_url ? 'Webhook saved — leave blank to keep it' : 'https://hooks.slack.com/services/…'} /></label><label className="block text-xs font-medium">Default channel (optional)<input className={field} value={channel} disabled={busy} onChange={(e) => setChannel(e.target.value)} placeholder="Channel ID" /></label></>}
        <details className="rounded-xl border border-slate-200 p-4"><summary className="text-xs text-slate-600 cursor-pointer">Limit who can message this bot</summary><p className="text-xs text-slate-500 mt-3">Comma-separated platform IDs. Leave blank to allow everyone who can reach the bot.</p><label className="block mt-3 text-xs font-medium">Allowed user IDs<input className={field} value={users} disabled={busy} onChange={(e) => setUsers(e.target.value)} placeholder="123456, 789012" /></label><label className="block mt-3 text-xs font-medium">Allowed chat / channel IDs<input className={field} value={chats} disabled={busy} onChange={(e) => setChats(e.target.value)} placeholder="Channel or chat IDs" /></label></details>
        <div className="flex items-center justify-between gap-3"><button disabled={busy} className="inline-flex items-center gap-2 rounded-xl bg-slate-900 text-white px-4 py-2.5 text-xs font-medium disabled:opacity-50">{busy ? <LoaderCircle size={14} className="animate-spin" /> : <Check size={14} />}Save & connect</button>{config?.enabled && <button type="button" disabled={busy} onClick={() => void save(false)} className="text-xs text-red-600 disabled:opacity-50">Disconnect</button>}</div>
      </form>
    </div>}
  </section>
}
