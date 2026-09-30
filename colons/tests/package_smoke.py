"""Check an installed wheel's entry points and bundled UI, without source imports."""

import argparse
import asyncio
import subprocess
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

parser = argparse.ArgumentParser()
parser.add_argument("--venv", required=True)
args = parser.parse_args()
venv = Path(args.venv).resolve()
python = venv / "bin/python"
cli = venv / "bin/colons"
adapter = venv / "bin/colons-hermes"
subprocess.run([str(cli), "--version"], check=True)
subprocess.run([str(cli), "setup", "--help"], check=True, stdout=subprocess.DEVNULL)
subprocess.run([str(python), "-c", """
from pathlib import Path
import colons_api
import colons_adapters.hermes
import colons_cli.tui
root = Path(colons_api.__file__).parent / 'static'
assert (root / 'index.html').is_file(), 'Wheel is missing its UI'
assert list((root / 'assets').glob('*.js')), 'Wheel is missing its JavaScript'
"""], check=True)


async def handshake():
    async with stdio_client(StdioServerParameters(command=str(adapter))) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            assert result.serverInfo.name == "Colons"
            assert len((await session.list_tools()).tools) == 9


asyncio.run(handshake())
print("PASS: installed wheel CLI, terminal UI, Hermes handshake, and bundled web assets")
