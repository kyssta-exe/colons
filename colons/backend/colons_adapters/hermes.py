"""Hermes-compatible MCP bridge to a running Colons server (stdio only)."""

import os
from contextlib import asynccontextmanager
from typing import Annotated, Optional

import httpx
from colons_cli.client import ColonsClient
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

Text = Annotated[str, Field(min_length=1, max_length=10000)]
Identifier = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")]


async def _call(operation):
    """Report actionable failures without leaking URLs, keys, or server tracebacks."""
    try:
        return await operation
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hints = {401: "Check COLONS_ADAPTER_API_KEY.",
                 404: "Check the ID and selected agent; tasks reset on server restart.",
                 429: "Colons rate limit reached; wait before polling again."}
        raise RuntimeError(f"Colons returned HTTP {status}. {hints.get(status, 'Check the Colons server logs.')}"
                           ) from None
    except httpx.TimeoutException:
        raise RuntimeError("Colons request timed out. Check task status before resubmitting work; "
                           "the server may still be processing it.") from None
    except httpx.RequestError:
        raise RuntimeError("Cannot reach Colons. Check COLONS_ADAPTER_URL and start the server.") from None


def create_server(client: Optional[ColonsClient] = None, user_id: Optional[str] = None) -> FastMCP:
    """Construct a server; injected clients allow API/protocol integration testing."""
    client = client or ColonsClient(
        base_url=os.environ.get("COLONS_ADAPTER_URL", "http://127.0.0.1:8000"),
        api_key=os.environ.get("COLONS_ADAPTER_API_KEY") or None,
        timeout=float(os.environ.get("COLONS_ADAPTER_TIMEOUT", "120")),
    )
    user_id = user_id or os.environ.get("COLONS_ADAPTER_USER_ID", "default")

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await client.close()

    server = FastMCP("Colons", lifespan=lifespan, instructions=(
        "Collaborate with Colons agents. Discover agent_id values with list_colons, then "
        "assign_work and poll get_work using the SAME agent_id. Assignment starts background "
        "execution; do not also run or repeatedly submit the task. Pending/running is not "
        "completion. Report the returned result and any failure honestly. Colons enforces its "
        "own permission policy. This bridge cannot approve tools or change permission modes."
    ))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    # Delegated agents can modify/delete files under the configured tool policy.
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

    @server.tool(annotations=read)
    async def list_colons() -> dict:
        """List named Colons bots and active agents with agent_id values for delegation."""
        return {"bots": await _call(client.list_bots()),
                "agents": await _call(client.list_agents())}

    @server.tool(annotations=write)
    async def create_colon(name: Text, title: str = "", description: str = "",
                           persona: str = "") -> dict:
        """Create a named Colons bot. Use its returned agent_id for future work."""
        bot = await _call(client.create_bot(name, title=title, description=description,
                                           persona=persona, owner=user_id))
        return {**bot, "agent_id": f"bot-{bot['id']}"}

    @server.tool(annotations=write)
    async def assign_work(description: Text, agent_id: Optional[Identifier] = None,
                          priority: int = 1) -> dict:
        """Queue work for a Colons agent and return immediately with task ID/status.

        Execution starts automatically in Colons. Poll get_work with the same agent_id.
        Omit agent_id to use the configured user's default agent. Never retry an ambiguous
        timeout blindly: list_work first to check whether the assignment was accepted.
        """
        if not description.strip():
            raise ValueError("Description cannot be blank")
        task = await _call(client.create_task(description, priority, user_id, agent_id))
        return {"task": task, "agent_id": task.get("agent_id", agent_id), "user_id": user_id}

    @server.tool(annotations=read)
    async def get_work(task_id: Identifier, agent_id: Optional[Identifier] = None) -> dict:
        """Get task status, plan, result, and error. Use the assignment's agent_id."""
        return await _call(client.get_task(task_id, user_id, agent_id))

    @server.tool(annotations=read)
    async def list_work(agent_id: Optional[Identifier] = None, status: Optional[str] = None) -> dict:
        """List this agent's assigned work; optionally filter by task status."""
        return {"tasks": await _call(client.list_tasks(user_id, status, agent_id))}

    @server.tool(annotations=write)
    async def chat_with_colon(message: Text, agent_id: Optional[Identifier] = None,
                              session_id: Optional[Identifier] = None) -> dict:
        """Talk to a Colons agent; reuse returned session_id for follow-ups.

        For long jobs use assign_work instead. Required tool approvals are not granted
        by this adapter. The agent's reply can contain limitations or denied operations.
        """
        return await _call(client.chat(message, session_id, user_id, agent_id))

    @server.tool(annotations=read)
    async def list_rooms() -> dict:
        """Find collaboration rooms owned by the configured Colons user."""
        return {"rooms": await _call(client.list_rooms(owner=user_id))}

    @server.tool(annotations=read)
    async def read_room(room_id: Identifier, limit: Annotated[int, Field(ge=1, le=100)] = 30) -> dict:
        """Read recent collaboration messages from a Colons room."""
        return {"messages": await _call(client.room_messages(room_id, limit))}

    @server.tool(annotations=write)
    async def send_to_room(room_id: Identifier, message: Text) -> dict:
        """Post as Hermes in a room and collect the room members' replies.

        Member agents may use tools according to their Colons permission settings.
        """
        return {"messages": await _call(client.send_room_message(room_id, message, speaker="Hermes"))}

    return server


def main():
    """Entry point launched by Hermes; stdout is reserved for MCP messages."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
