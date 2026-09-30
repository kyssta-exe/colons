"""Small inline chat attachments, validated before reaching a provider or store."""
import base64
import binascii
from typing import Dict, List, Optional

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 100_000
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


def validate_attachments(attachments: Optional[List[Dict]]) -> List[Dict]:
    if not attachments:
        return []
    if not isinstance(attachments, list) or len(attachments) > 3:
        raise ValueError('Attach up to three files per message')
    validated = []
    for item in attachments:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            raise ValueError('Invalid attachment')
        name = item['name'][:200]
        mime = item.get('mime_type', 'text/plain')
        if not isinstance(mime, str):
            raise ValueError('Invalid attachment media type')
        if mime in IMAGE_TYPES:
            url = item.get('data_url', '')
            prefix = f'data:{mime};base64,'
            if not isinstance(url, str) or not url.startswith(prefix) or len(url) > MAX_BYTES * 4 // 3 + 128:
                raise ValueError('Invalid image or image exceeds 2 MB')
            try:
                raw = base64.b64decode(url[len(prefix):], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError('Invalid image encoding') from exc
            signatures = {
                'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
                'image/gif': raw.startswith((b'GIF87a', b'GIF89a')),
                'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP',
            }
            if not signatures[mime] or len(raw) > MAX_BYTES:
                raise ValueError('Invalid image contents')
            validated.append({'name': name, 'mime_type': mime, 'data_url': url})
        else:
            text = item.get('text')
            if not isinstance(text, str) or len(text) > MAX_TEXT or '\x00' in text:
                raise ValueError('Text attachments must contain at most 100,000 characters')
            validated.append({'name': name, 'mime_type': 'text/plain', 'text': text})
    return validated


def attachment_content(content: str, attachments: List[Dict]):
    text = content
    images = []
    for item in attachments:
        if 'text' in item:
            text += f"\n\nAttached file: {item['name']}\n<file_content>\n{item['text']}\n</file_content>"
        else:
            images.append({'type': 'image_url', 'image_url': {'url': item['data_url']}})
    if images:
        return [{'type': 'text', 'text': text or 'Please review the attached image.'}, *images]
    return text
