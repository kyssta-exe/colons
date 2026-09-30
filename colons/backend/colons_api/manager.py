"""
Agent registry/factory: builds AgentHarness instances from config,
supports multiple agents and provider switching at runtime.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from colons_core.agents import AgentHarness, SessionStore
from colons_core.agents.harness import DEFAULT_SYSTEM_PROMPT
from colons_core.audio import TTSConfig, TTSService
from colons_core.avatars import AvatarManager
from colons_core.bots import Bot, BotMessenger, BotStore, is_silence
from colons_core.config import ColonsConfig
from colons_core.memory import EmbeddingService, create_cache, create_store
from colons_core.messaging import MessagingBridge
from colons_core.messaging.settings import MessagingSettings
from colons_core.providers import ProviderFactory
from colons_core.providers.base import ProviderAdapter
from colons_core.rooms import RoomService, RoomStore
from colons_core.scheduler import SchedulerService, ScheduleStore
from colons_core.tools.settings import PermissionSettings

logger = logging.getLogger(__name__)

# Canonical session where a bot receives teammate DMs (unique per bot)
def bot_chat_session(bot_id: str) -> str:
    return f"bot-chat-{bot_id}"


def build_schedule_prompt(schedule) -> str:
    """Compose a schedule's prompt with notepad, continuity and monitor context."""
    sections: List[str] = []
    if getattr(schedule, "notes", ""):
        sections.append(f"Notepad (persistent across runs):\n{schedule.notes}")
    if getattr(schedule, "continuity", False) and getattr(schedule, "last_result", ""):
        sections.append(
            f"Previous run result (for continuity and dedupe):\n{schedule.last_result}"
        )
    monitor_output = (getattr(schedule, "metadata", None) or {}).get("monitor_output")
    if monitor_output:
        sections.append(f"Monitor command output:\n{monitor_output}")
    if not sections:
        return schedule.prompt
    return "\n\n".join(sections + [schedule.prompt])


class AgentManager:
    """Holds all live agents and shared services."""

    def __init__(self, config: ColonsConfig):
        self.config = config
        self.permission_settings = PermissionSettings(config)

        # Shared services
        self.store = create_store(config.memory.store_url, dim=config.memory.embedding_dim)
        self.cache = create_cache(config.memory.cache_url or None)
        self.session_store = SessionStore(f"{config.data_dir}/sessions.db")
        self.avatars = AvatarManager()
        self.tts: Optional[TTSService] = None
        if config.voice.enabled:
            self.tts = TTSService(
                preferred=config.voice.tts_engine,
                config=TTSConfig(
                    voice=config.voice.voice,
                    rate=config.voice.rate,
                    volume=config.voice.volume,
                    pitch=config.voice.pitch,
                ),
            )

        # Default provider
        self.provider: ProviderAdapter = self._build_provider()

        self.agents: Dict[str, AgentHarness] = {}

        # Bots (named agents) + bot-to-bot messaging
        self.bots = BotStore(f"{config.data_dir}/bots.db")
        self.messenger = BotMessenger(
            store=self.bots,
            local_delivery=self._deliver_bot_dm,
            peers=config.peers,
            default_timeout=config.peers_timeout,
        )
        self._ensure_default_bot()

        # Group rooms
        self.rooms = RoomStore(f"{config.data_dir}/rooms.db")
        self.room_service = RoomService(self.rooms, self)

        # Scheduler (cronjobs)
        self.scheduler: Optional[SchedulerService] = None
        if config.scheduler.enabled:
            schedule_store = ScheduleStore(f"{config.data_dir}/schedules.db")
            self.scheduler = SchedulerService(
                store=schedule_store,
                executor=self.run_scheduled_prompt,
                notifier=self.notify_schedule,
                default_timezone=config.scheduler.timezone,
                tick_interval=config.scheduler.tick_interval,
                max_concurrent=config.scheduler.max_concurrent,
            )

        # Messaging integrations
        self.messaging_settings = MessagingSettings(config)
        self.messaging: Optional[MessagingBridge] = None
        if any([
            config.messaging.telegram.get("enabled"),
            config.messaging.discord.get("enabled"),
            config.messaging.slack.get("enabled"),
            any(wh.get("enabled") for wh in (config.messaging.webhooks or {}).values()),
        ]):
            self.messaging = MessagingBridge(self, config)

    # ------------------------------------------------------------------ #
    # Provider
    # ------------------------------------------------------------------ #

    def _build_provider(self) -> ProviderAdapter:
        p = self.config.provider
        return ProviderFactory.create(
            provider=p.name,
            api_key=p.api_key or None,
            base_url=p.base_url or None,
        )

    def switch_provider(self, name: str, api_key: Optional[str] = None,
                        base_url: Optional[str] = None, model: Optional[str] = None):
        """Swap the provider on the default config and all running agents."""
        provider = ProviderFactory.create(provider=name, api_key=api_key or None,
                                          base_url=base_url or None)
        old_provider = self.provider
        self.provider = provider
        self.config.provider.name = name
        if api_key:
            self.config.provider.api_key = api_key
        if base_url:
            self.config.provider.base_url = base_url
        if model:
            self.config.provider.model = model
        for agent in self.agents.values():
            agent.provider = provider
            agent.embeddings.set_provider(provider)
            if model:
                agent.model = model
        # Release the old provider's HTTP connection pool (best effort)
        if old_provider is not provider and hasattr(old_provider, "close"):
            try:
                import asyncio
                asyncio.get_running_loop().create_task(old_provider.close())
            except RuntimeError:
                pass
        return provider

    # ------------------------------------------------------------------ #
    # Agents
    # ------------------------------------------------------------------ #

    def get_or_create(self, user_id: str = "default", agent_id: Optional[str] = None) -> AgentHarness:
        # Bot agents resolve through the roster so they get identity + messaging tools
        if agent_id and agent_id.startswith("bot-"):
            bot_agent = self.get_bot_agent(agent_id[4:], require=False)
            if bot_agent is not None:
                return bot_agent
        agent_id = agent_id or f"{user_id}-default"
        if agent_id in self.agents:
            return self.agents[agent_id]

        cfg = self.config
        agent = AgentHarness(
            provider=self.provider,
            store=self.store,
            cache=self.cache,
            embeddings=EmbeddingService(
                provider=self.provider,
                model=cfg.memory.embedding_model or None,
                cache=self.cache,
            ),
            tts=self.tts,
            avatars=self.avatars,
            agent_id=agent_id,
            user_id=user_id,
            name=cfg.agent.name,
            system_prompt=cfg.agent.system_prompt or None,
            workspace=cfg.tools.workspace,
            model=cfg.provider.model or None,
            temperature=cfg.provider.temperature,
            max_tokens=cfg.provider.max_tokens,
            max_iterations=cfg.agent.max_iterations,
            context_messages=cfg.agent.context_messages,
            proactive=cfg.agent.proactive,
            proactive_interval=cfg.agent.proactive_interval,
            auto_approve_tools=cfg.tools.auto_approve,
            shell_allowlist=cfg.tools.shell_allowlist or None,
            disabled_tools=cfg.tools.disabled or None,
            voice_enabled=cfg.voice.enabled or cfg.voice.autospeak,
            voice_config=TTSConfig(
                voice=cfg.voice.voice,
                rate=cfg.voice.rate,
                volume=cfg.voice.volume,
                pitch=cfg.voice.pitch,
            ),
            session_store=self.session_store,
            tool_timeout=cfg.tools.global_timeout,
            memory_limit=cfg.memory.prune_after,
        )
        if not cfg.agent.avatar:
            generated = self.avatars.generate_for(agent_id)
            self.avatars.select(agent_id, generated.id)
            agent.avatar = generated
        else:
            agent.avatar = self.avatars.select(agent_id, cfg.agent.avatar)

        self.permission_settings.apply(agent)
        self.agents[agent_id] = agent
        return agent

    def get(self, agent_id: str) -> Optional[AgentHarness]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[Dict]:
        return [
            {"agent_id": a.agent_id, "name": a.name, "state": a.state.value,
             "model": a.model, "provider": getattr(a.provider, "name", ""),
             "avatar": a.avatar.to_dict()}
            for a in self.agents.values()
        ]

    async def start_all(self):
        for agent in self.agents.values():
            await agent.start()
        if self.scheduler:
            await self.scheduler.start()
        if self.messaging:
            await self.messaging.start()

    async def stop_all(self):
        if self.messaging:
            await self.messaging.stop()
        if self.scheduler:
            await self.scheduler.stop()
        for agent in self.agents.values():
            await agent.stop()

    async def delete(self, agent_id: str) -> bool:
        agent = self.agents.pop(agent_id, None)
        if not agent:
            return False
        await agent.stop()
        return True

    # ------------------------------------------------------------------ #
    # Bots (named agents)
    # ------------------------------------------------------------------ #

    def _ensure_default_bot(self):
        """Make sure a primary bot exists so the roster is never empty."""
        if self.bots.count() > 0:
            return
        default = Bot(
            id="default",
            name=self.config.agent.name or "Colons",
            title="Primary agent",
            description="Your always-on assistant.",
            avatar=self.config.agent.avatar or "colons",
            owner="default",
        )
        self.bots.save(default)
        logger.info("Created default bot 'default'")

    def list_bots(self, owner: Optional[str] = None) -> List[Dict]:
        bots = self.bots.list(owner=owner)
        out = []
        for bot in bots:
            data = bot.to_dict()
            agent = self.agents.get(f"bot-{bot.id}")
            data["agent_id"] = f"bot-{bot.id}"
            data["state"] = agent.state.value if agent else "idle"
            data["avatar_def"] = self.avatars.get(bot.avatar or "") .to_dict() \
                if self.avatars.get(bot.avatar or "") else None
            out.append(data)
        return out

    def create_bot(self, name: str, title: str = "", description: str = "",
                   persona: str = "", avatar: str = "", model: str = "",
                   owner: str = "default") -> Bot:
        bot = Bot(name=name, title=title, description=description, persona=persona,
                  avatar=avatar, model=model, owner=owner)
        existing = self.bots.get(bot.id)
        if existing:
            raise ValueError(f"A bot with handle @{bot.id} already exists")
        if not bot.avatar:
            bot.avatar = self.avatars.generate_for(bot.id).id
        return self.bots.save(bot)

    def update_bot(self, bot_id: str, **changes) -> Optional[Bot]:
        bot = self.bots.get(bot_id)
        if not bot:
            return None
        for key in ("name", "title", "description", "persona", "avatar", "model", "enabled"):
            if key in changes and changes[key] is not None:
                setattr(bot, key, changes[key])
        self.bots.save(bot)
        # Refresh the live agent's identity and roster prompt
        changed_identity = any(k in changes for k in ("name", "title", "description",
                                                     "persona", "model"))
        if changed_identity:
            self._refresh_bot_agent(bot)
        return bot

    async def delete_bot(self, bot_id: str) -> bool:
        if bot_id == "default" and self.bots.count() == 1:
            raise ValueError("Cannot delete the only bot")
        agent = self.agents.pop(f"bot-{bot_id}", None)
        if agent:
            await agent.stop()
        bot = self.bots.get(bot_id)
        if bot:
            for room in self.rooms.list(owner=bot.owner):
                if bot_id in room.members:
                    room.members = [m for m in room.members if m != bot_id]
                    self.rooms.save(room)
        return self.bots.delete(bot_id)

    def get_bot_agent(self, bot_id: str, require: bool = True) -> Optional[AgentHarness]:
        bot = self.bots.get(bot_id)
        if not bot:
            if require:
                raise KeyError(f"Unknown bot: {bot_id}")
            return None
        agent_key = f"bot-{bot.id}"
        if agent_key in self.agents:
            return self.agents[agent_key]

        cfg = self.config

        async def router(target: str, message: str) -> Dict[str, Any]:
            if not target and not message:
                return {"roster": self.messenger.roster_block(bot.id, owner=bot.owner)}
            return await self.messenger.send(bot, target, message, owner=bot.owner)

        base_prompt = cfg.agent.system_prompt or DEFAULT_SYSTEM_PROMPT.format(
            name=bot.name, mode="proactive" if cfg.agent.proactive else "on-demand",
        )
        if bot.persona:
            base_prompt = f"{base_prompt or ''}\n\n{bot.persona}".strip()
        if bot.title or bot.description:
            identity = f"You are {bot.name}"
            if bot.title:
                identity += f", {bot.title}"
            if bot.description:
                identity += f". {bot.description}"
            base_prompt = f"{identity}.\n\n{base_prompt or ''}".strip()

        agent = AgentHarness(
            provider=self.provider,
            store=self.store,
            cache=self.cache,
            embeddings=EmbeddingService(
                provider=self.provider,
                model=cfg.memory.embedding_model or None,
                cache=self.cache,
            ),
            tts=self.tts,
            avatars=self.avatars,
            agent_id=agent_key,
            user_id=bot.owner,
            name=bot.name,
            system_prompt=base_prompt,
            workspace=cfg.tools.workspace,
            model=bot.model or cfg.provider.model or None,
            temperature=cfg.provider.temperature,
            max_tokens=cfg.provider.max_tokens,
            max_iterations=cfg.agent.max_iterations,
            context_messages=cfg.agent.context_messages,
            proactive=cfg.agent.proactive,
            proactive_interval=cfg.agent.proactive_interval,
            auto_approve_tools=cfg.tools.auto_approve,
            shell_allowlist=cfg.tools.shell_allowlist or None,
            disabled_tools=cfg.tools.disabled or None,
            session_store=self.session_store,
            tool_timeout=cfg.tools.global_timeout,
            memory_limit=cfg.memory.prune_after,
            peer_router=router if cfg.bot_messaging else None,
            peer_roster=self.messenger.roster_block(bot.id, owner=bot.owner)
            if cfg.bot_messaging else "",
        )
        avatar = self.avatars.get(bot.avatar) if bot.avatar else None
        if avatar:
            self.avatars.select(agent_key, avatar.id)
            agent.avatar = avatar
        self.permission_settings.apply(agent)
        self.agents[agent_key] = agent
        return agent

    def _refresh_bot_agent(self, bot: Bot):
        """Drop a live bot agent so it is rebuilt with the new identity."""
        self.agents.pop(f"bot-{bot.id}", None)

    # ------------------------------------------------------------------ #
    # Bot-to-bot messaging
    # ------------------------------------------------------------------ #

    async def _deliver_bot_dm(self, sender_id: str, target_id: str, attributed: str,
                              timeout: float) -> Dict[str, Any]:
        """Run one turn in the target bot's canonical DM session."""
        target_agent = self.get_bot_agent(target_id)
        await target_agent.start()
        session_key = bot_chat_session(target_id)
        session = target_agent._get_or_create_session(session_key)
        if session.title in ("New chat", "", None):
            session.title = "Bot Chat"
            self.session_store.touch(session_key, title="Bot Chat")

        async def run() -> str:
            final = ""
            async for event in target_agent.chat(attributed, session_id=session_key,
                                                 stream=False):
                etype = event.get("type")
                if etype == "done":
                    final = event.get("content") or final
                elif etype == "error":
                    raise RuntimeError(event.get("message", "agent error"))
            return final

        try:
            reply = await asyncio.wait_for(run(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError("target busy") from e
        return {
            "status": "ok",
            "reply": "" if is_silence(reply) else reply,
            "session_id": session_key,
        }

    def mention_hint(self, message: str, owner: str = "default") -> str:
        """
        If a user message @mentions a bot, return a system hint telling the
        active agent to involve it via message_agent.
        """
        import re
        mentions = set(re.findall(r"@([a-zA-Z0-9][a-zA-Z0-9_-]*)", message or ""))
        if not mentions:
            return ""
        hints = []
        for mention in mentions:
            try:
                bot = self.bots.resolve(mention, owner=owner)
                if bot.owner == owner:
                    hints.append(f"@{bot.id} is {bot.name} ({bot.title or 'teammate'}). "
                                 f"Use message_agent(target=\"@{bot.id}\", ...) to involve them.")
            except KeyError:
                continue
        if not hints:
            return ""
        return ("The user addressed teammates in their message. "
                "If useful, delegate with the message_agent tool:\n" + "\n".join(hints))

    # ------------------------------------------------------------------ #
    # Peer messaging (cross-instance)
    # ------------------------------------------------------------------ #

    async def receive_peer_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an inbound peer DM (POST /api/peer/message)."""
        target = (payload.get("target") or "").strip()
        message = payload.get("message") or ""
        sender = payload.get("sender") or {}
        sender_name = sender.get("name") or sender.get("id") or "peer"
        sender_handle = sender.get("handle") or f"@{sender.get('id', 'peer')}"

        if target:
            try:
                bot = self.bots.resolve(target)
            except KeyError as e:
                return {"status": "error", "reason": "unknown_target", "message": str(e)}
        else:
            bot = self.bots.get("default") or (self.bots.list()[0] if self.bots.list() else None)
            if bot is None:
                return {"status": "error", "reason": "no_bots", "message": "No bots configured"}

        attributed = f"Message from 🤖 {sender_name} ({sender_handle}) [peer]: {message}"
        try:
            result = await self._deliver_bot_dm(sender.get("id", "peer"), bot.id,
                                                attributed, self.config.peers_timeout)
        except TimeoutError:
            return {"status": "queued", "target": bot.handle}
        except Exception as e:
            return {"status": "error", "reason": "delivery_failed", "message": str(e)}
        return {"status": result["status"], "reply": result["reply"],
                "target": bot.handle, "bot": bot.id}

    # ------------------------------------------------------------------ #
    # Scheduled jobs
    # ------------------------------------------------------------------ #

    async def run_scheduled_prompt(self, schedule) -> str:
        """Executor callback: run a schedule's prompt through the agent."""
        agent = self.get_or_create(schedule.user_id, schedule.agent_id)
        await agent.start()

        prompt = build_schedule_prompt(schedule)
        session_id = f"sched-{schedule.id}"
        final = ""
        async for event in agent.chat(prompt, session_id=session_id, stream=False):
            etype = event.get("type")
            if etype == "done":
                final = event.get("content") or final
            elif etype == "error":
                raise RuntimeError(event.get("message", "agent error"))
        return final

    async def notify_schedule(self, schedule, result: str):
        """Notifier callback: deliver scheduled results to a messaging channel."""
        if not self.messaging or not schedule.notify_adapter or not schedule.notify_chat_id:
            return
        header = f"**{schedule.name or schedule.cron}**\n\n"
        await self.messaging.notify(
            schedule.notify_adapter,
            schedule.notify_chat_id,
            header + result,
        )
