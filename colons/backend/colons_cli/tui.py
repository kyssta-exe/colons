"""Full-screen terminal chat with sessions, agents, streaming, and approvals."""

import json
import webbrowser

from colons_core import __version__
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Markdown, Select, Static


class ApprovalScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "deny", "Deny")]
    CSS = """
    ApprovalScreen { align: center middle; background: $background 80%; }
    #approval-dialog { width: 76; max-width: 95%; height: auto; max-height: 90%; padding: 1 2; border: round $warning; background: $surface; }
    #approval-actions { height: 3; align: right middle; margin-top: 1; }
    #approval-actions Button { margin-left: 1; }
    """

    def __init__(self, event):
        super().__init__()
        self.event = event

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label("Allow this tool call?")
            yield Static(Text(self.event.get("tool", "Tool"), style="bold"))
            yield Static(Text(json.dumps(self.event.get("arguments", {}), indent=2)))
            with Horizontal(id="approval-actions"):
                yield Button("Deny", id="deny", variant="default")
                yield Button("Allow once", id="allow", variant="warning")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "allow")

    def action_deny(self):
        self.dismiss(False)


class ColonsTUI(App):
    TITLE = "Colons"
    SUB_TITLE = f"v{__version__}"
    BINDINGS = [("ctrl+q", "quit", "Quit"), ("ctrl+n", "new_chat", "New chat"),
                ("escape", "stop_reply", "Stop"), ("f2", "setup", "Setup"),
                ("f3", "web", "Web"), ("ctrl+b", "sidebar", "Sidebar")]
    CSS = """
    Screen { background: #0e1420; }
    Header { background: #182338; }
    #body { height: 1fr; }
    #sidebar { width: 28; min-width: 20; padding: 1; background: #131d2c; border-right: solid #28364c; }
    #sidebar Select { margin-bottom: 1; }
    #sessions { height: 1fr; background: transparent; }
    #main { width: 1fr; }
    #transcript { height: 1fr; padding: 1 2; }
    .message { padding: 1 2; margin-bottom: 1; background: #182338; border: round #28364c; }
    .user-message { background: #1c3049; border: round #33516e; }
    .note { color: #91a4bd; margin-bottom: 1; }
    #status { height: 1; padding-left: 2; color: #91a4bd; }
    #composer { height: 3; margin: 1 2; }
    #message { width: 1fr; }
    #send { min-width: 5; width: 5; margin-left: 1; }
    Footer { background: #182338; }
    """

    def __init__(self, client, user_id="default", session_id=None):
        super().__init__()
        self.client = client
        self.user_id = user_id
        self.session_id = session_id
        self.agent_id = None
        self.reply_worker = None
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label(Text("●\n●  Colons", style="bold"))
                yield Button("New chat", id="new-chat")
                yield Select([("Colons", "")], value="", allow_blank=False, id="agent")
                yield Label("Conversations")
                yield ListView(id="sessions")
            with Vertical(id="main"):
                yield VerticalScroll(id="transcript")
                yield Static("Connecting…", id="status")
                with Horizontal(id="composer"):
                    yield Input(placeholder="Ask Colons anything…", id="message")
                    yield Button("➤", id="send", variant="primary")
        yield Footer()

    def on_mount(self):
        self.query_one("#message", Input).focus()
        self.run_worker(self.connect(), exit_on_error=False)

    async def connect(self):
        try:
            health = await self.client.health()
            self.query_one("#status", Static).update(Text(
                f"{self.client.base_url} · {health.get('provider', '')} · Ready"))
            bots = await self.client.list_bots()
            self.query_one("#agent", Select).set_options([
                ("Colons", ""), *[(bot["name"], bot["agent_id"]) for bot in bots]])
            await self.refresh_sessions()
            if self.session_id:
                await self.load_session(self.session_id)
            else:
                await self.note("A calmer space to think and get things done. Enter sends; Esc stops a reply.")
        except Exception as exc:
            await self.note(f"Connection failed: {exc}. Check the URL or server API key.")

    async def note(self, message):
        await self.query_one("#transcript", VerticalScroll).mount(Static(Text(message), classes="note"))

    async def refresh_sessions(self):
        sessions = await self.client.list_sessions(self.user_id, self.agent_id)
        view = self.query_one("#sessions", ListView)
        await view.clear()
        for session in sessions[:30]:
            item = ListItem(Label(Text(session.get("title", "Chat")[:28])))
            item.session_id = session["id"]
            await view.append(item)

    async def load_session(self, session_id):
        if self.busy:
            return
        data = await self.client.get_session(session_id, self.user_id, self.agent_id)
        self.session_id = session_id
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        for message in data.get("messages", []):
            if message.get("role") in ("user", "assistant"):
                await transcript.mount(Markdown(message.get("content") or "",
                                               classes="message user-message" if message["role"] == "user" else "message"))
        transcript.scroll_end(animate=False)

    async def on_list_view_selected(self, event: ListView.Selected):
        if event.item and not self.busy:
            self.run_worker(self.load_session(event.item.session_id), exit_on_error=False)

    async def on_select_changed(self, event: Select.Changed):
        if event.select.id == "agent" and event.value is not Select.BLANK and not self.busy:
            selected = event.value or None
            if selected != self.agent_id:
                self.agent_id = selected
                await self.action_new_chat()
                self.run_worker(self.refresh_sessions(), exit_on_error=False)

    async def on_input_submitted(self, event: Input.Submitted):
        await self.send_message()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "send":
            if self.busy:
                self.action_stop_reply()
            else:
                await self.send_message()
        elif event.button.id == "new-chat":
            await self.action_new_chat()

    async def send_message(self):
        field = self.query_one("#message", Input)
        message = field.value.strip()
        if not message or self.busy:
            return
        field.value = ""
        self.busy = True
        self.query_one("#agent", Select).disabled = True
        self.query_one("#send", Button).label = "■"
        self.query_one("#status", Static).update("Thinking…")
        await self.query_one("#transcript", VerticalScroll).mount(Markdown(message, classes="message user-message"))
        self.reply_worker = self.run_worker(self.reply(message), group="reply", exit_on_error=False)

    async def approve(self, event):
        self.query_one("#status", Static).update("Waiting for your approval…")
        return await self.push_screen_wait(ApprovalScreen(event))

    async def reply(self, message):
        transcript = self.query_one("#transcript", VerticalScroll)
        response = Markdown("", classes="message")
        await transcript.mount(response)
        content = ""
        try:
            async for event in self.client.chat_stream(
                message, self.session_id, self.user_id, self.agent_id, approval_handler=self.approve,
            ):
                kind = event.get("type")
                if kind in ("start", "done") and event.get("session_id"):
                    self.session_id = event["session_id"]
                if kind == "delta":
                    content += event.get("content", "")
                    response.update(content)
                    transcript.scroll_end(animate=False)
                elif kind == "tool_call":
                    self.query_one("#status", Static).update(Text(f"Using {event.get('tool')}…"))
                elif kind == "tool_result":
                    await self.note(f"{'✓' if event.get('success') else '×'} {event.get('tool')}"
                                    + (f": {event.get('error')}" if event.get('error') else ""))
                elif kind == "error":
                    await self.note(event.get("message", "Request failed"))
                elif kind == "done" and not content:
                    response.update(event.get("content", ""))
            await self.refresh_sessions()
        except Exception as exc:
            await self.note(f"Reply failed: {exc}")
        finally:
            self.busy = False
            self.query_one("#agent", Select).disabled = False
            self.query_one("#send", Button).label = "➤"
            self.query_one("#status", Static).update("Ready")

    def action_stop_reply(self):
        if self.reply_worker and self.busy:
            self.reply_worker.cancel()
            if isinstance(self.screen, ApprovalScreen):
                self.screen.dismiss(False)

    async def action_new_chat(self):
        if self.busy:
            return
        self.session_id = None
        await self.query_one("#transcript", VerticalScroll).remove_children()
        self.query_one("#message", Input).focus()

    def action_sidebar(self):
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display

    def action_web(self):
        webbrowser.open(self.client.base_url)

    def action_setup(self):
        from .configuration import setup
        with self.suspend():
            setup()
        self.notify("Settings saved. Restart the server to apply changes.")

    async def on_unmount(self):
        await self.client.close()
