"""
Async client for talking to a Colons server (used by the CLI and scripts).
"""
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx


class ColonsClient:
    """Thin async client over the Colons REST + WebSocket API."""

    def __init__(self, base_url: str = "http://localhost:8000", api_key: Optional[str] = None,
                 timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout,
                                        headers=self._headers)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        await self.client.aclose()

    # ------------------------------------------------------------------ #
    # Meta
    # ------------------------------------------------------------------ #

    async def health(self) -> Dict:
        r = await self.client.get("/health")
        r.raise_for_status()
        return r.json()

    async def config(self) -> Dict:
        r = await self.client.get("/api/config")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Chat
    # ------------------------------------------------------------------ #

    async def chat(self, message: str, session_id: Optional[str] = None,
                   user_id: str = "default", agent_id: Optional[str] = None,
                   stream: bool = False) -> Dict:
        r = await self.client.post("/api/chat", json={
            "message": message, "session_id": session_id, "stream": stream,
            "user_id": user_id, "agent_id": agent_id,
        })
        r.raise_for_status()
        return r.json()

    async def chat_stream(self, message: str, session_id: Optional[str] = None,
                          user_id: str = "default", agent_id: Optional[str] = None,
                          ) -> AsyncGenerator[Dict, None]:
        """Stream typed events over the WebSocket."""
        import websockets  # optional dependency
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws_url}/ws/chat"
        if self.api_key:
            url += f"?api_key={self.api_key}"

        async with websockets.connect(url, ping_interval=20, max_size=20 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "type": "chat", "message": message,
                "session_id": session_id, "user_id": user_id, "agent_id": agent_id,
                "stream": True,
            }))
            async for raw in ws:
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Stop when the agent is done
                data = json.loads(raw) if isinstance(raw, str) else {}
                if data.get("type") == "done":
                    break

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    async def list_sessions(self, user_id: str = "default",
                            agent_id: Optional[str] = None) -> List[Dict]:
        params: Dict[str, Any] = {"user_id": user_id}
        if agent_id:
            params["agent_id"] = agent_id
        r = await self.client.get("/api/sessions", params=params)
        r.raise_for_status()
        return r.json()["sessions"]

    async def get_session(self, session_id: str, user_id: str = "default",
                          agent_id: Optional[str] = None) -> Dict:
        params: Dict[str, Any] = {"user_id": user_id}
        if agent_id:
            params["agent_id"] = agent_id
        r = await self.client.get(f"/api/sessions/{session_id}", params=params)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Tasks
    # ------------------------------------------------------------------ #

    async def create_task(self, description: str, priority: int = 1,
                          user_id: str = "default", agent_id: Optional[str] = None) -> Dict:
        r = await self.client.post("/api/tasks", json={
            "description": description, "priority": priority, "user_id": user_id,
            "agent_id": agent_id,
        })
        r.raise_for_status()
        return r.json()

    async def list_tasks(self, user_id: str = "default", status: Optional[str] = None,
                         agent_id: Optional[str] = None) -> List[Dict]:
        params: Dict[str, Any] = {"user_id": user_id}
        if agent_id:
            params["agent_id"] = agent_id
        if status:
            params["status"] = status
        r = await self.client.get("/api/tasks", params=params)
        r.raise_for_status()
        return r.json()["tasks"]

    async def get_task(self, task_id: str, user_id: str = "default",
                       agent_id: Optional[str] = None) -> Dict:
        params = {"user_id": user_id}
        if agent_id:
            params["agent_id"] = agent_id
        r = await self.client.get(f"/api/tasks/{task_id}", params=params)
        r.raise_for_status()
        return r.json()

    async def run_task(self, task_id: str, user_id: str = "default") -> Dict:
        r = await self.client.post(f"/api/tasks/{task_id}/run", params={"user_id": user_id})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Memory
    # ------------------------------------------------------------------ #

    async def search_memory(self, query: str, limit: int = 5,
                            user_id: str = "default") -> List[Dict]:
        r = await self.client.get("/api/memory/search", params={
            "q": query, "limit": limit, "user_id": user_id,
        })
        r.raise_for_status()
        return r.json()["results"]

    async def memory_history(self, limit: int = 50, user_id: str = "default") -> List[Dict]:
        r = await self.client.get("/api/memory/history", params={
            "limit": limit, "user_id": user_id,
        })
        r.raise_for_status()
        return r.json()["entries"]

    # ------------------------------------------------------------------ #
    # Tools / providers / voice
    # ------------------------------------------------------------------ #

    async def list_tools(self, user_id: str = "default") -> Dict:
        r = await self.client.get("/api/tools", params={"user_id": user_id})
        r.raise_for_status()
        return r.json()

    async def execute_tool(self, tool: str, arguments: Dict,
                           user_id: str = "default") -> Dict:
        r = await self.client.post("/api/tools/execute", json={
            "tool": tool, "arguments": arguments, "user_id": user_id,
        })
        r.raise_for_status()
        return r.json()

    async def list_providers(self) -> Dict:
        r = await self.client.get("/api/providers")
        r.raise_for_status()
        return r.json()

    async def switch_provider(self, name: str, api_key: Optional[str] = None,
                              base_url: Optional[str] = None,
                              model: Optional[str] = None) -> Dict:
        r = await self.client.post("/api/providers/switch", json={
            "name": name, "api_key": api_key, "base_url": base_url, "model": model,
        })
        r.raise_for_status()
        return r.json()

    async def usage(self, user_id: str = "default") -> Dict:
        r = await self.client.get("/api/usage", params={"user_id": user_id})
        r.raise_for_status()
        return r.json()

    async def list_avatars(self) -> List[Dict]:
        r = await self.client.get("/api/avatars")
        r.raise_for_status()
        return r.json()["avatars"]

    async def list_voices(self, locale: Optional[str] = None) -> Dict:
        params = {"locale": locale} if locale else {}
        r = await self.client.get("/api/voice/voices", params=params)
        r.raise_for_status()
        return r.json()

    async def tts(self, text: str, voice: Optional[str] = None) -> bytes:
        r = await self.client.post("/api/voice/tts", json={"text": text, "voice": voice})
        r.raise_for_status()
        return r.content

    async def transcribe(self, path: str, model: str = "",
                         language: Optional[str] = None) -> Dict:
        import os
        with open(path, "rb") as f:
            audio = f.read()
        files = {"file": (os.path.basename(path), audio)}
        data: Dict[str, str] = {}
        if model:
            data["model"] = model
        if language:
            data["language"] = language
        r = await self.client.post("/api/voice/transcribe", files=files, data=data)
        r.raise_for_status()
        return r.json()

    async def list_agents(self) -> List[Dict]:
        r = await self.client.get("/api/agents")
        r.raise_for_status()
        return r.json()["agents"]

    async def agent_status(self, agent_id: str, user_id: str = "default") -> Dict:
        r = await self.client.get(f"/api/agents/{agent_id}/status", params={"user_id": user_id})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Scheduler (cronjobs)
    # ------------------------------------------------------------------ #

    async def list_schedules(self) -> Dict:
        r = await self.client.get("/api/schedules")
        r.raise_for_status()
        return r.json()

    async def add_schedule(self, cron: str, prompt: str, name: str = "",
                           user_id: str = "default", notify_adapter: Optional[str] = None,
                           notify_chat_id: Optional[str] = None,
                           timezone: Optional[str] = None,
                           continuity: bool = False, notes: str = "",
                           monitor_command: Optional[str] = None) -> Dict:
        r = await self.client.post("/api/schedules", json={
            "cron": cron, "prompt": prompt, "name": name, "user_id": user_id,
            "notify_adapter": notify_adapter, "notify_chat_id": notify_chat_id,
            "timezone": timezone, "continuity": continuity, "notes": notes,
            "monitor_command": monitor_command or "",
        })
        r.raise_for_status()
        return r.json()

    async def update_schedule(self, schedule_id: str, **changes) -> Dict:
        r = await self.client.patch(f"/api/schedules/{schedule_id}", json=changes)
        r.raise_for_status()
        return r.json()

    async def remove_schedule(self, schedule_id: str) -> Dict:
        r = await self.client.delete(f"/api/schedules/{schedule_id}")
        r.raise_for_status()
        return r.json()

    async def run_schedule(self, schedule_id: str) -> Dict:
        r = await self.client.post(f"/api/schedules/{schedule_id}/run")
        r.raise_for_status()
        return r.json()

    async def validate_cron(self, expression: str) -> Dict:
        r = await self.client.get(f"/api/schedules/validate/{expression}")
        r.raise_for_status()
        return r.json()

    async def schedule_history(self, limit: int = 50) -> Dict:
        r = await self.client.get("/api/schedules/history/recent", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Doctor
    # ------------------------------------------------------------------ #

    async def doctor(self, check_provider: bool = True) -> Dict:
        r = await self.client.get("/api/doctor", params={"check_provider": check_provider})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Debug & logs
    # ------------------------------------------------------------------ #

    async def logs(self, level: str = "INFO", limit: int = 100,
                   logger_prefix: Optional[str] = None) -> Dict:
        params: Dict[str, Any] = {"level": level, "limit": limit}
        if logger_prefix:
            params["logger_prefix"] = logger_prefix
        r = await self.client.get("/api/debug/logs", params=params)
        r.raise_for_status()
        return r.json()

    async def clear_logs(self) -> Dict:
        r = await self.client.delete("/api/debug/logs")
        r.raise_for_status()
        return r.json()

    async def debug_stats(self) -> Dict:
        r = await self.client.get("/api/debug/stats")
        r.raise_for_status()
        return r.json()

    async def debug_config(self) -> Dict:
        r = await self.client.get("/api/debug/config")
        r.raise_for_status()
        return r.json()

    async def debug_env(self) -> Dict:
        r = await self.client.get("/api/debug/env")
        r.raise_for_status()
        return r.json()

    async def set_log_level(self, level: str) -> Dict:
        r = await self.client.post("/api/debug/log-level", params={"level": level})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #

    async def messaging_status(self) -> Dict:
        r = await self.client.get("/api/messaging/status")
        r.raise_for_status()
        return r.json()

    async def messaging_send(self, adapter: str, chat_id: str, text: str) -> Dict:
        r = await self.client.post("/api/messaging/send",
                                   json={"adapter": adapter, "chat_id": chat_id, "text": text})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Bots
    # ------------------------------------------------------------------ #

    async def list_bots(self, owner: Optional[str] = None) -> List[Dict]:
        params = {"owner": owner} if owner else {}
        r = await self.client.get("/api/bots", params=params)
        r.raise_for_status()
        return r.json()["bots"]

    async def create_bot(self, name: str, title: str = "", description: str = "",
                         persona: str = "", avatar: str = "", model: str = "",
                         owner: str = "default") -> Dict:
        r = await self.client.post("/api/bots", json={
            "name": name, "title": title, "description": description,
            "persona": persona, "avatar": avatar, "model": model, "owner": owner,
        })
        r.raise_for_status()
        return r.json()

    async def update_bot(self, bot_id: str, **changes) -> Dict:
        r = await self.client.patch(f"/api/bots/{bot_id}", json=changes)
        r.raise_for_status()
        return r.json()

    async def delete_bot(self, bot_id: str) -> Dict:
        r = await self.client.delete(f"/api/bots/{bot_id}")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # Rooms
    # ------------------------------------------------------------------ #

    async def list_rooms(self, owner: str = "default") -> List[Dict]:
        r = await self.client.get("/api/rooms", params={"owner": owner})
        r.raise_for_status()
        return r.json()["rooms"]

    async def create_room(self, name: str, members: List[str]) -> Dict:
        r = await self.client.post("/api/rooms", json={"name": name, "members": members})
        r.raise_for_status()
        return r.json()

    async def delete_room(self, room_id: str) -> Dict:
        r = await self.client.delete(f"/api/rooms/{room_id}")
        r.raise_for_status()
        return r.json()

    async def room_messages(self, room_id: str, limit: int = 100) -> List[Dict]:
        r = await self.client.get(f"/api/rooms/{room_id}/messages", params={"limit": limit})
        r.raise_for_status()
        return r.json()["messages"]

    async def send_room_message(self, room_id: str, message: str,
                                speaker: str = "user") -> List[Dict]:
        r = await self.client.post(f"/api/rooms/{room_id}/messages",
                                   json={"message": message, "speaker": speaker})
        r.raise_for_status()
        return r.json()["messages"]

    # ------------------------------------------------------------------ #
    # Peers
    # ------------------------------------------------------------------ #

    async def list_peers(self) -> Dict:
        r = await self.client.get("/api/peers")
        r.raise_for_status()
        return r.json()

    async def peer_dm(self, target: str, message: str) -> Dict:
        r = await self.client.post("/api/peer/message", json={
            "target": target, "message": message,
            "sender": {"id": "cli", "name": "CLI", "handle": "@cli"},
        })
        r.raise_for_status()
        return r.json()
