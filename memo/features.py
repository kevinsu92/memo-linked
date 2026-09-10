"""이 메모장만의 기능 라우트.

- 위키 링크와 백링크, 연결 그래프
- 수정 이력과 되돌리기, 활동 히트맵
- 공유 링크와 읽으면 사라지는 메모
"""

from __future__ import annotations

import difflib
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
)
from pymongo import DESCENDING, ReturnDocument

from memo.crypto_notes import new_share_token, public_view, share_hash
from memo.db import get_db, now
from memo.extensions import limiter
from memo.models import is_locked, parse_id, serialize

logger = logging.getLogger('memo.features')

bp = Blueprint('features', __name__)

MAX_REVISIONS = 20
MAX_GRAPH_NODES = 150
MAX_ACTIVITY_DAYS = 366

ResponseTuple = tuple[Response, int]


def error(msg: str, status: int = 400) -> ResponseTuple:
    return jsonify({'result': 'fail', 'msg': msg}), status


# --------------------------------------------------------------------------
# 연결: 백링크와 그래프
# --------------------------------------------------------------------------


@bp.route('/memo/<memo_id>/links', methods=['GET'])
def memo_links(memo_id: str) -> ResponseTuple | Response:
    """이 메모가 가리키는 메모와, 이 메모를 가리키는 메모(백링크)."""
    oid = parse_id(memo_id)
    if oid is None:
        return error('잘못된 메모 id 입니다.')

    db = get_db()
    memo = db.memos.find_one({'_id': oid, 'deleted_at': None})
    if memo is None:
        return error('메모를 찾을 수 없습니다.', 404)

    wanted = memo.get('link_titles', [])
    outgoing = list(db.memos.find({'title_key': {'$in': wanted}, 'deleted_at': None}).limit(50))
    found_keys = {m.get('title_key') for m in outgoing}

    incoming = list(
        db.memos.find({'link_titles': memo.get('title_key'), 'deleted_at': None}).limit(50)
    )

    return jsonify(
        {
            'result': 'success',
            # 아직 만들어지지 않은 메모도 링크할 수 있다. 그 목록도 알려 준다.
            'missing': [key for key in wanted if key not in found_keys],
            'outgoing': [brief(m) for m in outgoing if m['_id'] != oid],
            'incoming': [brief(m) for m in incoming if m['_id'] != oid],
        }
    )


def brief(memo: dict[str, Any]) -> dict[str, Any]:
    """목록에 곁들일 짧은 정보."""
    return {
        'id': str(memo['_id']),
        'title': memo.get('title', ''),
        'tags': memo.get('tags', []),
    }


@bp.route('/graph', methods=['GET'])
def graph() -> Response:
    """메모 연결 관계. 클라이언트가 SVG 로 그린다."""
    db = get_db()
    memos = list(
        db.memos.find(
            {'deleted_at': None},
            projection={'title': 1, 'title_key': 1, 'link_titles': 1, 'tags': 1, 'like': 1},
        )
        .sort([('pinned', DESCENDING), ('created_at', DESCENDING)])
        .limit(MAX_GRAPH_NODES)
    )

    by_key = {m.get('title_key'): str(m['_id']) for m in memos if m.get('title_key')}
    nodes = [
        {
            'id': str(m['_id']),
            'title': m.get('title', ''),
            'tag': (m.get('tags') or [''])[0],
            'like': m.get('like', 0),
        }
        for m in memos
    ]

    edges = []
    for memo in memos:
        source = str(memo['_id'])
        for key in memo.get('link_titles', []):
            target = by_key.get(key)
            if target and target != source:
                edges.append({'from': source, 'to': target})

    return jsonify({'result': 'success', 'nodes': nodes, 'edges': edges})


# --------------------------------------------------------------------------
# 기록: 수정 이력과 되돌리기
# --------------------------------------------------------------------------


def save_revision(db: Any, memo: dict[str, Any]) -> None:
    """수정 직전 상태를 이력으로 남긴다. 메모당 최근 것만 유지한다."""
    db.revisions.insert_one(
        {
            'memo_id': memo['_id'],
            'title': memo.get('title', ''),
            'content': memo.get('content', ''),
            'tags': memo.get('tags', []),
            'saved_at': now(),
        }
    )

    keep = list(
        db.revisions.find({'memo_id': memo['_id']}, projection={'_id': 1})
        .sort('saved_at', DESCENDING)
        .limit(MAX_REVISIONS)
    )
    if len(keep) >= MAX_REVISIONS:
        db.revisions.delete_many(
            {
                'memo_id': memo['_id'],
                '_id': {'$nin': [r['_id'] for r in keep]},
            }
        )


@bp.route('/memo/<memo_id>/revisions', methods=['GET'])
def list_revisions(memo_id: str) -> ResponseTuple | Response:
    """수정 이력 목록. 현재 내용과의 차이도 함께 준다."""
    oid = parse_id(memo_id)
    if oid is None:
        return error('잘못된 메모 id 입니다.')

    db = get_db()
    memo = db.memos.find_one({'_id': oid, 'deleted_at': None})
    if memo is None:
        return error('메모를 찾을 수 없습니다.', 404)

    # 아직 열리지 않은 타임캡슐은 현재 내용도, 예전 이력도 내보내지 않는다.
    # 이 경로는 토큰조차 요구하지 않으므로 목록에서만 감추면 그대로 새어 나간다.
    if is_locked(memo):
        return jsonify(
            {
                'result': 'success',
                'locked': True,
                'current': {'title': memo.get('title', ''), 'content': ''},
                'revisions': [],
            }
        )

    revisions = list(db.revisions.find({'memo_id': oid}).sort('saved_at', DESCENDING).limit(20))
    return jsonify(
        {
            'result': 'success',
            'locked': False,
            'current': {'title': memo.get('title', ''), 'content': memo.get('content', '')},
            'revisions': [
                {
                    'id': str(rev['_id']),
                    'title': rev.get('title', ''),
                    'saved_at': rev['saved_at'].isoformat() if rev.get('saved_at') else None,
                    'diff': diff_lines(rev.get('content', ''), memo.get('content', '')),
                }
                for rev in revisions
            ],
        }
    )


def diff_lines(old: str, new: str) -> list[dict[str, str]]:
    """줄 단위 변경점. 화면에서 색만 입히면 되도록 정리해 둔다."""
    result: list[dict[str, str]] = []
    matcher = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        for line in old.splitlines()[i1:i2]:
            result.append({'kind': 'removed', 'text': line})
        for line in new.splitlines()[j1:j2]:
            result.append({'kind': 'added', 'text': line})
    return result[:200]


@bp.route('/memo/revision/restore', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def restore_revision() -> ResponseTuple | Response:
    """예전 내용으로 되돌린다. 되돌리기 직전 상태도 이력으로 남는다."""
    from memo.models import memo_fields
    from memo.routes import invalidate_tag_cache, load_and_authorize, read_field

    memo, failure = load_and_authorize(read_field('id_give', 'id'))
    if failure:
        return failure

    rev_id = parse_id(read_field('revision_give', 'revision'))
    if rev_id is None:
        return error('잘못된 이력 id 입니다.')

    db = get_db()
    revision = db.revisions.find_one({'_id': rev_id, 'memo_id': memo['_id']})
    if revision is None:
        return error('이력을 찾을 수 없습니다.', 404)

    save_revision(db, memo)
    updated = db.memos.find_one_and_update(
        {'_id': memo['_id']},
        {
            '$set': {
                **memo_fields(
                    revision.get('title', ''),
                    revision.get('content', ''),
                    revision.get('tags', []),
                ),
                'updated_at': now(),
            },
            '$inc': {'revision_count': 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    invalidate_tag_cache()
    logger.info('이력 복원: %s <- %s', memo['_id'], rev_id)
    return jsonify({'result': 'success', 'msg': '되돌렸습니다.', 'memo': serialize(updated)})


# --------------------------------------------------------------------------
# 기록: 활동 히트맵
# --------------------------------------------------------------------------


@bp.route('/stats/activity', methods=['GET'])
def activity() -> Response:
    """날짜별 작성 수. 잔디밭처럼 그리는 데 쓴다."""
    days = max(30, min(MAX_ACTIVITY_DAYS, request.args.get('days', 180, type=int) or 180))
    since = now() - timedelta(days=days)

    rows = get_db().memos.aggregate(
        [
            {'$match': {'deleted_at': None, 'created_at': {'$gte': since}}},
            {
                '$group': {
                    '_id': {'$dateToString': {'format': '%Y-%m-%d', 'date': '$created_at'}},
                    'count': {'$sum': 1},
                }
            },
            {'$sort': {'_id': 1}},
        ]
    )

    counts = {row['_id']: row['count'] for row in rows}
    return jsonify(
        {
            'result': 'success',
            'days': days,
            'from': since.strftime('%Y-%m-%d'),
            'counts': counts,
            'total': sum(counts.values()),
            'streak': current_streak(counts),
        }
    )


def current_streak(counts: dict[str, int]) -> int:
    """오늘(또는 어제)부터 며칠 연속으로 썼는지."""
    today = datetime.now(timezone.utc).date()
    if today.isoformat() not in counts and (today - timedelta(days=1)).isoformat() not in counts:
        return 0

    streak = 0
    day = today if today.isoformat() in counts else today - timedelta(days=1)
    while day.isoformat() in counts:
        streak += 1
        day -= timedelta(days=1)
    return streak


# --------------------------------------------------------------------------
# 공유: 읽기 전용 링크와 읽으면 사라지는 메모
# --------------------------------------------------------------------------


@bp.route('/memo/share', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def create_share() -> ResponseTuple | Response:
    """읽기 전용 공유 링크를 만들거나 없앤다. 소유 토큰이 필요하다."""
    from memo.routes import load_and_authorize, read_field

    memo, failure = load_and_authorize(read_field('id_give', 'id'))
    if failure:
        return failure

    if read_field('revoke') in ('1', 'true', 'True'):
        get_db().memos.update_one(
            {'_id': memo['_id']},
            {'$unset': {'share_hash': '', 'burn_after_read': ''}},
        )
        return jsonify({'result': 'success', 'msg': '공유를 중단했습니다.', 'token': None})

    burn = read_field('burn_give', 'burn') in ('1', 'true', 'True')
    token = new_share_token()
    get_db().memos.update_one(
        {'_id': memo['_id']},
        {'$set': {'share_hash': share_hash(token), 'burn_after_read': burn}},
    )
    logger.info('공유 링크 생성: %s (읽으면 삭제=%s)', memo['_id'], burn)
    return jsonify(
        {
            'result': 'success',
            'msg': '공유 링크를 만들었습니다.',
            'token': token,
            'burn_after_read': burn,
        }
    )


@bp.route('/s/<token>', methods=['GET', 'POST'])
def shared_page(token: str) -> ResponseTuple | str:
    """공유 링크로 여는 읽기 전용 화면.

    읽으면 사라지는 메모는 GET 으로 소각하지 않는다. 메신저의 링크 미리보기
    봇이나 메일 보안 검사기가 주소를 한 번 긁는 것만으로 사라지기 때문이다.
    사람이 버튼을 눌러 POST 로 확인한 뒤에만 소각한다.
    """
    memo = get_db().memos.find_one({'share_hash': share_hash(token), 'deleted_at': None})
    if memo is None:
        return render_template('shared.html', memo=None, burned=False, confirm=False), 404

    burn = bool(memo.get('burn_after_read'))

    if burn and request.method == 'GET':
        # 아직 보여 주지 않는다. 확인 화면만 띄운다.
        return render_template('shared.html', memo=None, burned=False, confirm=True)

    view = public_view(memo)

    if burn:
        get_db().memos.update_one(
            {'_id': memo['_id']},
            {'$set': {'deleted_at': now()}, '$unset': {'share_hash': ''}},
        )
        logger.info('읽고 사라지는 메모 열람: %s', memo['_id'])

    response = make_response(render_template('shared.html', memo=view, burned=burn, confirm=False))
    # 사라지는 메모가 캐시에 남으면 의미가 없다.
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


# --------------------------------------------------------------------------
# 타임캡슐
# --------------------------------------------------------------------------


@bp.route('/memo/capsule', methods=['POST'])
@limiter.limit(lambda: current_app.config['RATELIMIT_WRITE'])
def set_capsule() -> ResponseTuple | Response:
    """메모가 열릴 날짜를 정하거나 해제한다."""
    from memo.routes import load_and_authorize, read_field

    memo, failure = load_and_authorize(read_field('id_give', 'id'))
    if failure:
        return failure

    raw = read_field('open_at_give', 'open_at').strip()
    if not raw:
        updated = get_db().memos.find_one_and_update(
            {'_id': memo['_id']},
            {'$unset': {'open_at': ''}},
            return_document=ReturnDocument.AFTER,
        )
        return jsonify(
            {'result': 'success', 'msg': '잠금을 풀었습니다.', 'memo': serialize(updated)}
        )

    try:
        open_at = datetime.fromisoformat(raw)
    except ValueError:
        return error('날짜 형식이 올바르지 않습니다.')
    if open_at.tzinfo is None:
        open_at = open_at.replace(tzinfo=timezone.utc)
    if open_at <= now():
        return error('앞으로의 날짜를 지정하세요.')
    if open_at > now() + timedelta(days=3650):
        return error('10년 이내로 지정하세요.')

    updated = get_db().memos.find_one_and_update(
        {'_id': memo['_id']},
        {'$set': {'open_at': open_at}},
        return_document=ReturnDocument.AFTER,
    )
    logger.info('타임캡슐 설정: %s -> %s', memo['_id'], open_at.isoformat())
    return jsonify(
        {
            'result': 'success',
            'msg': '지정한 날짜에 열립니다.',
            'memo': serialize(updated),
        }
    )


# --------------------------------------------------------------------------
# 명령 팔레트용 빠른 검색
# --------------------------------------------------------------------------


@bp.route('/search/quick', methods=['GET'])
def quick_search() -> Response:
    """제목 위주로 빠르게 찾는다. 명령 팔레트가 쓴다."""
    keyword = request.args.get('q', '').strip()[: current_app.config['MAX_QUERY']]
    if not keyword:
        memos = list(
            get_db()
            .memos.find({'deleted_at': None}, projection={'title': 1, 'tags': 1})
            .sort('created_at', DESCENDING)
            .limit(8)
        )
    else:
        import re as _re

        pattern = _re.escape(keyword)
        memos = list(
            get_db()
            .memos.find(
                {'deleted_at': None, 'title': {'$regex': pattern, '$options': 'i'}},
                projection={'title': 1, 'tags': 1},
            )
            .limit(8)
            .max_time_ms(current_app.config['QUERY_TIMEOUT_MS'])
        )
    return jsonify({'result': 'success', 'memos': [brief(m) for m in memos]})


@bp.route('/stats/summary', methods=['GET'])
def summary() -> Response:
    """메모 수, 태그 수, 연결 수 요약."""
    db = get_db()
    total = db.memos.count_documents({'deleted_at': None}, limit=10_000)
    linked = db.memos.count_documents({'link_titles.0': {'$exists': True}, 'deleted_at': None})
    locked = db.memos.count_documents({'open_at': {'$gt': now()}, 'deleted_at': None})
    encrypted = db.memos.count_documents({'encrypted': True, 'deleted_at': None})

    tags: Counter = Counter()
    for row in db.memos.find({'deleted_at': None}, projection={'tags': 1}).limit(2000):
        tags.update(row.get('tags', []))

    return jsonify(
        {
            'result': 'success',
            'total': total,
            'linked': linked,
            'locked': locked,
            'encrypted': encrypted,
            'tags': len(tags),
        }
    )
