import argparse
import json
import os
import tempfile
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description='Browser UI smoke checks against a running development server. Chat events are scripted in the browser; native browser operations use the real API.')
parser.add_argument('--url', default='http://localhost:8000')
parser.add_argument('--executable', default=os.environ.get('COLONS_BROWSER_EXECUTABLE'))
parser.add_argument('--output', default=tempfile.mkdtemp(prefix='colons-ui-'))
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<title>Colons browser check</title><h1>Native browser works</h1><label>Name <input></label><button>Continue</button>')
    def log_message(self, *args): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
 with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=args.executable or None)
    page=browser.new_page(viewport={'width':1600,'height':1000})
    errors=[];payloads=[];sockets=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def socket_handler(socket):
        sockets.append(socket)
        def receive(raw):
            event=json.loads(raw)
            payloads.append(event)
            if event['type']=='chat':
                socket.send(json.dumps({'type':'start','request_id':'smoke','session_id':'smoke','timestamp':0}))
                socket.send(json.dumps({'type':'delta','content':'I have your files.','timestamp':0}))
                if 'approval' in event['message']:
                    socket.send(json.dumps({'type':'tool_call','tool':'browser_click','tool_call_id':'t1','arguments':{'ref':'page-1-0'},'timestamp':0}))
                    socket.send(json.dumps({'type':'approval_required','approval_id':'approve-1','tool':'browser_click','arguments':{'ref':'page-1-0'},'reason':'Click requires approval','timestamp':0}))
                elif 'keep going' not in event['message']:
                    socket.send(json.dumps({'type':'done','content':'I have your files.','request_id':'smoke','session_id':'smoke','timestamp':0}))
            elif event['type']=='approval':
                socket.send(json.dumps({'type':'tool_result','tool':'browser_click','tool_call_id':'t1','success':True,'output':'clicked','timestamp':0}))
                socket.send(json.dumps({'type':'done','content':'Approved browser action completed.','request_id':'smoke','session_id':'smoke','timestamp':0}))
        socket.on_message(receive)
    page.route_web_socket('**/ws/chat',socket_handler)
    page.goto(args.url)
    expect(page.get_by_role('heading',name='Dashboard',exact=True)).to_be_visible()
    page.wait_for_timeout(500)
    page.screenshot(path=str(output / 'dashboard.png'))
    page.get_by_role('button',name='browser',exact=True).click()
    page.get_by_role('textbox',name='Browser URL').fill(f'http://127.0.0.1:{server.server_port}')
    page.get_by_role('button',name='Open',exact=True).click()
    expect(page.get_by_role('heading',name='Colons browser check')).to_be_visible(timeout=20000)
    page.get_by_role('button',name='Capture',exact=True).click()
    expect(page.get_by_alt_text('Native browser page capture')).to_be_visible(timeout=10000)
    page.screenshot(path=str(output / 'browser.png'))
    page.get_by_role('button',name='Close',exact=True).click()
    page.get_by_role('button',name='New chat',exact=True).click()
    expect(page.get_by_role('heading',name='What can I help you think through today?')).to_be_visible()
    page.screenshot(path=str(output / 'chat.png'))
    page.get_by_label('Attach files',exact=True).set_input_files([
      {'name':'notes.txt','mimeType':'text/plain','buffer':b'Important project context'},
      {'name':'chat.png','mimeType':'image/png','buffer':(output / 'chat.png').read_bytes()}])
    expect(page.get_by_role('button',name='Remove notes.txt',exact=True)).to_be_visible()
    page.get_by_role('textbox',name='Message Colons').fill('Review my files')
    page.screenshot(path=str(output / 'attachments.png'))
    page.get_by_role('button',name='Send message').click()
    expect(page.get_by_role('button',name='Send message')).to_be_visible()
    page.wait_for_timeout(250)
    assert len(payloads[0]['attachments'])==2
    assert payloads[0]['attachments'][0]['text']=='Important project context'
    assert payloads[0]['attachments'][1]['data_url'].startswith('data:image/png;base64,')
    page.get_by_role('button',name='New chat',exact=True).click()
    page.get_by_role('textbox',name='Message Colons').fill('keep going')
    page.get_by_role('button',name='Send message').click()
    expect(page.get_by_role('button',name='Stop response')).to_be_visible()
    page.get_by_role('button',name='New chat',exact=True).click()
    expect(page.get_by_role('heading',name='What can I help you think through today?')).to_be_visible()
    page.get_by_role('textbox',name='Message Colons').fill('approval please')
    page.get_by_role('button',name='Send message').click()
    page.get_by_role('button',name='Allow once',exact=True).click()
    expect(page.get_by_text('Approved browser action completed.',exact=True)).to_be_visible()
    assert any(x['type']=='approval' and x['approved'] for x in payloads)
    page.screenshot(path=str(output / 'conversation.png'))
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(350)
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    expect(page.get_by_role('button',name='Toggle navigation')).to_be_visible()
    page.get_by_role('button',name='Toggle navigation').click()
    page.get_by_role('button',name='New chat',exact=True).click()
    page.wait_for_timeout(350)
    page.screenshot(path=str(output / 'mobile.png'))
    assert not errors, errors
    print('PASS: dashboard, native browser navigation/capture, text+image attachments, chat switching during streaming, approvals, mobile navigation, no overflow or page errors.')
    print(f'Screenshots: {output}')
    browser.close()
finally:
 server.shutdown();server.server_close();thread.join()
