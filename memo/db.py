"""MongoDB 연결, 인덱스, 마이그레이션."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from flask import current_app
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.database import Database
from pymongo.errors import OperationFailure

logger = logging.getLogger('memo.db')

# 삭제 표시된 메모를 실제로 지우기까지의 유예 기간.
DELETED_RETENTION_DAYS = 30

# MongoDB: 같은 키에 다른 이름의 인덱스가 이미 있을 때의 오류 코드
INDEX_OPTIONS_CONFLICT = 85

# 정렬 옵션과 그 정렬을 커버하는 인덱스를 한곳에서 관리한다.
# 여기에 항목을 추가하면 인덱스도 함께 만들어지므로 한쪽만 빠뜨릴 수 없다.
SORTS: dict[str, list[tuple[str, int]]] = {
    'newest': [('pinned', DESCENDING), ('created_at', DESCENDING)],
    'oldest': [('pinned', DESCENDING), ('created_at', ASCENDING)],
    'like': [('pinned', DESCENDING), ('like', DESCENDING), ('created_at', DESCENDING)],
}

# 태그로 거른 뒤 같은 정렬을 적용하는 경로를 위한 인덱스.
# 태그 인덱스에 정렬 키가 빠져 있으면 MongoDB 가 문서를 찾은 뒤 메모리에서
# 다시 정렬한다(blocking SORT). 정렬별로 접두사가 맞는 인덱스를 따로 둔다.
# 이름은 위치가 아니라 의미로 짓는다. 목록 중간에 항목을 넣어도 기존 인덱스
# 이름이 밀리지 않는다.
TAG_INDEXES: dict[str, list[tuple[str, int]]] = {
    f'tag_{name}': [('tags', ASCENDING), *keys] for name, keys in SORTS.items()
}

# 위키 링크와 공유 링크 조회용 인덱스.
FEATURE_INDEXES: dict[str, list[tuple[str, int]]] = {
    'title_key': [('title_key', ASCENDING)],
    'link_titles': [('link_titles', ASCENDING)],
    'share_hash': [('share_hash', ASCENDING)],
}


def make_client(uri: str) -> MongoClient:
    """MongoDB 클라이언트를 만든다. tz_aware 로 시각을 UTC 인식 값으로 받는다."""
    return MongoClient(
        uri,
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=3000,
        socketTimeoutMS=10000,
    )


def get_db() -> Database:
    """현재 요청 컨텍스트의 데이터베이스."""
    return current_app.extensions['mongo'][current_app.config['DB_NAME']]


def now() -> datetime:
    """현재 시각 (UTC)."""
    return datetime.now(timezone.utc)


def ensure_indexes(db: Database) -> list[str]:
    """정렬·필터 패턴에 맞는 인덱스를 만든다.

    같은 키 조합이 다른 이름으로 이미 있으면 (예전 배포에서 기본 이름으로
    만들어진 경우) 새 이름으로 먼저 만든 뒤 옛 것을 지운다. 이 순서라야
    해당 정렬이 인덱스 없이 노출되는 구간이 생기지 않는다.
    """
    wanted: list[tuple[str, list[tuple[str, int]]]] = [
        (f'sort_{name}', keys) for name, keys in SORTS.items()
    ]
    wanted += list(TAG_INDEXES.items())
    wanted += list(FEATURE_INDEXES.items())

    created = []
    for name, keys in wanted:
        created.append(_create_index(db, name, keys))

    # 삭제 표시된 메모는 유예 기간이 지나면 MongoDB 가 알아서 지운다.
    db.memos.create_index(
        [('deleted_at', ASCENDING)],
        name='deleted_ttl',
        expireAfterSeconds=DELETED_RETENTION_DAYS * 24 * 3600,
    )
    # 수정 이력은 메모별로 최근 것만 본다.
    db.revisions.create_index([('memo_id', ASCENDING), ('saved_at', DESCENDING)], name='rev_memo')

    return created


def _create_index(db: Database, name: str, keys: list[tuple[str, int]]) -> str:
    """인덱스를 만든다.

    같은 키 조합이 다른 이름으로 이미 있으면 MongoDB 가 충돌로 거부한다.
    그때만 옛 인덱스를 지우고 다시 만든다. 예전 배포가 기본 이름으로 만들어
    둔 인덱스를 정해진 이름으로 옮기기 위한 처리다.
    """
    try:
        return db.memos.create_index(keys, name=name)
    except OperationFailure as exc:
        if exc.code != INDEX_OPTIONS_CONFLICT:
            raise
        for other_name, info in db.memos.index_information().items():
            if other_name == '_id_' or other_name == name:
                continue
            if [tuple(pair) for pair in info.get('key', [])] == keys:
                logger.info('인덱스 이름 정리: %s -> %s', other_name, name)
                db.memos.drop_index(other_name)
                break
        return db.memos.create_index(keys, name=name)


def backfill(db: Database, batch_size: int = 500) -> int:
    """예전 문서를 현재 스키마로 보정한다.

    - created_at 이 없으면 ObjectId 의 생성 시각으로 채운다.
    - created_at 이 문자열이면 datetime 으로 바꾼다.
    - tags / pinned / like / deleted_at 기본값을 채운다.

    배포 시 1회만 실행한다 (flask init-db). 문서를 한꺼번에 메모리에 올리지
    않도록 배치 단위로 끊어 쓴다.
    """
    query = {
        '$or': [
            {'created_at': {'$exists': False}},
            {'created_at': {'$type': 'string'}},
            {'tags': {'$exists': False}},
            {'pinned': {'$exists': False}},
            {'like': {'$exists': False}},
            {'deleted_at': {'$exists': False}},
        ]
    }

    total = 0
    ops: list[UpdateOne] = []
    for memo in db.memos.find(query).batch_size(batch_size):
        patch: dict[str, Any] = {}
        created = memo.get('created_at')
        if created is None:
            patch['created_at'] = memo['_id'].generation_time
        elif isinstance(created, str):
            patch['created_at'] = _parse_iso(created, memo['_id'])
        if 'tags' not in memo:
            patch['tags'] = []
        if 'pinned' not in memo:
            patch['pinned'] = False
        if 'like' not in memo:
            patch['like'] = 0
        if 'deleted_at' not in memo:
            patch['deleted_at'] = None
        if 'title_key' not in memo:
            # 위키 링크 대조에 쓰는 파생 필드. 늦게 도입돼 예전 문서에는 없다.
            from memo.links import extract_links, normalize_title

            patch['title_key'] = normalize_title(memo.get('title', ''))
            patch['link_titles'] = extract_links(memo.get('content', ''))
        updated = memo.get('updated_at')
        if isinstance(updated, str):
            patch['updated_at'] = _parse_iso(updated, memo['_id'])
        if patch:
            ops.append(UpdateOne({'_id': memo['_id']}, {'$set': patch}))

        if len(ops) >= batch_size:
            db.memos.bulk_write(ops, ordered=False)
            total += len(ops)
            ops = []

    if ops:
        db.memos.bulk_write(ops, ordered=False)
        total += len(ops)

    if total:
        logger.info('예전 메모 %d건 보정 완료', total)
    return total


def _parse_iso(value: str, oid: Any) -> datetime:
    """ISO 문자열을 datetime 으로. 실패하면 ObjectId 시각으로 대체한다."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return oid.generation_time
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
