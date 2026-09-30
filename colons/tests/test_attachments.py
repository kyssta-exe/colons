import base64

import pytest
from colons_core.agents.attachments import validate_attachments
from colons_core.agents.session_store import SessionMeta, SessionStore
from colons_core.providers.anthropic import AnthropicAdapter
from colons_core.providers.base import Message, ProviderAdapter
from colons_core.providers.google import GeminiAdapter
from colons_core.providers.ollama import OllamaAdapter

PNG = base64.b64encode(b'\x89PNG\r\n\x1a\nimage-data').decode()
IMAGE = {'name': 'chart.png', 'mime_type': 'image/png', 'data_url': f'data:image/png;base64,{PNG}'}
TEXT = {'name': 'notes.txt', 'mime_type': 'text/plain', 'text': 'Revenue grew by 20%.'}


def test_text_and_images_persist_and_translate(tmp_path):
    attachments = validate_attachments([TEXT, IMAGE])
    message = Message(role='user', content='Analyze these', attachments=attachments)
    store = SessionStore(str(tmp_path / 'sessions.db'))
    store.save(SessionMeta(id='s'))
    store.append_message('s', message)
    restored = store.messages('s')[0]
    assert restored.to_dict()['attachments'] == attachments
    assert restored.content == 'Analyze these'
    normalized = ProviderAdapter.normalize_messages([restored])[0]
    assert 'Revenue grew by 20%' in normalized['content'][0]['text']
    assert normalized['content'][1]['image_url']['url'] == IMAGE['data_url']
    _, anthropic = AnthropicAdapter(api_key='test')._split_system([restored])
    assert anthropic[0]['content'][1]['source']['data'] == PNG
    _, gemini = GeminiAdapter(api_key='test')._to_gemini([restored])
    assert gemini[0]['parts'][1]['inlineData']['data'] == PNG
    payload = OllamaAdapter()._build_payload([restored], 'vision', 0.7, None, False, None)
    assert payload['messages'][0]['images'] == [PNG]
    assert 'Revenue grew by 20%' in payload['messages'][0]['content']


@pytest.mark.parametrize('attachments', [
    [TEXT] * 4, [{'name': 'bad.png', 'mime_type': 'image/png', 'data_url': 'javascript:alert(1)'}],
    [{'name': 'bad.png', 'mime_type': 'image/png', 'data_url': 'data:image/png;base64,bm90LWFuLWltYWdl'}],
    [{'name': 'large.txt', 'text': 'x' * 100001}], [{'name': 'binary.txt', 'text': '\x00'}],
    [{'name': 'bad.txt'}], [123],
])
def test_invalid_attachments_rejected(attachments):
    with pytest.raises(ValueError):
        validate_attachments(attachments)
