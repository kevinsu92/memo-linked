"""도메인 헬퍼 단위 테스트. DB 없이 순수 함수만 확인한다."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson.objectid import ObjectId

from memo.models import (
    build_query,
    clamp_int,
    parse_id,
    parse_tags,
    serialize,
    validate_memo,
)

LIMITS = {
    'MAX_TITLE': 100,
    'MAX_CONTENT': 2000,
    'MAX_TAGS': 5,
    'MAX_TAG_LEN': 20,
}


# ------------------------------------------------------------------ parse_id


@pytest.mark.parametrize('raw', ['', 'abc', None, '0' * 23, 123, [], {}])
def test_parse_id_rejects_bad_input(raw):
    assert parse_id(raw) is None


def test_parse_id_accepts_valid():
    oid = ObjectId()
    assert parse_id(str(oid)) == oid


# ---------------------------------------------------------------- parse_tags


@pytest.mark.parametrize(
    'raw,expected',
    [
        ('', []),
        (None, []),
        (',,,', []),
        ('python', ['python']),
        (' Python , PYTHON ', ['python']),
        ('a,b,c', ['a', 'b', 'c']),
        (['A', 'b', 'a'], ['a', 'b']),
        ('  띄어쓰기 , 태그  ', ['띄어쓰기', '태그']),
    ],
)
def test_parse_tags(raw, expected):
    assert parse_tags(raw) == expected


# -------------------------------------------------------------- validate_memo


@pytest.mark.parametrize(
    'title,content,tags',
    [
        ('', '내용', []),
        ('제목', '', []),
        ('가' * 101, '내용', []),
        ('제목', '나' * 2001, []),
        ('제목', '내용', ['a', 'b', 'c', 'd', 'e', 'f']),
        ('제목', '내용', ['x' * 21]),
    ],
)
def test_validate_memo_rejects(title, content, tags):
    assert validate_memo(title, content, tags, LIMITS) is not None


def test_validate_memo_accepts_boundary_values():
    assert validate_memo('가' * 100, '나' * 2000, ['x' * 20] * 5, LIMITS) is None


# ----------------------------------------------------------------- clamp_int


@pytest.mark.parametrize(
    'raw,expected',
    [
        (None, 20),
        ('', 20),
        ('abc', 20),
        ('1e5', 20),
        ('-3', 1),
        ('0', 1),
        ('5', 5),
        ('99999', 100),
    ],
)
def test_clamp_int(raw, expected):
    assert clamp_int(raw, 20, 1, 100) == expected


# --------------------------------------------------------------- build_query


def test_build_query_empty():
    assert build_query('', '') == {'deleted_at': None}


def test_build_query_escapes_regex_metacharacters():
    query = build_query('.*', '')
    text_match = query['$and'][0]['$or']
    assert text_match[0]['title']['$regex'] == r'\.\*'


def test_build_query_lowercases_tag():
    assert build_query('', ' STUDY ')['tags'] == 'study'


def test_build_query_combines_keyword_and_tag():
    query = build_query('파이썬', 'study')
    assert '$and' in query and query['tags'] == 'study'


def test_build_query_excludes_locked_memos_from_search():
    """잠긴 메모가 검색되면 내용을 맞혀 볼 수 있다."""
    conditions = build_query('키워드', '')['$and']
    assert any('open_at' in str(part) for part in conditions)


def test_build_query_without_keyword_does_not_filter_locked():
    """검색어가 없으면 잠긴 메모도 목록에는 나와야 한다."""
    assert '$and' not in build_query('', 'study')


# ----------------------------------------------------------------- serialize


def test_serialize_converts_datetime_to_iso():
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    out = serialize(
        {
            '_id': ObjectId(),
            'title': 't',
            'content': 'c',
            'tags': ['a'],
            'like': 2,
            'pinned': True,
            'created_at': created,
            'updated_at': None,
        }
    )
    assert out['created_at'] == created.isoformat()
    assert out['updated_at'] is None
    assert out['pinned'] is True


def test_serialize_falls_back_to_objectid_time():
    oid = ObjectId()
    out = serialize({'_id': oid, 'title': 't', 'content': 'c'})
    assert out['created_at'] == oid.generation_time.isoformat()
    assert out['tags'] == []
    assert out['like'] == 0


# ------------------------------------------------------------ 소유 토큰


def test_new_owner_token_is_unique_and_long():
    from memo.models import new_owner_token

    tokens = {new_owner_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 30 for t in tokens)


def test_hash_token_is_stable_and_hides_input():
    from memo.models import hash_token

    digest = hash_token('secret')
    assert digest == hash_token('secret')
    assert 'secret' not in digest
    assert len(digest) == 64


@pytest.mark.parametrize(
    'token,stored,expected',
    [
        ('secret', None, False),
        (None, 'abc', False),
        ('', '', False),
        ('wrong', None, False),
    ],
)
def test_token_matches_rejects_missing_values(token, stored, expected):
    from memo.models import token_matches

    assert token_matches(token, stored) is expected


def test_token_matches_accepts_correct_token():
    from memo.models import hash_token, token_matches

    assert token_matches('secret', hash_token('secret')) is True
    assert token_matches('other', hash_token('secret')) is False


def test_build_query_always_excludes_deleted():
    assert build_query('', '')['deleted_at'] is None
    assert build_query('키워드', 'tag')['deleted_at'] is None


def test_serialize_reports_owner_without_leaking_hash():
    from memo.models import serialize as ser

    out = ser({'_id': ObjectId(), 'title': 't', 'content': 'c', 'owner_hash': 'a' * 64})
    assert out['has_owner'] is True
    assert 'owner_hash' not in out

    out = ser({'_id': ObjectId(), 'title': 't', 'content': 'c'})
    assert out['has_owner'] is False


# ------------------------------------------------- 레이트 리밋 저장소 주소


@pytest.mark.parametrize(
    'mongo_uri,expected',
    [
        ('mongodb://localhost:27017', 'mongodb://localhost:27017/memo'),
        ('mongodb://localhost:27017/memo', 'mongodb://localhost:27017/memo'),
        (
            'mongodb://user:pw@127.0.0.1:27017/memo?authSource=memo',
            'mongodb://user:pw@127.0.0.1:27017/memo?authSource=memo',
        ),
    ],
)
def test_storage_uri_derives_from_mongo_uri(monkeypatch, mongo_uri, expected):
    """자격 증명을 두 곳에 적지 않도록 MONGO_URI 에서 파생시킨다."""
    from memo.config import storage_uri

    monkeypatch.delenv('RATELIMIT_STORAGE_URI', raising=False)
    monkeypatch.delenv('DB_NAME', raising=False)
    monkeypatch.setenv('MONGO_URI', mongo_uri)
    assert storage_uri() == expected


def test_storage_uri_respects_explicit_value(monkeypatch):
    from memo.config import storage_uri

    monkeypatch.setenv('RATELIMIT_STORAGE_URI', 'redis://example:6379')
    assert storage_uri() == 'redis://example:6379'


def test_storage_uri_falls_back_without_mongo(monkeypatch):
    from memo.config import storage_uri

    monkeypatch.delenv('RATELIMIT_STORAGE_URI', raising=False)
    monkeypatch.setenv('MONGO_URI', '')
    assert storage_uri() == 'memory://'
