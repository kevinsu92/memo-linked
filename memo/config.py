"""애플리케이션 설정. 환경 변수를 읽어 검증한 뒤 Flask config 로 넘긴다."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def log_level(raw: str) -> int:
    """로그 레벨 문자열을 검증한다. 잘못된 값이면 기동 시점에 바로 실패시킨다."""
    level = logging.getLevelName(raw.upper())
    if not isinstance(level, int):
        raise RuntimeError(f'잘못된 LOG_LEVEL 값입니다: {raw!r}')
    return level


def storage_uri() -> str:
    """레이트 리밋 카운터를 둘 곳.

    워커가 여러 개라 memory:// 로는 카운터가 프로세스마다 따로 센다.
    이미 쓰고 있는 MongoDB 를 공유 저장소로 재사용한다.

    RATELIMIT_STORAGE_URI 를 직접 주면 그대로 쓴다. 없으면 MONGO_URI 에
    앱 데이터베이스 경로를 붙여 만든다. 자격 증명을 두 곳에 적지 않고,
    별도 DB 를 만들지 않아 사용자 권한도 그대로 쓸 수 있다.
    """
    explicit = os.environ.get('RATELIMIT_STORAGE_URI', '')
    if explicit:
        return explicit

    mongo_uri = os.environ.get('MONGO_URI', '')
    if not mongo_uri.startswith('mongodb'):
        return 'memory://'

    parts = urlsplit(mongo_uri)
    path = parts.path if parts.path not in ('', '/') else '/' + os.environ.get('DB_NAME', 'memo')
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ''))


def from_env() -> dict[str, Any]:
    """환경 변수에서 설정을 읽는다."""
    return {
        # 'production' 이면 보안 관련 설정이 비었을 때 기동을 거부한다.
        'APP_ENV': os.environ.get('APP_ENV', 'development'),
        # 배포된 커밋. 배포 스크립트가 넣고 /healthz 가 알린다.
        'APP_REVISION': os.environ.get('APP_REVISION', 'unknown'),
        'MONGO_URI': os.environ.get('MONGO_URI', 'mongodb://localhost:27017'),
        'DB_NAME': os.environ.get('DB_NAME', 'memo'),
        'LOG_LEVEL': log_level(os.environ.get('LOG_LEVEL', 'INFO')),
        # 사이트 자체 출처. 교차 사이트 쓰기 요청을 막는 데 쓴다.
        'SITE_ORIGIN': os.environ.get('SITE_ORIGIN', ''),
        # 소유자가 없는 예전 메모를 고칠 때 쓰는 토큰. 비우면 그런 메모는 잠긴다.
        'ADMIN_TOKEN': os.environ.get('ADMIN_TOKEN', ''),
        # 요청 본문 상한. nginx 의 client_max_body_size 와 함께 이중으로 건다.
        'MAX_CONTENT_LENGTH': int(os.environ.get('MAX_BODY_BYTES', 1 * 1024 * 1024)),
        # 입력 제한
        'MAX_TITLE': int(os.environ.get('MAX_TITLE', 100)),
        'MAX_CONTENT': int(os.environ.get('MAX_CONTENT', 2000)),
        'MAX_TAGS': int(os.environ.get('MAX_TAGS', 5)),
        'MAX_TAG_LEN': int(os.environ.get('MAX_TAG_LEN', 20)),
        'MAX_QUERY': int(os.environ.get('MAX_QUERY', 50)),
        # 목록 조회 제한
        'DEFAULT_PAGE_SIZE': int(os.environ.get('DEFAULT_PAGE_SIZE', 20)),
        'MAX_PAGE_SIZE': int(os.environ.get('MAX_PAGE_SIZE', 100)),
        'MAX_PAGE': int(os.environ.get('MAX_PAGE', 200)),
        # 개수 세기 상한. 필터가 걸리면 인덱스를 못 타므로 무한정 세지 않는다.
        'COUNT_LIMIT': int(os.environ.get('COUNT_LIMIT', 1000)),
        # 저장 가능한 전체 메모 수 상한 (디스크 고갈 방지)
        'MAX_MEMOS': int(os.environ.get('MAX_MEMOS', 10_000)),
        # 태그 집계 캐시 유지 시간(초)
        'TAG_CACHE_TTL': int(os.environ.get('TAG_CACHE_TTL', 60)),
        # 쿼리 실행 시간 상한(밀리초)
        'QUERY_TIMEOUT_MS': int(os.environ.get('QUERY_TIMEOUT_MS', 2000)),
        # 레이트 리밋. 아래 네 키는 flask-limiter 가 직접 읽는다.
        'RATELIMIT_DEFAULT': os.environ.get('RATELIMIT_DEFAULT', '120 per minute'),
        'RATELIMIT_STORAGE_URI': storage_uri(),
        'RATELIMIT_ENABLED': os.environ.get('RATELIMIT_ENABLED', '1') == '1',
        'RATELIMIT_HEADERS_ENABLED': True,
        # 쓰기 전용 제한. 라우트 데코레이터가 이 값을 읽는다.
        'RATELIMIT_WRITE': os.environ.get('RATELIMIT_WRITE', '20 per minute'),
    }


def check_production(config: dict[str, Any]) -> list[str]:
    """운영 환경에서 비어 있으면 안 되는 설정을 찾는다."""
    problems = []
    if not config.get('SITE_ORIGIN'):
        problems.append('SITE_ORIGIN 이 비어 있어 교차 사이트 쓰기 검사가 꺼집니다.')
    if config.get('RATELIMIT_STORAGE_URI') == 'memory://':
        problems.append('RATELIMIT_STORAGE_URI 가 memory:// 라 워커마다 카운터가 따로 셉니다.')
    return problems
