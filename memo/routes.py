"""메모 API 라우트."""

from __future__ import annotations

import logging
import time
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from memo.crypto_notes import looks_encrypted
from memo.db import SORTS, get_db, now
from memo.extensions import limiter
from memo.models import (
    build_query,
    clamp_int,
    hash_token,
    memo_fields,
    new_owner_token,
    parse_id,
    parse_tags,
    serialize,
    token_matches,
    validate_memo,
)

logger = logging.getLogger('memo.routes')

bp = Blueprint('memo', __name__)

# 템플릿에 넘길 설정 키. config 전체를 넘기면 MONGO_URI 같은 비밀값이
# 실수 한 번으로 HTML 에 렌더링될 수 있다.
UI_CONFIG_KEYS = ('MAX_TITLE', 'MAX_CONTENT', 'MAX_TAGS', 'MAX_TAG_LEN')

ResponseTuple = tuple[Response, int]


def save_revision(db: Any, memo: dict[str, Any]) -> None:
    """features 모듈의 이력 저장을 늦게 불러온다 (순환 import 방지)."""
    from memo.features import save_revision as _save

    _save(db, memo)


def error(msg: str, status: int = 400) -> ResponseTuple:
    """실패 응답."""
    return jsonify({'result': 'fail', 'msg': msg}), status


def cfg() -> dict[str, Any]:
    return current_app.config


def read_form() -> tuple[str, str, list[str]]:
    """폼 또는 JSON 본문에서 제목/내용/태그를 읽어 정규화한다."""
    data = request.get_json(silent=True) if request.is_json else None
    if data is None:
        data = request.form
    title = str(data.get('title_give', data.get('title', ''))).strip()
    content = str(data.get('content_give', data.get('content', ''))).strip()
    tags = parse_tags(data.get('tags_give', data.get('tags', '')))
    return title, content, tags


def read_field(*names: str) -> str:
    """폼 또는 JSON 본문에서 첫 번째로 존재하는 필드를 읽는다."""
    data = request.get_json(silent=True) if request.is_json else None
    if data is None:
        data = request.form
    for name in names:
        value = data.get(name)
        if value is not None:
            return str(value)
    return ''


# --------------------------------------------------------------------------
# 소유권
# --------------------------------------------------------------------------


def authorize(memo: dict[str, Any]) -> str | None:
    """메모를 고칠 권한이 있는지 확인한다. 문제가 있으면 메시지, 없으면 None.

    - 저장할 때 발급한 소유 토큰을 가진 요청은 통과한다.
    - 관리자 토큰(ADMIN_TOKEN)이 설정돼 있고 그 값이 오면 통과한다.
    - 소유자가 없는 예전 메모는 관리자 토큰이 있어야 고칠 수 있다.
    """
    supplied = read_field('token_give', 'token') or request.headers.get('X-Memo-Token', '')
    admin = cfg().get('ADMIN_TOKEN')
    if admin and token_matches(supplied, hash_token(admin)):
        return None

    stored = memo.get('owner_hash')
    if not stored:
        return '이 메모에는 소유자가 없습니다. 관리자 토큰이 필요합니다.'
    if not token_matches(supplied, stored):
        return '이 메모를 고칠 권한이 없습니다.'
    return None


def load_and_authorize(memo_id_raw: str) -> tuple[Any, ResponseTuple | None]:
    """id 를 확인하고 메모를 불러온 뒤 권한까지 검사한다."""
    memo_id = parse_id(memo_id_raw)
    if memo_id is None:
        return None, error('잘못된 메모 id 입니다.')

    memo = get_db().memos.find_one({'_id': memo_id, 'deleted_at': None})
    if memo is None:
        return None, error('메모를 찾을 수 없습니다.', 404)

    msg = authorize(memo)
    if msg:
        logger.info('권한 없는 수정 시도: %s', memo_id)
        return None, error(msg, 403)
    return memo, None


# --------------------------------------------------------------------------
# 페이지 / 상태
# --------------------------------------------------------------------------


@bp.route('/')
def home() -> str:
    limits = {key: current_app.config[key] for key in UI_CONFIG_KEYS}
    return render_template('index.html', limits=limits)


@bp.route('/healthz')
@limiter.exempt
def healthz() -> ResponseTuple | Response:
    """헬스 체크. 앱 DB 에 ping 을 보내 연결까지 확인하고 배포된 버전을 알린다."""
    try:
        get_db().command('ping')
    except PyMongoError as exc:
        logger.warning('헬스 체크 실패: %s', exc)
        return jsonify({'result': 'fail', 'db': 'down'}), 503
    return jsonify(
        {
            'result': 'success',
            'db': 'up',
            'revision': cfg().get('APP_REVISION', 'unknown'),
        }
    )


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------


@bp.route('/memo', methods=['GET'])
def get_memos() -> Response:
    """메모 목록. 검색(q), 태그(tag), 정렬(sort), 페이지(page/size)를 지원한다."""
    conf = cfg()
    keyword = request.args.get('q', '').strip()[: conf['MAX_QUERY']]
    tag = request.args.get('tag', '').strip()[: conf['MAX_TAG_LEN']]
    sort = SORTS.get(request.args.get('sort', 'newest'), SORTS['newest'])
    page = clamp_int(request.args.get('page'), 1, 1, conf['MAX_PAGE'])
    size = clamp_int(request.args.get('size'), conf['DEFAULT_PAGE_SIZE'], 1, conf['MAX_PAGE_SIZE'])
    # 목록에서 항목이 지워지면 뒤 페이지가 앞으로 당겨진다. 클라이언트가 그동안
    # 지운 개수를 보내면 그만큼 건너뛰기를 줄여 항목이 건너뛰이지 않게 한다.
    shift = clamp_int(request.args.get('shift'), 0, 0, conf['MAX_PAGE_SIZE'])

    skip = max(0, (page - 1) * size - shift)
    query = build_query(keyword, tag)
    # size + 1 건을 읽어 다음 페이지 존재 여부를 판단한다.
    docs = list(
        get_db()
        .memos.find(query)
        .sort(sort)
        .skip(skip)
        .limit(size + 1)
        .max_time_ms(conf['QUERY_TIMEOUT_MS'])
    )
    has_more = len(docs) > size

    return jsonify(
        {
            'result': 'success',
            'memos': [serialize(m) for m in docs[:size]],
            'page': page,
            'size': size,
            'has_more': has_more,
        }
    )


@bp.route('/memo/count', methods=['GET'])
def count_memos() -> Response:
    """조건에 맞는 메모 개수.

    검색·태그 필터가 걸린 조회는 인덱스를 못 타므로 상한을 둔다.
    상한에 걸리면 exact=false 로 알려 클라이언트가 '999+' 처럼 표기하게 한다.
    """
    conf = cfg()
    keyword = request.args.get('q', '').strip()[: conf['MAX_QUERY']]
    tag = request.args.get('tag', '').strip()[: conf['MAX_TAG_LEN']]
    query = build_query(keyword, tag)
    cap = conf['COUNT_LIMIT']

    total = get_db().memos.count_documents(
        query, limit=max(1, cap), maxTimeMS=conf['QUERY_TIMEOUT_MS']
    )
    return jsonify({'result': 'success', 'total': total, 'exact': total < cap})


@bp.route('/tags', methods=['GET'])
def get_tags() -> Response:
    """사용 중인 태그와 개수. 많이 쓰인 순."""
    cache = tag_cache()
    ttl = cfg()['TAG_CACHE_TTL']
    current = time.monotonic()

    if cache['data'] is None or current - cache['at'] > ttl:
        pipeline = [
            {'$match': {'deleted_at': None}},
            {'$unwind': '$tags'},
            {'$group': {'_id': '$tags', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1, '_id': 1}},
            {'$limit': 50},
        ]
        cache['data'] = [
            {'tag': row['_id'], 'count': row['count']} for row in get_db().memos.aggregate(pipeline)
        ]
        cache['at'] = current
    return jsonify({'result': 'success', 'tags': cache['data']})


def tag_cache() -> dict[str, Any]:
    """태그 집계 캐시. 앱 인스턴스마다 따로 둬야 DB 가 섞이지 않는다."""
    return current_app.extensions.setdefault('tag_cache', {'at': 0.0, 'data': None})


def invalidate_tag_cache() -> None:
    tag_cache()['data'] = None


# --------------------------------------------------------------------------
# 쓰기
# --------------------------------------------------------------------------


@bp.route('/memo', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def save_memo() -> ResponseTuple:
    """메모 저장. 소유 토큰을 발급해 한 번만 돌려준다."""
    conf = cfg()
    title, content, tags = read_form()
    msg = validate_memo(title, content, tags, conf)
    if msg:
        return error(msg)

    db = get_db()
    # limit 은 0 을 '제한 없음' 으로 해석하므로 최소 1 을 넘긴다.
    stored = db.memos.count_documents({'deleted_at': None}, limit=max(1, conf['MAX_MEMOS']))
    if stored >= conf['MAX_MEMOS']:
        return error('저장 한도에 도달했습니다. 오래된 메모를 지운 뒤 다시 시도하세요.', 507)

    token = new_owner_token()
    result = db.memos.insert_one(
        {
            # 제목 키와 위키 링크 목록도 여기서 함께 계산된다.
            **memo_fields(title, content, tags),
            'like': 0,
            'pinned': False,
            'created_at': now(),
            'updated_at': None,
            'deleted_at': None,
            'owner_hash': hash_token(token),
            # 브라우저에서 암호화한 내용인지. 서버는 형식만 보고 판단한다.
            'encrypted': looks_encrypted(content),
        }
    )
    invalidate_tag_cache()
    logger.info('메모 저장: %s', result.inserted_id)
    return jsonify(
        {
            'result': 'success',
            'msg': '저장했습니다.',
            'id': str(result.inserted_id),
            'owner_token': token,
        }
    ), 201


@bp.route('/memo/update', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def update_memo() -> ResponseTuple | Response:
    """메모 수정. 소유 토큰이 필요하다."""
    memo, failure = load_and_authorize(read_field('id_give', 'id'))
    if failure:
        return failure

    title, content, tags = read_form()
    msg = validate_memo(title, content, tags, cfg())
    if msg:
        return error(msg)

    db = get_db()
    # 고치기 전 상태를 이력으로 남긴다. 되돌릴 수 있어야 마음 놓고 고친다.
    if memo.get('content') != content or memo.get('title') != title:
        save_revision(db, memo)

    updated = db.memos.find_one_and_update(
        {'_id': memo['_id'], 'deleted_at': None},
        {
            '$set': {
                **memo_fields(title, content, tags),
                'encrypted': looks_encrypted(content),
                'updated_at': now(),
            },
            '$inc': {'revision_count': 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        return error('메모를 찾을 수 없습니다.', 404)
    invalidate_tag_cache()
    logger.info('메모 수정: %s', memo['_id'])
    return jsonify({'result': 'success', 'msg': '수정했습니다.', 'memo': serialize(updated)})


@bp.route('/memo/delete', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def delete_memo() -> ResponseTuple | Response:
    """메모 삭제.

    바로 지우지 않고 삭제 표시만 남긴다. 유예 기간이 지나면 MongoDB 의
    TTL 인덱스가 실제로 지운다. 그동안은 복구할 수 있다.
    """
    memo, failure = load_and_authorize(read_field('id_give', 'id'))
    if failure:
        return failure

    get_db().memos.update_one({'_id': memo['_id']}, {'$set': {'deleted_at': now()}})
    invalidate_tag_cache()
    logger.info('메모 삭제 표시: %s', memo['_id'])
    return jsonify({'result': 'success', 'msg': '삭제했습니다.', 'id': str(memo['_id'])})


@bp.route('/memo/restore', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def restore_memo() -> ResponseTuple | Response:
    """삭제한 메모를 되살린다. 좋아요 수와 작성 시각이 그대로 유지된다."""
    memo_id = parse_id(read_field('id_give', 'id'))
    if memo_id is None:
        return error('잘못된 메모 id 입니다.')

    memo = get_db().memos.find_one({'_id': memo_id})
    if memo is None:
        return error('메모를 찾을 수 없습니다.', 404)
    if memo.get('deleted_at') is None:
        return error('삭제된 메모가 아닙니다.')

    msg = authorize(memo)
    if msg:
        return error(msg, 403)

    restored = get_db().memos.find_one_and_update(
        {'_id': memo_id},
        {'$set': {'deleted_at': None}},
        return_document=ReturnDocument.AFTER,
    )
    invalidate_tag_cache()
    logger.info('메모 복구: %s', memo_id)
    return jsonify({'result': 'success', 'msg': '복구했습니다.', 'memo': serialize(restored)})


@bp.route('/memo/like', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def like_memo() -> ResponseTuple | Response:
    """좋아요 +1. 누구나 누를 수 있고 되돌릴 수 있는 동작이라 토큰을 요구하지 않는다."""
    memo_id = parse_id(read_field('id_give', 'id'))
    if memo_id is None:
        return error('잘못된 메모 id 입니다.')

    memo = get_db().memos.find_one_and_update(
        {'_id': memo_id, 'deleted_at': None},
        {'$inc': {'like': 1}},
        return_document=ReturnDocument.AFTER,
    )
    if memo is None:
        return error('메모를 찾을 수 없습니다.', 404)
    return jsonify({'result': 'success', 'msg': '좋아요!', 'like': memo.get('like', 0)})


@bp.route('/memo/pin', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def pin_memo() -> ResponseTuple | Response:
    """고정 토글. 소유 토큰이 필요하다.

    파이프라인 업데이트로 서버에서 값을 뒤집는다. 읽고-쓰는 방식이었다면
    동시 요청 시 토글 한 번이 유실될 수 있다.
    """
    memo, failure = load_and_authorize(read_field('id_give', 'id'))
    if failure:
        return failure

    updated = get_db().memos.find_one_and_update(
        {'_id': memo['_id'], 'deleted_at': None},
        [{'$set': {'pinned': {'$not': [{'$ifNull': ['$pinned', False]}]}}}],
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        return error('메모를 찾을 수 없습니다.', 404)

    pinned = bool(updated.get('pinned'))
    return jsonify(
        {
            'result': 'success',
            'msg': '고정했습니다.' if pinned else '고정을 해제했습니다.',
            'pinned': pinned,
        }
    )
