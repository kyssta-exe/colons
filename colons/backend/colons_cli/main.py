"""
Colons CLI - talk to the same agent from your terminal.

Commands:
  colons                        Interactive chat (default)
  colons run "message"          One-shot message
  colons serve                  Start the API server
  colons task add|list|run      Task management
  colons memory search|list     Memory tools
  colons tools list|run         Tool management
  colons provider list|switch   Provider management
  colons voice voices|speak     TTS
  colons status|config          Agent & config info
  colons init                   Write a starter config
"""
import argparse
import asyncio
import json
import os
import sys
from typing import List, Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.text import Text
    RICH = True
except ImportError:
    RICH = False

from .client import ColonsClient

DEFAULT_URL = os.environ.get("COLONS_URL", "http://localhost:8000")
DEFAULT_KEY = os.environ.get("COLONS_API_KEY")

console = Console() if RICH else None


# --------------------------------------------------------------------------- #
# Output helpers (graceful fallback without rich)
# --------------------------------------------------------------------------- #

def out(text: str = ""):
    if console:
        console.print(text)
    else:
        print(text)


def err(text: str):
    if console:
        console.print(f"[red]{text}[/red]", highlight=False)
    else:
        print(f"ERROR: {text}", file=sys.stderr)


def header(text: str):
    if console:
        console.rule(f"[bold]{text}[/bold]")
    else:
        print(f"\n=== {text} ===")


def render_markdown(text: str):
    if console:
        console.print(Markdown(text))
    else:
        print(text)


def table(title: str, columns: List[str], rows: List[List[str]]):
    if console:
        t = Table(title=title)
        for c in columns:
            t.add_column(c)
        for row in rows:
            t.add_row(*[str(x) for x in row])
        console.print(t)
    else:
        print(f"\n{title}")
        print(" | ".join(columns))
        print("-" * 60)
        for row in rows:
            print(" | ".join(str(x) for x in row))


# --------------------------------------------------------------------------- #
# Interactive chat
# --------------------------------------------------------------------------- #

EVENT_STYLES = {
    "status": "dim",
    "reasoning": "dim italic",
    "tool_call": "cyan",
    "tool_result": "green",
    "error": "red",
}

async def interactive(client: ColonsClient, session_id: Optional[str], user_id: str,
                      speak: bool = False, show_reasoning: bool = True):
    header("Colons")
    try:
        health = await client.health()
        out(f"[dim]connected to {DEFAULT_URL} | provider={health.get('provider')} "
            f"| v{health.get('version')}[/dim]" if RICH else
            f"Connected to {DEFAULT_URL} (provider={health.get('provider')})")
    except Exception as e:
        err(f"Cannot reach Colons at {DEFAULT_URL}: {e}")
        err("Start the server with: colons serve")
        return

    out("Type your message, /help for commands, /quit to exit.\n")

    while True:
        try:
            if RICH:
                message = Prompt.ask("[bold green]you[/bold green]")
            else:
                message = input("you> ")
        except (KeyboardInterrupt, EOFError):
            out("\nbye")
            return

        message = message.strip()
        if not message:
            continue

        if message.startswith("/"):
            cmd = message[1:].strip()
            if cmd in ("quit", "exit", "q"):
                return
            if cmd == "help":
                out("/help /quit /sessions /clear /status")
                continue
            if cmd == "clear":
                os.system("cls" if os.name == "nt" else "clear")
                continue
            if cmd == "sessions":
                sessions = await client.list_sessions(user_id)
                for s in sessions[:20]:
                    out(f"  {s['id']}  {s['title'][:50]}  ({s['message_count']} msgs)")
                continue
            if cmd == "status":
                print(json.dumps(await client.usage(user_id), indent=2))
                continue
            err(f"unknown command: /{cmd}")
            continue

        # Stream the agent's work
        final_text = ""
        live = None
        try:
            if RICH and sys.stdout.isatty():
                live = Live(Text(""), refresh_per_second=12, console=console)
                live.start()

            async for ev in client.chat_stream(message, session_id=session_id, user_id=user_id):
                etype = ev.get("type")
                if etype == "delta":
                    final_text += ev.get("content", "")
                    if live:
                        live.update(Text(final_text))
                elif etype == "reasoning" and show_reasoning and live:
                    live.update(Text(f"[thinking] {ev.get('content','')}", style="dim italic"))
                elif etype == "tool_call":
                    if live:
                        live.stop()
                        live = None
                    out(f"[cyan]→ {ev.get('tool')}({json.dumps(ev.get('arguments', {}))[:120]})[/cyan]"
                        if RICH else f"-> {ev.get('tool')}(...)")
                elif etype == "tool_result":
                    ok = "✓" if ev.get("success") else "✗"
                    if RICH:
                        out(f"[{'green' if ev.get('success') else 'red'}]{ok} "
                            f"{ev.get('tool')} ({ev.get('duration_ms', 0):.0f}ms)[/]")
                    else:
                        out(f"{ok} {ev.get('tool')}")
                elif etype == "approval_required":
                    err(f"approval required for {ev.get('tool')}: {ev.get('reason')}")
                elif etype == "error":
                    err(ev.get("message", "unknown error"))
                elif etype == "done":
                    if ev.get("session_id"):
                        session_id = ev["session_id"]
                    break
        finally:
            if live:
                live.stop()

        out()
        render_markdown(final_text)
        out()

        if speak and final_text:
            try:
                audio = await client.tts(final_text)
                path = "/tmp/colons_speech.mp3"
                with open(path, "wb") as f:
                    f.write(audio)
                out(f"[dim]audio: {path}[/dim]")
                _play_audio(path)
            except Exception as e:
                err(f"TTS failed: {e}")


def _play_audio(path: str):
    import shutil
    import subprocess
    for player, args in (
        ("mpv", ["mpv", "--no-video", path]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", path]),
        ("afplay", ["afplay", path]),
        ("aplay", ["aplay", path]),
    ):
        if shutil.which(player):
            try:
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue


# --------------------------------------------------------------------------- #
# Serve
# --------------------------------------------------------------------------- #

def run_server(host: str, port: int, reload: bool):
    try:
        import uvicorn
    except ImportError:
        err("uvicorn is required to serve: pip install uvicorn")
        sys.exit(1)
    os.environ.setdefault("COLONS_HOST", host)
    os.environ.setdefault("COLONS_PORT", str(port))
    uvicorn.run("colons_api.main:app", host=host, port=port, reload=reload)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

async def cmd_run(args):
    async with ColonsClient(args.url, args.api_key) as client:
        res = await client.chat(args.message, user_id=args.user)
        render_markdown(res.get("response", ""))
        if args.usage:
            out(json.dumps(res.get("usage", {}), indent=2))


async def cmd_task(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "add":
            task = await client.create_task(args.description, priority=args.priority, user_id=args.user)
            out(f"created task {task['id']}")
        elif args.action == "list":
            tasks = await client.list_tasks(args.user, status=args.status)
            table("Tasks", ["id", "status", "description"],
                  [[t["id"], t["status"], t["description"][:60]] for t in tasks])
        elif args.action == "run":
            res = await client.run_task(args.task_id, user_id=args.user)
            render_markdown(res.get("result", ""))


async def cmd_memory(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "search":
            results = await client.search_memory(args.query, limit=args.limit, user_id=args.user)
            for r in results:
                out(f"[bold]{r['content'][:200]}[/bold]\n  kind={r['kind']} sim={r['similarity']}\n"
                    if RICH else f"{r['content'][:200]} (sim={r['similarity']})")
        elif args.action == "list":
            entries = await client.memory_history(limit=args.limit, user_id=args.user)
            table("Memory", ["id", "kind", "content"],
                  [[e.get("id"), e.get("kind"), str(e.get("content"))[:70]] for e in entries])


async def cmd_tools(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "list":
            data = await client.list_tools(args.user)
            table("Tools", ["name", "category", "permission", "danger"],
                  [[t["name"], t["category"], t["permission"], t["danger"]]
                   for t in data["tools"]])
        elif args.action == "run":
            arguments = json.loads(args.arguments) if args.arguments else {}
            result = await client.execute_tool(args.tool, arguments, user_id=args.user)
            out(json.dumps(result, indent=2))


async def cmd_provider(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "list":
            data = await client.list_providers()
            rows = [[p["name"], p["display"], p["kind"], "yes" if p["configured"] else "no"]
                    for p in data["providers"]]
            table("Providers", ["name", "display", "kind", "configured"], rows)
            out(f"active: {data['active']}")
        elif args.action == "switch":
            res = await client.switch_provider(args.name, api_key=args.api_key_opt,
                                               base_url=args.base_url, model=args.model)
            out(f"switched: {res}")


async def cmd_voice(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "voices":
            data = await client.list_voices(args.locale)
            table("Voices", ["id", "locale", "gender"],
                  [[v["id"], v["locale"], v.get("gender", "")] for v in data["voices"][:40]])
        elif args.action == "speak":
            audio = await client.tts(args.text, voice=args.voice)
            path = args.output or "/tmp/colons_speech.mp3"
            with open(path, "wb") as f:
                f.write(audio)
            out(f"wrote {path}")
            if not args.output:
                _play_audio(path)
        elif args.action == "transcribe":
            if not args.file:
                err("usage: colons voice transcribe <audio-file>")
                return
            result = await client.transcribe(args.file, model=args.model or "",
                                             language=args.language)
            out(result.get("text", ""))


async def cmd_status(args):
    async with ColonsClient(args.url, args.api_key) as client:
        out(json.dumps(await client.usage(args.user), indent=2))


async def cmd_avatars(args):
    async with ColonsClient(args.url, args.api_key) as client:
        avatars = await client.list_avatars()
        table("Avatars", ["id", "name", "kind", "personality"],
              [[a["id"], a["name"], a["kind"], a["personality"]] for a in avatars])


# --------------------------------------------------------------------------- #
# Schedules (cronjobs)
# --------------------------------------------------------------------------- #

def _format_next(ts) -> str:
    if not ts:
        return "-"
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


async def cmd_cron(args):
    async with ColonsClient(args.url, args.api_key) as client:
        action = args.action

        if action == "validate":
            try:
                res = await client.validate_cron(args.expression)
                out(f"valid: {res['description']}  next: {res.get('next_run_iso', '-')}")
            except Exception as e:
                err(f"invalid: {e}")
            return

        if action == "list":
            data = await client.list_schedules()
            schedules = data["schedules"]
            status = data.get("status", {})
            if not schedules:
                out("No schedules yet. Add one: colons cron add \"0 9 * * 1-5\" \"...\"")
                return
            table(
                "Schedules",
                ["id", "name", "cron", "next run", "runs", "last"],
                [[s["id"], (s["name"] or "")[:28], s["cron"], _format_next(s.get("next_run")),
                  s.get("run_count", 0), s.get("last_status") or "-"]
                 for s in schedules],
            )
            out(f"[dim]scheduler running={status.get('running')} tz={status.get('timezone')} "
                f"enabled={status.get('enabled')}/{status.get('total')}[/dim]" if RICH else
                f"scheduler running={status.get('running')}")
            return

        if action == "add":
            try:
                s = await client.add_schedule(
                    cron=args.expression, prompt=args.prompt, name=args.name,
                    user_id=args.user, notify_adapter=args.notify_adapter,
                    notify_chat_id=args.notify_chat_id, timezone=getattr(args, "timezone", None),
                    continuity=getattr(args, "continuity", False),
                    notes=getattr(args, "notes", "") or "",
                    monitor_command=getattr(args, "monitor_command", None),
                )
                out(f"created schedule {s['id']} - next run {_format_next(s.get('next_run'))}")
            except Exception as e:
                err(str(e))
            return

        if action == "remove":
            try:
                await client.remove_schedule(args.schedule_id)
                out(f"removed {args.schedule_id}")
            except Exception as e:
                err(str(e))
            return

        if action == "enable":
            res = await client.update_schedule(args.schedule_id, enabled=True)
            out(f"enabled {res['id']} - next {_format_next(res.get('next_run'))}")
            return

        if action == "disable":
            res = await client.update_schedule(args.schedule_id, enabled=False)
            out(f"disabled {res['id']}")
            return

        if action == "run":
            result = await client.run_schedule(args.schedule_id)
            render_markdown(result.get("result") or "(no output)")
            return

        if action == "history":
            data = await client.schedule_history(limit=args.limit)
            runs = data.get("runs", [])
            if not runs:
                out("No runs yet.")
                return
            table("Recent runs", ["when", "name", "status", "ms"],
                  [[_format_next(r.get("at")), (r.get("name") or "")[:28],
                    r.get("status"), f"{r.get('duration_ms', 0):.0f}"] for r in reversed(runs)])


# --------------------------------------------------------------------------- #
# Bots
# --------------------------------------------------------------------------- #

async def cmd_bots(args):
    async with ColonsClient(args.url, args.api_key) as client:
        action = args.action
        if action == "list":
            bots = await client.list_bots()
            if not bots:
                out("No bots yet. Create one: colons bots create --name Researcher")
                return
            table("Bots", ["handle", "name", "title", "avatar", "model", "enabled"],
                  [[b["handle"], b["name"], (b.get("title") or "")[:24],
                    b.get("avatar") or "-", b.get("model") or "(default)",
                    "yes" if b.get("enabled", True) else "no"] for b in bots])
        elif action == "create":
            if not args.name:
                err("usage: colons bots create --name NAME [--title T] [--description D]")
                return
            bot = await client.create_bot(args.name, title=args.title,
                                          description=args.description,
                                          persona=args.persona, avatar=args.avatar,
                                          model=args.model)
            out(f"created {bot['handle']} ({bot['name']})")
        elif action == "update":
            if not args.bot_id:
                err("usage: colons bots update <bot-id> [--name N] [--title T] ...")
                return
            changes = {k: v for k, v in {
                "name": args.name or None, "title": args.title or None,
                "description": args.description or None, "persona": args.persona or None,
                "avatar": args.avatar or None, "model": args.model or None,
            }.items() if v is not None}
            bot = await client.update_bot(args.bot_id, **changes)
            out(f"updated {bot['handle']}")
        elif action == "delete":
            if not args.bot_id:
                err("usage: colons bots delete <bot-id>")
                return
            await client.delete_bot(args.bot_id)
            out(f"deleted {args.bot_id}")


# --------------------------------------------------------------------------- #
# Rooms
# --------------------------------------------------------------------------- #

async def cmd_rooms(args):
    async with ColonsClient(args.url, args.api_key) as client:
        action = args.action
        if action == "list":
            rooms = await client.list_rooms()
            if not rooms:
                out("No rooms yet. Create one: colons rooms create --name standup --members a,b")
                return
            table("Rooms", ["id", "name", "members", "msgs", "needs you"],
                  [[r["id"], r["name"], ", ".join("@" + m for m in r["members"]),
                    r.get("message_count", 0), "yes" if r.get("needs_user") else ""]
                   for r in rooms])
        elif action == "create":
            members = [m.strip() for m in (args.members or "").split(",") if m.strip()]
            if not args.name or len(members) < 2:
                err("usage: colons rooms create --name NAME --members bot1,bot2")
                return
            room = await client.create_room(args.name, members)
            out(f"created room {room['id']} ({room['name']})")
        elif action == "send":
            if not args.room_id or not args.text:
                err("usage: colons rooms send <room-id> --text 'message'")
                return
            with console.status("[green]members are deliberating…") if RICH else _nullstatus():
                messages = await client.send_room_message(args.room_id, args.text)
            for m in messages:
                speaker = "you" if m["speaker"] == "user" else f"@{m['speaker']}"
                out(f"{speaker}: {m['content']}")
        elif action == "history":
            if not args.room_id:
                err("usage: colons rooms history <room-id>")
                return
            for m in await client.room_messages(args.room_id, limit=args.limit):
                speaker = "you" if m["speaker"] == "user" else f"@{m['speaker']}"
                out(f"{speaker}: {m['content']}")
        elif action == "delete":
            if not args.room_id:
                err("usage: colons rooms delete <room-id>")
                return
            await client.delete_room(args.room_id)
            out(f"deleted {args.room_id}")


class _nullstatus:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------- #
# Peers
# --------------------------------------------------------------------------- #

async def cmd_peer(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "list":
            data = await client.list_peers()
            peers = data.get("peers", [])
            if not peers:
                out("No peers configured. Add them under `peers:` in colons.yaml:")
                out("  peers:\n    spark:\n      url: http://spark.lan:8000\n      api_key: ...")
                return
            table("Peers", ["name", "url", "agents"],
                  [[p["name"], p["url"], ", ".join(p.get("agents") or [])] for p in peers])
        elif args.action == "dm":
            if not args.target or not args.text:
                err("usage: colons peer dm <peer[/agent]> --text 'message'")
                return
            result = await client.peer_dm(args.target, args.text)
            if result.get("status") == "error":
                err(result.get("message", "peer delivery failed"))
            else:
                render_markdown(result.get("reply") or "(no reply)")
        elif args.action == "inbox":
            # Show a bot's canonical Bot Chat (where peer DMs land)
            bot_id = args.target or "default"
            session_key = f"bot-chat-{bot_id}"
            sessions = await client.list_sessions(args.user, agent_id=f"bot-{bot_id}")
            session = next((s for s in sessions if s["id"] == session_key), None)
            if not session:
                out(f"No Bot Chat for @{bot_id} yet.")
                return
            data = await client.get_session(session_key, args.user, agent_id=f"bot-{bot_id}")
            for m in data.get("messages", [])[-args.limit:]:
                speaker = "peer" if m["role"] == "user" else f"@{bot_id}"
                out(f"{speaker}: {m['content'][:300]}")


# --------------------------------------------------------------------------- #
# Doctor
# --------------------------------------------------------------------------- #

DOCTOR_ICONS = {"pass": "✓", "warn": "!", "fail": "✗", "skip": "-"}
DOCTOR_COLORS = {"pass": "green", "warn": "yellow", "fail": "red", "skip": "dim"}


async def cmd_doctor(args):
    async with ColonsClient(args.url, args.api_key) as client:
        try:
            report = await client.doctor(check_provider=not args.skip_provider)
        except Exception as e:
            err(f"cannot reach Colons at {args.url}: {e}")
            sys.exit(2)

        if args.json:
            out(json.dumps(report, indent=2))
            sys.exit(0 if report["ok"] else 1)

        summary = report["summary"]
        for check in report["checks"]:
            icon = DOCTOR_ICONS.get(check["status"], "?")
            color = DOCTOR_COLORS.get(check["status"], "white")
            line = f"  {icon} {check['name']:<26} {check['detail']}"
            if RICH:
                out(f"[{color}]{line}[/{color}]")
            else:
                out(line)
            if check.get("hint"):
                out(f"      → {check['hint']}" if not RICH else f"      [dim]→ {check['hint']}[/dim]")

        out()
        out(f"  {summary.get('pass', 0)} passed, {summary.get('warn', 0)} warnings, "
            f"{summary.get('fail', 0)} failures  ({report['duration_ms']:.0f}ms)")
        sys.exit(0 if report["ok"] else 1)


# --------------------------------------------------------------------------- #
# Logs & debug
# --------------------------------------------------------------------------- #

async def cmd_logs(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.clear:
            await client.clear_logs()
            out("logs cleared")
            return
        if args.level_set:
            res = await client.set_log_level(args.level_set)
            out(f"log level set to {res['level']}")
            return

        data = await client.logs(level=args.level, limit=args.limit,
                                 logger_prefix=args.logger)
        for record in data["records"]:
            color = {"DEBUG": "dim", "INFO": "white", "WARNING": "yellow",
                     "ERROR": "red", "CRITICAL": "bold red"}.get(record["level"], "white")
            line = (f"{record['iso'][11:23]} {record['level']:<7} "
                    f"{record['logger']:<28} {record['message']}")
            if RICH:
                out(f"[{color}]{line}[/{color}]")
            else:
                out(line)


async def cmd_debug(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "stats":
            stats = await client.debug_stats()
            table("Runtime", ["metric", "value"],
                  [[k, str(v)] for k, v in stats.items() if not isinstance(v, dict)])
            for key, value in stats.items():
                if isinstance(value, dict):
                    out(f"\n{key}:")
                    for k, v in value.items():
                        out(f"  {k}: {v}")
        elif args.action == "config":
            out(json.dumps(await client.debug_config(), indent=2))
        elif args.action == "env":
            out(json.dumps(await client.debug_env(), indent=2))


async def cmd_messaging(args):
    async with ColonsClient(args.url, args.api_key) as client:
        if args.action == "status":
            data = await client.messaging_status()
            if not data.get("enabled"):
                out("Messaging is not enabled. Configure adapters in colons.yaml or .env:")
                out("  TELEGRAM_BOT_TOKEN / DISCORD_BOT_TOKEN / SLACK_WEBHOOK_URL ...")
                return
            table("Messaging adapters", ["name", "configured", "connected", "uptime", "last error"],
                  [[a["name"], a["configured"], a["connected"],
                    f"{a.get('uptime_seconds', 0):.0f}s", (a.get("last_error") or "-")[:40]]
                   for a in data["adapters"]])
        elif args.action == "send":
            res = await client.messaging_send(args.adapter, args.chat_id, args.text)
            out(f"sent: {res}")


def cmd_init(args):
    path = args.path or "colons.yaml"
    try:
        import yaml
        config = {
            "provider": {"name": "ollama", "base_url": "http://localhost:11434",
                         "model": "llama3.1", "temperature": 0.7, "max_tokens": 4096},
            "memory": {"store_url": "colons.db", "cache_url": "", "embedding_dim": 1536},
            "tools": {"workspace": ".", "auto_approve": False,
                      "shell_allowlist": ["ls", "cat", "pwd", "python3", "git"]},
            "voice": {"enabled": False, "tts_engine": "edge", "voice": "en-US-AriaNeural"},
            "agent": {"name": "Colons", "proactive": True, "max_iterations": 15},
            "scheduler": {"enabled": True, "tick_interval": 15, "timezone": "UTC"},
            "bot_messaging": True,
            "peers": {},
            "peers_timeout": 120,
            "messaging": {
                "telegram": {"enabled": False, "bot_token": ""},
                "discord": {"enabled": False, "bot_token": ""},
                "slack": {"enabled": False, "webhook_url": "", "signing_secret": ""},
                "webhooks": {},
            },
            "logging": {"level": "INFO", "file": "", "buffer_size": 1000, "json_format": False},
            "server": {"host": "0.0.0.0", "port": 8000, "api_keys": []},
            "ui": {"title": "Colons", "theme": "dark"},
        }
        with open(path, "w") as f:
            yaml.safe_dump(config, f, sort_keys=False)
        out(f"wrote {path}")
    except ImportError:
        err("pyyaml required for YAML config: pip install pyyaml")


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="colons", description="Colons - always-on AI agent")
    p.add_argument("--url", default=DEFAULT_URL, help="Colons server URL")
    p.add_argument("--api-key", default=DEFAULT_KEY, help="API key")
    p.add_argument("--user", default="default", help="User id")

    sub = p.add_subparsers(dest="command")

    # default interactive chat
    chat = sub.add_parser("chat", help="Interactive chat")
    chat.add_argument("--session", default=None)
    chat.add_argument("--speak", action="store_true", help="Speak responses via TTS")
    chat.add_argument("--no-reasoning", action="store_true")

    run = sub.add_parser("run", help="Send one message")
    run.add_argument("message")
    run.add_argument("--usage", action="store_true")

    serve = sub.add_parser("serve", help="Start the API server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    task = sub.add_parser("task", help="Manage tasks")
    task.add_argument("action", choices=["add", "list", "run"])
    task.add_argument("--description", default="")
    task.add_argument("--priority", type=int, default=1)
    task.add_argument("--status", default=None)
    task.add_argument("--task-id", default="")

    mem = sub.add_parser("memory", help="Memory tools")
    mem.add_argument("action", choices=["search", "list"])
    mem.add_argument("--query", default="")
    mem.add_argument("--limit", type=int, default=5)

    tools = sub.add_parser("tools", help="Tool tools")
    tools.add_argument("action", choices=["list", "run"])
    tools.add_argument("--tool", default="")
    tools.add_argument("--arguments", default="")

    prov = sub.add_parser("provider", help="Provider management")
    prov.add_argument("action", choices=["list", "switch"])
    prov.add_argument("--name", default="")
    prov.add_argument("--api-key-opt", dest="api_key_opt", default=None)
    prov.add_argument("--base-url", default=None)
    prov.add_argument("--model", default=None)

    voice = sub.add_parser("voice", help="Voice: Edge TTS + speech-to-text")
    voice.add_argument("action", choices=["voices", "speak", "transcribe"])
    voice.add_argument("file", nargs="?", default="", help="Audio file for transcribe")
    voice.add_argument("--text", default="Hello from Colons")
    voice.add_argument("--voice", default=None)
    voice.add_argument("--locale", default=None)
    voice.add_argument("--output", default=None)
    voice.add_argument("--model", default="", help="Transcription model (default whisper-1)")
    voice.add_argument("--language", default=None, help="ISO-639-1 language hint")

    # Cronjobs
    cron = sub.add_parser("cron", help="Scheduled jobs (cron)")
    cron.add_argument("action",
                      choices=["add", "list", "remove", "enable", "disable", "run",
                               "history", "validate"])
    cron.add_argument("expression", nargs="?", default="",
                      help="Cron expression, @every 5m, or @at ISO datetime")
    cron.add_argument("prompt", nargs="?", default="", help="Prompt for the agent")
    cron.add_argument("--name", default="")
    cron.add_argument("--schedule-id", default="")
    cron.add_argument("--notify-adapter", default=None,
                      help="Messaging adapter to deliver results (e.g. telegram)")
    cron.add_argument("--notify-chat-id", default=None)
    cron.add_argument("--timezone", default=None)
    cron.add_argument("--limit", type=int, default=50)
    cron.add_argument("--continuity", action="store_true",
                      help="Pass the previous run's result into the next run")
    cron.add_argument("--notes", default="",
                      help="Durable notepad prepended to every run")
    cron.add_argument("--monitor-command", default=None,
                      help="Shell command; skip the LLM entirely when its output is unchanged")

    # Bots
    bots = sub.add_parser("bots", help="Named agents (roster)")
    bots.add_argument("action", choices=["list", "create", "update", "delete"])
    bots.add_argument("bot_id", nargs="?", default="")
    bots.add_argument("--name", default="")
    bots.add_argument("--title", default="")
    bots.add_argument("--description", default="")
    bots.add_argument("--persona", default="")
    bots.add_argument("--avatar", default="")
    bots.add_argument("--model", default="")

    # Rooms
    rooms = sub.add_parser("rooms", help="Group rooms (multi-bot chats)")
    rooms.add_argument("action", choices=["list", "create", "send", "history", "delete"])
    rooms.add_argument("room_id", nargs="?", default="")
    rooms.add_argument("--name", default="")
    rooms.add_argument("--members", default="", help="Comma-separated bot ids")
    rooms.add_argument("--text", default="")
    rooms.add_argument("--limit", type=int, default=50)

    # Peers
    peer = sub.add_parser("peer", help="Peer messaging across Colons instances")
    peer.add_argument("action", choices=["list", "dm", "inbox"])
    peer.add_argument("target", nargs="?", default="", help="peer[/agent]")
    peer.add_argument("--text", default="")
    peer.add_argument("--limit", type=int, default=20)

    # Doctor
    doc = sub.add_parser("doctor", help="Diagnose environment, config and connections")
    doc.add_argument("--json", action="store_true", help="Machine-readable output")
    doc.add_argument("--skip-provider", action="store_true",
                     help="Do not call the provider API during checks")

    # Logs
    logs = sub.add_parser("logs", help="View server logs")
    logs.add_argument("--level", default="INFO", help="DEBUG|INFO|WARNING|ERROR")
    logs.add_argument("--limit", type=int, default=50)
    logs.add_argument("--logger", default=None, help="Filter by logger name prefix")
    logs.add_argument("--clear", action="store_true", help="Clear the in-memory log buffer")
    logs.add_argument("--level-set", dest="level_set", default=None,
                      help="Change the server log level")

    # Debug
    dbg = sub.add_parser("debug", help="Debug info (stats/config/env)")
    dbg.add_argument("action", choices=["stats", "config", "env"])

    # Messaging
    msg = sub.add_parser("messaging", help="Messaging integrations (Telegram, Discord, Slack)")
    msg.add_argument("action", choices=["status", "send"])
    msg.add_argument("--adapter", default="")
    msg.add_argument("--chat-id", dest="chat_id", default="")
    msg.add_argument("--text", default="")

    sub.add_parser("status", help="Usage stats")
    sub.add_parser("avatars", help="List avatars")

    init = sub.add_parser("init", help="Write a starter config")
    init.add_argument("--path", default=None)

    return p


def main(argv: Optional[List[str]] = None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        run_server(args.host, args.port, args.reload)
        return

    if args.command == "init":
        cmd_init(args)
        return

    if args.command == "run":
        asyncio.run(cmd_run(args))
        return

    if args.command == "task":
        asyncio.run(cmd_task(args))
        return

    if args.command == "memory":
        asyncio.run(cmd_memory(args))
        return

    if args.command == "tools":
        asyncio.run(cmd_tools(args))
        return

    if args.command == "provider":
        asyncio.run(cmd_provider(args))
        return

    if args.command == "voice":
        asyncio.run(cmd_voice(args))
        return

    if args.command == "status":
        asyncio.run(cmd_status(args))
        return

    if args.command == "avatars":
        asyncio.run(cmd_avatars(args))
        return

    if args.command == "cron":
        asyncio.run(cmd_cron(args))
        return

    if args.command == "doctor":
        asyncio.run(cmd_doctor(args))
        return

    if args.command == "logs":
        asyncio.run(cmd_logs(args))
        return

    if args.command == "debug":
        asyncio.run(cmd_debug(args))
        return

    if args.command == "messaging":
        asyncio.run(cmd_messaging(args))
        return

    if args.command == "bots":
        asyncio.run(cmd_bots(args))
        return

    if args.command == "rooms":
        asyncio.run(cmd_rooms(args))
        return

    if args.command == "peer":
        asyncio.run(cmd_peer(args))
        return

    # default: interactive chat
    session = getattr(args, "session", None)
    speak = getattr(args, "speak", False)
    show_reasoning = not getattr(args, "no_reasoning", False)

    async def _run():
        async with ColonsClient(args.url, args.api_key) as client:
            await interactive(client, session, args.user, speak, show_reasoning)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
