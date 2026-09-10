"""테스트 공통 설정.

실제 MongoDB 의 별도 테스트 DB 를 쓴다. 설정은 팩토리에 직접 주입하므로
셸 환경 변수(DB_NAME 등)의 영향을 받지 않는다.
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memo import create_app  # noqa: E402

TEST_DB_PREFIX = 'memo_test_'
TEST_MONGO_URI = os.environ.get('TEST_MONGO_URI', 'mongodb://localhost:27017')
ADMIN_TOKEN = 'test-admin-token'


@pytest.fixture()
def app():
    """테스트마다 고유한 DB 를 쓰는 앱. 종료 시 그 DB 를 통째로 지운다."""
    db_name = TEST_DB_PREFIX + uuid4().hex[:8]
    application = create_app(
        {
            'MONGO_URI': TEST_MONGO_URI,
            'DB_NAME': db_name,
            'TESTING': True,
            'RATELIMIT_ENABLED': False,
            'SITE_ORIGIN': '',
            'ADMIN_TOKEN': ADMIN_TOKEN,
        }
    )

    # 안전장치: 테스트 DB 가 아니면 아무것도 하지 않고 즉시 실패한다.
    assert application.config['DB_NAME'].startswith(TEST_DB_PREFIX), (
        f'테스트가 {application.config["DB_NAME"]!r} 에 연결되었습니다. 중단합니다.'
    )

    try:
        yield application
    finally:
        application.extensions['mongo'].drop_database(db_name)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    return app.extensions['mongo'][app.config['DB_NAME']]


@pytest.fixture()
def make_memo(client):
    """메모를 만들고 id 를 돌려주는 헬퍼. 소유 토큰은 tokens 에 모아 둔다."""
    tokens: dict[str, str] = {}

    def _make(title='제목', content='내용', tags=''):
        res = client.post(
            '/memo',
            data={
                'title_give': title,
                'content_give': content,
                'tags_give': tags,
            },
        )
        assert res.status_code == 201, res.get_json()
        body = res.get_json()
        tokens[body['id']] = body['owner_token']
        return body['id']

    _make.tokens = tokens
    return _make


@pytest.fixture()
def owned(client, make_memo):
    """메모를 만들고 (id, 소유 토큰) 을 돌려준다."""

    def _make(*args, **kwargs):
        memo_id = make_memo(*args, **kwargs)
        return memo_id, make_memo.tokens[memo_id]

    return _make
