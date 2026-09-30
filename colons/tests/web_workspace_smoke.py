"""Settings/workspace checks against a running server, including a harmless shell command."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8137')
parser.add_argument('--executable', default=os.environ.get('COLONS_BROWSER_EXECUTABLE'))
parser.add_argument('--output', default=tempfile.mkdtemp(prefix='colons-workspace-'))
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.executable or None)
    page = browser.new_page(viewport={'width': 1600, 'height': 1000})
    errors = []; switches = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(args.url)
    page.get_by_role('button', name='Settings', exact=True).click()
    dialog = page.get_by_role('dialog', name='Settings', exact=True)
    expect(dialog.get_by_label('Provider', exact=True)).to_be_visible()
    expect(dialog.get_by_role('button', name='Apply changes')).to_be_enabled()
    page.screenshot(path=str(output / 'settings-models.png'))
    def failed_switch(route):
        switches.append(route.request.post_data_json)
        route.fulfill(status=400, content_type='application/json', body=json.dumps({'detail': 'Test provider is unavailable'}))
    page.route('**/api/providers/switch', failed_switch)
    provider = dialog.get_by_label('Provider', exact=True)
    names = provider.locator('option').evaluate_all('(options) => options.map(o => o.value)')
    alternative = next(n for n in names if n != provider.input_value())
    provider.select_option(alternative)
    assert not switches, 'Selecting a provider must not apply it'
    dialog.get_by_role('button', name='Apply changes').click()
    expect(dialog.get_by_role('alert')).to_contain_text('Test provider is unavailable')
    assert switches[0]['name'] == alternative and switches[0]['model']
    page.unroute('**/api/providers/switch', failed_switch)
    for name in ['Appearance', 'Voice', 'Schedules', 'Messaging', 'Server access', 'Usage']:
        dialog.get_by_role('button', name=name, exact=True).click()
        expect(dialog.get_by_role('heading', level=3)).to_be_visible()
    dialog.get_by_role('button', name='Messaging', exact=True).click()
    expect(dialog.get_by_role('heading', name='Telegram')).to_be_visible()
    telegram = dialog.get_by_role('region', name='Telegram messaging')
    telegram.get_by_role('button', name='Set up', exact=True).click()
    expect(telegram.get_by_text('Setup instructions', exact=True)).to_be_visible()
    telegram.get_by_role('button', name='Save & connect').click()
    expect(telegram.get_by_role('alert')).to_contain_text('bot token is required')
    saves = []
    def messaging_save(route):
        saves.append(route.request.post_data_json)
        route.fulfill(status=400, content_type='application/json', body=json.dumps({'detail': 'Connection test error'}))
    page.route('**/api/messaging/config/telegram', messaging_save)
    token = telegram.get_by_label('Telegram bot token', exact=True)
    token.fill('ui-test-token')
    assert token.get_attribute('type') == 'password'
    telegram.get_by_role('button', name='Save & connect').click()
    expect(telegram.get_by_role('alert')).to_contain_text('Connection test error')
    assert saves[0]['bot_token'] == 'ui-test-token' and saves[0]['enabled']
    page.unroute('**/api/messaging/config/telegram', messaging_save)
    page.screenshot(path=str(output / 'settings-messaging.png'))
    circles = page.evaluate("""async () => {
      const text = await (await fetch(document.querySelector('link[rel=icon]').href)).text();
      const svg = new DOMParser().parseFromString(text, 'image/svg+xml');
      return [...svg.querySelectorAll('circle')].map(c => [c.getAttribute('cx'), c.getAttribute('cy')]);
    }""")
    assert len(circles) == 2 and circles[0][0] == circles[1][0] and circles[0][1] != circles[1][1]
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    page.get_by_role('button', name='Toggle navigation').click()
    expect(page.get_by_role('navigation', name='Main navigation')).not_to_be_visible()
    page.reload()
    expect(page.get_by_role('navigation', name='Main navigation')).not_to_be_visible()
    page.get_by_role('button', name='Toggle navigation').click()
    expect(page.get_by_role('navigation', name='Main navigation')).to_be_visible()
    page.get_by_role('button', name='New chat', exact=True).click()
    expect(page.get_by_role('complementary', name='Conversation details')).to_be_visible()
    page.get_by_role('button', name='Collapse conversation panel').click()
    expect(page.get_by_role('complementary', name='Conversation details')).not_to_be_visible()
    page.get_by_role('button', name='Toggle conversation panel').click()
    expect(page.get_by_role('complementary', name='Conversation details')).to_be_visible()
    page.get_by_role('button', name='Toggle terminal').click()
    page.get_by_role('textbox', name='Terminal command', exact=True).fill('printf colons-terminal-ok')
    page.get_by_role('button', name='Run terminal command').click()
    expect(page.get_by_role('log').locator('pre')).to_have_text('colons-terminal-ok')
    expect(page.get_by_role('textbox', name='Terminal command', exact=True)).to_be_enabled()
    page.screenshot(path=str(output / 'terminal.png'))
    page.get_by_role('textbox', name='Terminal command', exact=True).press('ArrowUp')
    expect(page.get_by_role('textbox', name='Terminal command', exact=True)).to_have_value('printf colons-terminal-ok')
    page.get_by_role('button', name='Close terminal').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.get_by_role('complementary', name='Conversation details')).not_to_be_visible()
    page.get_by_role('button', name='Toggle conversation panel').click()
    expect(page.get_by_role('complementary', name='Conversation details')).to_be_visible()
    page.get_by_role('button', name='Collapse conversation panel').click()
    page.get_by_role('button', name='Toggle navigation').click()
    page.get_by_role('button', name='Settings', exact=True).click()
    expect(dialog.get_by_label('Provider', exact=True)).to_be_visible()
    page.screenshot(path=str(output / 'settings-mobile.png'))
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.get_by_role('button', name='Close settings').click()
    assert not errors, errors
    print('PASS: settings sections, staged model changes/errors, persisted left collapse, right collapse/mobile overlay, real terminal output/history, mobile settings, no page errors.')
    print(f'Screenshots: {output}')
    browser.close()
