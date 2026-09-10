"""잠금 메모와 공유 링크에 관한 서버 쪽 규칙.

내용 암호화는 전적으로 브라우저에서 이루어진다. 서버는 암호문 덩어리를
그대로 보관할 뿐이고 비밀번호도 평문도 알지 못한다. 여기서는 그 덩어리가
형식에 맞는지 확인하고, 공유 토큰을 만들고 검증한다.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from memo.models import hash_token, is_locked

# 브라우저가 만드는 암호문 형식: memo1.<salt>.<iv>.<ciphertext> (모두 base64url)
CIPHER_RE = re.compile(r'^memo1\.[A-Za-z0-9_-]{10,64}\.[A-Za-z0-9_-]{10,64}\.[A-Za-z0-9_-]{4,}$')

SHARE_TOKEN_BYTES = 16


def looks_encrypted(content: str) -> bool:
    """브라우저가 만든 암호문 형식인지 확인한다."""
    return bool(CIPHER_RE.match(content or ''))


def new_share_token() -> str:
    """공유 링크용 토큰. 해시만 저장하고 평문은 한 번만 돌려준다."""
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def share_hash(token: str) -> str:
    return hash_token(token)


def public_view(memo: dict[str, Any]) -> dict[str, Any]:
    """공유 링크로 보여 줄 최소 정보. 소유 정보와 내부 필드는 뺀다.

    아직 열리지 않은 타임캡슐 메모는 공유 링크로도 내용을 내보내지 않는다.
    """
    locked = is_locked(memo)
    return {
        'title': memo.get('title', ''),
        'content': '' if locked else memo.get('content', ''),
        'tags': memo.get('tags', []),
        'encrypted': bool(memo.get('encrypted')),
        'locked': locked,
        'open_at': memo['open_at'].isoformat() if locked and memo.get('open_at') else None,
        'burn_after_read': bool(memo.get('burn_after_read')),
        'created_at': memo.get('created_at').isoformat() if memo.get('created_at') else None,
    }
