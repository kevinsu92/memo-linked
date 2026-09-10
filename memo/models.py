"""메모 도메인 로직. 직렬화, 검증, 입력 파싱, 소유권."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId

from memo.links import extract_links, normalize_title

Memo = dict[str, Any]

# 소유 토큰 길이. 24바이트 -> base64url 32자.
OWNER_TOKEN_BYTES = 24


def parse_id(raw_id: Any) -> ObjectId | None:
    """문자열 id 를 ObjectId 로 변환한다. 형식이 틀리면 None.

    ObjectId(None) 은 예외 대신 새 id 를 만들어 내므로 문자열인지 먼저 확인한다.
    id 가 빠진 요청이 조용히 엉뚱한 id 로 바뀌는 것을 막는다.
    """
    if not isinstance(raw_id, str):
        return None
    try:
        return ObjectId(raw_id)
    except (InvalidId, TypeError):
        return None


def new_owner_token() -> str:
    """새 소유 토큰. 평문은 저장하지 않고 응답으로 한 번만 돌려준다."""
    return secrets.token_urlsafe(OWNER_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """토큰을 SHA-256 으로 해싱한다. DB 가 새어도 토큰 자체는 복원되지 않는다."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def token_matches(token: str | None, stored_hash: str | None) -> bool:
    """토큰이 저장된 해시와 일치하는지. 타이밍 공격에 안전한 비교를 쓴다."""
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def parse_tags(raw: str | list[str] | None) -> list[str]:
    """태그 입력을 정규화한다.

    쉼표로 구분된 문자열이나 리스트를 받아 공백 제거, 소문자 변환,
    중복 제거를 거친 리스트로 만든다. 입력 순서는 유지한다.
    """
    if not raw:
        return []
    pieces = raw if isinstance(raw, list) else str(raw).split(',')
    tags: list[str] = []
    for piece in pieces:
        tag = str(piece).strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def validate_memo(title: str, content: str, tags: list[str], limits: dict[str, Any]) -> str | None:
    """메모 입력을 검증한다. 문제가 있으면 사용자에게 보여줄 메시지, 없으면 None."""
    if not title or not content:
        return '제목과 내용을 모두 입력하세요.'
    if len(title) > limits['MAX_TITLE']:
        return f'제목은 {limits["MAX_TITLE"]}자 이하로 입력하세요.'
    if len(content) > limits['MAX_CONTENT']:
        return f'내용은 {limits["MAX_CONTENT"]}자 이하로 입력하세요.'
    if len(tags) > limits['MAX_TAGS']:
        return f'태그는 최대 {limits["MAX_TAGS"]}개까지 사용할 수 있습니다.'
    for tag in tags:
        if len(tag) > limits['MAX_TAG_LEN']:
            return f'태그는 {limits["MAX_TAG_LEN"]}자 이하로 입력하세요: {tag}'
    return None


def is_locked(memo: Memo, now: datetime | None = None) -> bool:
    """아직 열릴 때가 되지 않은 타임캡슐 메모인지."""
    open_at = memo.get('open_at')
    if not isinstance(open_at, datetime):
        return False
    current = now or datetime.now(open_at.tzinfo or timezone.utc)
    return open_at > current


def serialize(memo: Memo, now: datetime | None = None) -> dict[str, Any]:
    """MongoDB 문서를 JSON 응답용 dict 로 바꾼다.

    owner_hash 는 절대 내보내지 않는다. 대신 소유자가 지정된 메모인지만 알린다.
    아직 열릴 때가 안 된 타임캡슐 메모는 내용을 통째로 뺀다. 서버가 내보내지
    않아야 브라우저 개발자 도구로도 미리 볼 수 없다.
    """
    created = memo.get('created_at') or memo['_id'].generation_time
    locked = is_locked(memo, now)
    return {
        'id': str(memo['_id']),
        'title': memo.get('title', ''),
        'content': '' if locked else memo.get('content', ''),
        'tags': memo.get('tags', []),
        'like': memo.get('like', 0),
        'pinned': bool(memo.get('pinned', False)),
        'has_owner': bool(memo.get('owner_hash')),
        'links': memo.get('link_titles', []),
        'encrypted': bool(memo.get('encrypted')),
        'locked': locked,
        'open_at': _iso(memo.get('open_at')),
        'shared': bool(memo.get('share_hash')),
        'burn_after_read': bool(memo.get('burn_after_read')),
        'revisions': memo.get('revision_count', 0),
        'created_at': _iso(created),
        'updated_at': _iso(memo.get('updated_at')),
    }


def _iso(value: Any) -> str | None:
    """datetime 이면 ISO 문자열로, 이미 문자열이면 그대로, 없으면 None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def memo_fields(title: str, content: str, tags: list[str]) -> dict[str, Any]:
    """제목·내용에서 파생되는 필드를 한곳에서 계산한다.

    저장과 수정이 같은 규칙을 쓰도록 묶어 둔다.
    """
    return {
        'title': title,
        'content': content,
        'tags': tags,
        'title_key': normalize_title(title),
        'link_titles': extract_links(content),
    }


def build_query(keyword: str, tag: str, now: datetime | None = None) -> dict[str, Any]:
    """검색어와 태그로 MongoDB 조회 조건을 만든다.

    검색어는 re.escape 로 감싸 정규식 메타문자를 문자 그대로 취급한다.
    삭제 표시된 메모는 항상 제외한다.

    아직 열리지 않은 타임캡슐 메모는 검색 대상에서 뺀다. 내용을 감춰 놓고
    검색은 되게 두면 "이 낱말이 들어 있는가" 를 맞혀 보는 통로가 된다.
    """
    query: dict[str, Any] = {'deleted_at': None}
    if keyword:
        pattern = re.escape(keyword)
        current = now or datetime.now(timezone.utc)
        query['$and'] = [
            {
                '$or': [
                    {'title': {'$regex': pattern, '$options': 'i'}},
                    {'content': {'$regex': pattern, '$options': 'i'}},
                ]
            },
            {
                '$or': [
                    {'open_at': None},
                    {'open_at': {'$exists': False}},
                    {'open_at': {'$lte': current}},
                ]
            },
        ]
    if tag:
        query['tags'] = tag.strip().lower()
    return query


def clamp_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    """쿼리 파라미터를 지정한 범위 안의 정수로 만든다."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))
