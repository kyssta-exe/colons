"""Native browser integration with a local page; no network or LLM required."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from colons_core.browser import BrowserTools
from colons_core.tools import ToolManager, ToolRegistry

HTML = b'''<html><head><title>Browser test</title></head><body>
<label for="name">Name</label><input id="name"><button onclick="document.getElementById('result').textContent='Hello '+document.getElementById('name').value">Greet</button>
<p id="result">Ready</p><button style="display:none">Invisible</button></body></html>'''


@pytest.fixture
def local_page():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.asyncio
async def test_native_browser_warm_sessions_refs_and_isolation(tmp_path, local_page):
    pytest.importorskip('playwright')
    if not os.environ.get('COLONS_BROWSER_EXECUTABLE'):
        pytest.skip('Set COLONS_BROWSER_EXECUTABLE to run the native Chromium integration')
    first = BrowserTools(str(tmp_path), 'first')
    second = BrowserTools(str(tmp_path), 'second')
    try:
        initial = await first.open(local_page)
        assert initial['title'] == 'Browser test'
        assert all(item['name'] != 'Invisible' for item in initial['controls'])
        name = next(item['ref'] for item in initial['controls'] if item['name'] == 'Name')
        filled = await first.fill(name, 'Colons')
        button = next(item['ref'] for item in filled['controls'] if item['name'] == 'Greet')
        clicked = await first.click(button)
        assert 'Hello Colons' in clicked['text']
        assert name not in [item['ref'] for item in clicked['controls']]
        with pytest.raises(ValueError, match='expired'):
            await first.fill(name, 'stale')
        assert 'Hello Colons' in (await first.wait('Hello Colons'))['text']
        context = first.context
        await first.context.add_cookies([{'name': 'private', 'value': 'first-only', 'url': local_page}])
        await first.open(local_page)
        assert first.context is context
        assert len(first.pages) == 1
        await second.open(local_page)
        assert first.engine.browser is second.engine.browser
        assert not await second.context.cookies()
        saved = await first.screenshot()
        assert saved['bytes'] > 100
        from pathlib import Path
        assert Path(saved['path']).is_relative_to(tmp_path)
        assert Path(saved['path']).read_bytes().startswith(b'\x89PNG')
        await first.close()
        assert second.engine.browser.is_connected()
        assert (await second.snapshot())['title'] == 'Browser test'
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_browser_url_validation_and_permissions(tmp_path):
    browser = BrowserTools(str(tmp_path))
    for url in ['file:///etc/passwd', 'javascript:alert(1)', 'https://user:secret@example.com']:
        with pytest.raises(ValueError):
            await browser.open(url)
    registry = ToolRegistry()
    browser.register(registry)
    result = await ToolManager(registry).execute('browser_click', {'ref': 'some-ref'})
    assert result.status.value == 'denied'
    assert not browser.status()['running']


@pytest.mark.asyncio
async def test_shutdown_handles_an_already_closed_browser(tmp_path, local_page):
    pytest.importorskip('playwright')
    if not os.environ.get('COLONS_BROWSER_EXECUTABLE'):
        pytest.skip('Set COLONS_BROWSER_EXECUTABLE for the Chromium integration')
    browser = BrowserTools(str(tmp_path))
    await browser.open(local_page)
    await browser.engine.browser.close()
    await browser.close()
    assert not browser.status()['running']
    assert browser.engine.browser is None
    assert browser.engine.driver is None
