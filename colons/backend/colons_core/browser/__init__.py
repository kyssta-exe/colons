"""Warm Chromium shared by agents, with an isolated context per agent."""
import asyncio
import hashlib
import importlib.util
import logging
import os
import uuid
import weakref
from pathlib import Path
from urllib.parse import urlparse

from ..tools.base import PermissionLevel, ToolSpec

logger = logging.getLogger(__name__)
_ENGINES = weakref.WeakKeyDictionary()


class BrowserEngine:
    def __init__(self):
        self.driver = None
        self.browser = None
        self.lock = asyncio.Lock()
        self.clients = 0

    async def acquire(self):
        async with self.lock:
            if self.browser is None:
                try:
                    from playwright.async_api import async_playwright
                except ImportError as exc:
                    raise RuntimeError('Install browser support: pip install "colons[browser]" && playwright install chromium') from exc
                self.driver = await async_playwright().start()
                try:
                    self.browser = await self.driver.chromium.launch(
                        headless=True, executable_path=os.environ.get('COLONS_BROWSER_EXECUTABLE') or None,
                        handle_sigint=False, handle_sigterm=False, handle_sighup=False)
                except BaseException as exc:
                    await self.driver.stop()
                    self.driver = None
                    if "Executable doesn't exist" in str(exc):
                        raise RuntimeError('Chromium is not installed. Run: playwright install chromium') from exc
                    raise
            context = await self.browser.new_context(viewport={'width': 1440, 'height': 900},
                                                     accept_downloads=False)
            context.set_default_timeout(10000)
            self.clients += 1
            return context

    async def release(self, context):
        async with self.lock:
            try:
                await context.close()
            except Exception:
                logger.debug('Browser context was already disconnected', exc_info=True)
            self.clients = max(0, self.clients - 1)
            if self.clients == 0:
                try:
                    if self.browser and self.browser.is_connected():
                        await self.browser.close()
                except Exception:
                    logger.debug('Chromium was already disconnected', exc_info=True)
                finally:
                    try:
                        if self.driver:
                            await self.driver.stop()
                    except Exception:
                        logger.debug('Playwright driver was already disconnected', exc_info=True)
                    self.browser = self.driver = None


# Returns visible, actionable references instead of shipping entire DOMs.
SNAPSHOT_JS = r'''({prefix, limit}) => {
  document.querySelectorAll('[data-colons-ref]').forEach(el => el.removeAttribute('data-colons-ref'));
  const controls = [];
  const elements = document.querySelectorAll('a[href], button, input:not([type="hidden"]), textarea, select, [role="button"], [role="link"], [contenteditable="true"]');
  for (const el of elements) {
    if (controls.length >= 80) break;
    if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') continue;
    const ref = `${prefix}-${controls.length}`;
    el.setAttribute('data-colons-ref', ref);
    const label = el.getAttribute('aria-label') || (el.labels && Array.from(el.labels).map(x => x.innerText).join(' ')) || el.innerText || el.placeholder || el.getAttribute('title') || el.getAttribute('name') || '';
    controls.push({ref, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', name: label.trim().slice(0, 160), type: el.type || '', href: el.href || '', disabled: !!el.disabled});
  }
  return {text: (document.body?.innerText || '').slice(0, limit), controls};
}'''


class BrowserTools:
    def __init__(self, workspace=None, namespace='default'):
        self.workspace = Path(workspace or '.').resolve()
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()[:12]
        self.context = None
        self.engine = None
        self.pages = {}
        self.active_page = None
        self.revision = 0
        self.lock = asyncio.Lock()

    def status(self):
        return {'available': importlib.util.find_spec('playwright') is not None,
                'running': self.context is not None, 'active_page': self.active_page,
                'pages': [{'id': key, 'url': page.url} for key, page in self.pages.items() if not page.is_closed()]}

    async def _ensure(self):
        if self.context is None:
            loop = asyncio.get_running_loop()
            self.engine = _ENGINES.setdefault(loop, BrowserEngine())
            self.context = await self.engine.acquire()
            self.context.on('page', self._track)

    def _track(self, page):
        if page not in self.pages.values():
            self.pages[uuid.uuid4().hex[:8]] = page

    def _page(self, page_id=None):
        key = page_id or self.active_page
        page = self.pages.get(key)
        if page is None or page.is_closed():
            raise ValueError('No active page. Call browser_open first.')
        return key, page

    async def _snapshot(self, key, page, max_chars=12000):
        self.revision += 1
        result = await page.evaluate(SNAPSHOT_JS, {'prefix': f'{key}-{self.revision}',
                                                  'limit': min(max(max_chars, 500), 20000)})
        return {'page_id': key, 'url': page.url, 'title': await page.title(), **result}

    async def open(self, url: str, new_tab: bool = False):
        """Navigate with DOM-content-loaded readiness; keep Chromium and cookies warm."""
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Browser URLs must be HTTP(S) without embedded credentials')
        async with self.lock:
            await self._ensure()
            if new_tab or not self.active_page:
                if len([p for p in self.pages.values() if not p.is_closed()]) >= 6:
                    raise ValueError('Maximum six tabs per agent; close a tab first')
                page = await self.context.new_page()
                key = next(key for key, value in self.pages.items() if value is page)
                self.active_page = key
            else:
                key, page = self._page()
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            return await self._snapshot(key, page)

    async def tabs(self):
        return self.status()

    async def snapshot(self, page_id: str = '', max_chars: int = 12000):
        async with self.lock:
            key, page = self._page(page_id)
            return await self._snapshot(key, page, max_chars)

    async def _target(self, page, ref):
        if not isinstance(ref, str) or not ref.replace('-', '').isalnum():
            raise ValueError('Use an element reference returned by browser_snapshot')
        target = page.locator(f'[data-colons-ref="{ref}"]')
        if await target.count() != 1:
            raise ValueError('Element reference expired. Call browser_snapshot for fresh refs.')
        return target

    async def click(self, ref: str, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await (await self._target(page, ref)).click()
            return await self._snapshot(key, page)

    async def fill(self, ref: str, value: str, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await (await self._target(page, ref)).fill(value)
            return await self._snapshot(key, page)

    async def press(self, ref: str, key: str = 'Enter', page_id: str = ''):
        async with self.lock:
            page_key, page = self._page(page_id)
            await (await self._target(page, ref)).press(key)
            return await self._snapshot(page_key, page)

    async def select(self, ref: str, value: str, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await (await self._target(page, ref)).select_option(value=value)
            return await self._snapshot(key, page)

    async def scroll(self, pixels: int = 700, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await page.evaluate('(pixels) => window.scrollBy(0, pixels)', max(-5000, min(pixels, 5000)))
            return await self._snapshot(key, page)

    async def wait(self, text: str, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await page.get_by_text(text, exact=False).first.wait_for(state='visible', timeout=15000)
            return await self._snapshot(key, page)

    async def back(self, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await page.go_back(wait_until='domcontentloaded', timeout=30000)
            return await self._snapshot(key, page)

    async def screenshot_bytes(self, page_id: str = ''):
        async with self.lock:
            _, page = self._page(page_id)
            return await page.screenshot(type='png', animations='disabled')

    async def screenshot(self, page_id: str = ''):
        image = await self.screenshot_bytes(page_id)
        directory = self.workspace / '.colons' / 'browser' / self.namespace
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{uuid.uuid4().hex[:12]}.png'
        path.write_bytes(image)
        return {'path': str(path), 'bytes': len(image)}

    async def close_tab(self, page_id: str = ''):
        async with self.lock:
            key, page = self._page(page_id)
            await page.close()
            self.pages.pop(key)
            if self.active_page == key:
                self.active_page = next((key for key, value in self.pages.items() if not value.is_closed()), None)
            return self.status()

    async def close(self):
        async with self.lock:
            if self.context:
                await self.engine.release(self.context)
                self.context = None
            self.pages.clear()
            self.active_page = None

    def register(self, registry):
        from ..tools.base import spec_from_func
        operations = [
            ('browser_tabs', self.tabs, PermissionLevel.AUTO, 'List open browser tabs and their page IDs and URLs.'),
            ('browser_open', self.open, PermissionLevel.AUTO, 'Open a URL in the native browser. Reuses the current tab unless new_tab=true. Returns visible text and actionable element refs.'),
            ('browser_snapshot', self.snapshot, PermissionLevel.AUTO, 'Read visible page text and fresh element refs. Use refs for click, fill, and press; refs change after each snapshot.'),
            ('browser_click', self.click, PermissionLevel.ASK, 'Click a visible element using a snapshot ref. Returns updated page context.'),
            ('browser_fill', self.fill, PermissionLevel.ASK, 'Fill a form field using a snapshot ref.'),
            ('browser_press', self.press, PermissionLevel.ASK, 'Press a key on an element using a snapshot ref, e.g. Enter to submit.'),
            ('browser_select', self.select, PermissionLevel.ASK, 'Choose an option in a select field using its latest element ref.'),
            ('browser_scroll', self.scroll, PermissionLevel.AUTO, 'Scroll the page by pixels (positive down, negative up) and read updated context.'),
            ('browser_wait', self.wait, PermissionLevel.AUTO, 'Wait up to 15 seconds for visible text, useful for dynamic pages. Returns fresh context.'),
            ('browser_back', self.back, PermissionLevel.AUTO, 'Go back in the tab history and read the previous page.'),
            ('browser_screenshot', self.screenshot, PermissionLevel.AUTO, 'Save a viewport screenshot to the workspace for visual inspection.'),
            ('browser_close_tab', self.close_tab, PermissionLevel.AUTO, 'Close a browser tab and release its page resources.'),
        ]
        for name, function, permission, description in operations:
            inferred = spec_from_func(function)
            registry.register(ToolSpec(name=name, description=description, parameters=inferred.parameters,
                                       func=function, permission=permission, category='browser', timeout=40))
