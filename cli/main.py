"""
Colons CLI - Command line interface for interacting with the agent
"""
import asyncio
import sys
import argparse
from typing import Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.live import Live
from rich.text import Text

# CLI Configuration
DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_MODEL = "llama3.1"

console = Console()

class ColonsCLI:
    """Command-line interface for Colons"""

    def __init__(self, api_url: str = DEFAULT_API_URL):
        self.api_url = api_url.rstrip('/')
        self.client = httpx.AsyncClient(base_url=self.api_url, timeout=300.0)
        self.conversation_history: list = []

    async def connect(self) -> bool:
        """Check API connection"""
        try:
            response = await self.client.get("/health")
            return response.status_code == 200
        except Exception:
            return False

    async def chat(self, message: str, stream: bool = True):
        """Send a message to the agent"""

        if stream:
            await self._chat_stream(message)
        else:
            await self._chat_non_stream(message)

    async def _chat_stream(self, message: str):
        """Stream chat response"""

        with console.status("[bold green]Thinking...") as status:
            try:
                async with self.client.stream(
                    "POST",
                    "/ws/chat",
                    json={"message": message, "stream": True}
                ) as response:
                    full_content = ""
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                data = httpx._models.Response.json.loads(line)
                                if data.get("type") == "chunk":
                                    content = data.get("content", "")
                                    full_content += content
                                    console.print(content, end="", markup=False)
                            except Exception:
                                pass

                    console.print()  # New line after response

                    # Show usage if available
                    if data.get("usage"):
                        usage = data["usage"]
                        console.print(
                            f"\n[dim]Tokens: {usage.get('total_tokens', 0)} | "
                            f"Cost: ~${usage.get('total_tokens', 0) * 0.000015:.4f}[/dim]"
                        )

            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

    async def _chat_non_stream(self, message: str):
        """Non-streaming chat"""
        try:
            response = await self.client.post(
                "/chat",
                json={"message": message, "stream": False}
            )

            if response.status_code == 200:
                data = response.json()
                console.print(data["content"])

                usage = data.get("usage", {})
                console.print(
                    f"\n[dim]Tokens: {usage.get('total_tokens', 0)} | "
                    f"Model: {data.get('model', 'unknown')}[/dim]"
                )
            else:
                console.print(f"[red]Error: {response.status_code}[/red]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def create_task(self, description: str):
        """Create a new task"""
        try:
            response = await self.client.post(
                "/tasks",
                json={"description": description}
            )

            if response.status_code == 200:
                task = response.json()
                console.print(Panel(f"Task created: {task['id']}\n{task['description']}", title="Task"))
                return task
            else:
                console.print(f"[red]Failed to create task: {response.status_code}[/red]")
                return None

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return None

    async def execute_task(self, task_id: str):
        """Execute a task"""
        try:
            response = await self.client.post(f"/tasks/{task_id}/execute")

            if response.status_code == 200:
                task = response.json()
                console.print(Panel(f"Task {task_id} completed", title="Success"))
                return task
            else:
                console.print(f"[red]Failed: {response.status_code}[/red]")
                return None

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def list_tasks(self):
        """List all tasks"""
        try:
            response = await self.client.get("/tasks")

            if response.status_code == 200:
                tasks = response.json()

                table = Table(title="Tasks")
                table.add_column("ID", style="cyan", max_width=20)
                table.add_column("Description", max_width=40)
                table.add_column("Status", style="green")

                for task in tasks:
                    table.add_row(
                        task["id"][:20],
                        task["description"][:40],
                        task["status"]
                    )

                console.print(table)
            else:
                console.print(f"[red]Failed: {response.status_code}[/red]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def search_memory(self, query: str):
        """Search memory"""
        try:
            response = await self.client.post(
                "/memory/search",
                json={"query": query}
            )

            if response.status_code == 200:
                results = response.json()["results"]

                for i, result in enumerate(results, 1):
                    console.print(f"\n[bold]{i}. {result['content']}[/bold]")
                    console.print(f"   Similarity: {result.get('similarity', 0):.2f}")
            else:
                console.print(f"[red]Failed: {response.status_code}[/red]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def get_usage(self):
        """Show usage statistics"""
        try:
            response = await self.client.get("/usage")

            if response.status_code == 200:
                usage = response.json()

                table = Table(title="Usage Statistics")
                table.add_column("Metric", style="cyan")
                table.add_column("Value", style="green")

                table.add_row("Prompt Tokens", str(usage.get("prompt_tokens", 0)))
                table.add_row("Completion Tokens", str(usage.get("completion_tokens", 0)))
                table.add_row("Total Tokens", str(usage.get("total_tokens", 0)))
                table.add_row("Estimated Cost", f"${usage.get('estimated_cost', 0):.6f}")

                console.print(table)
            else:
                console.print(f"[red]Failed: {response.status_code}[/red]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def show_status(self):
        """Show agent status"""
        try:
            response = await self.client.get("/agent/status")

            if response.status_code == 200:
                status = response.json()

                table = Table(title="Agent Status")
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="green")

                for key, value in status.items():
                    table.add_row(key, str(value))

                console.print(table)
            else:
                console.print(f"[red]Failed: {response.status_code}[/red]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def interactive_mode(self):
        """Interactive chat mode"""
        console.print(Panel("[bold green]Colons CLI - Interactive Mode[/bold green]\nType 'help' for commands, 'quit' to exit"))

        connected = await self.connect()
        if not connected:
            console.print("[red]Failed to connect to Colons API[/red]")
            return

        console.print("[green]Connected![/green]")

        while True:
            try:
                user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")

                if not user_input:
                    continue

                # Commands
                if user_input.lower() in ["quit", "exit", "q"]:
                    break
                elif user_input.lower() == "help":
                    self._show_help()
                    continue
                elif user_input.lower() == "clear":
                    console.clear()
                    continue
                elif user_input.lower().startswith("/task "):
                    desc = user_input[6:]
                    await self.create_task(desc)
                    continue
                elif user_input.lower().startswith("/tasks"):
                    await self.list_tasks()
                    continue
                elif user_input.lower().startswith("/memory "):
                    query = user_input[8:]
                    await self.search_memory(query)
                    continue
                elif user_input.lower() == "/usage":
                    await self.get_usage()
                    continue
                elif user_input.lower() == "/status":
                    await self.show_status()
                    continue
                elif user_input.lower().startswith("/model "):
                    model = user_input[7:]
                    # TODO: Implement model switching
                    console.print(f"[yellow]Model switching not yet implemented[/yellow]")
                    continue

                # Regular chat
                await self.chat(user_input, stream=True)

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted[/yellow]")
                continue
            except EOFError:
                break

        console.print("[green]Goodbye![/green]")

    def _show_help(self):
        """Show help text"""
        help_text = """
[bold]Commands:[/bold]
  /task <desc>  - Create a new task
  /tasks        - List all tasks
  /memory <q>   - Search memory
  /usage        - Show usage statistics
  /status       - Show agent status
  /model <m>    - Switch model (coming soon)
  /clear        - Clear screen
  /help         - Show this help
  /quit         - Exit

[bold]Regular messages[/bold] are sent to the agent as chat.
        """
        console.print(Panel(help_text, title="Help"))

async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Colons CLI - Interact with your AI agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  colons                          # Interactive mode
  colons "Hello, how are you?"    # Single message
  colons /task "Build a website"  # Create a task
  colons /usage                   # Show usage stats
        """
    )

    parser.add_argument(
        "message",
        nargs="?",
        help="Message to send to the agent"
    )

    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"API URL (default: {DEFAULT_API_URL})"
    )

    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming"
    )

    args = parser.parse_args()

    cli = ColonsCLI(api_url=args.api_url)

    if args.message:
        # Single message mode
        connected = await cli.connect()
        if not connected:
            console.print("[red]Failed to connect to Colons API[/red]")
            sys.exit(1)

        await cli.chat(args.message, stream=not args.no_stream)
    else:
        # Interactive mode
        await cli.interactive_mode()

if __name__ == "__main__":
    asyncio.run(main())