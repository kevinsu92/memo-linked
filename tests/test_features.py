"""위키 링크, 이력, 통계, 공유, 타임캡슐 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import ADMIN_TOKEN

from memo.crypto_notes import looks_encrypted, new_share_token, share_hash
from memo.links import extract_links, normalize_title


def form(memo_id=None, token=None, **extra):
    data = dict(extra)
    if memo_id is not None:
        data['id_give'] = memo_id
    if token is not None:
        data['token_give'] = token
    return data


# ---------------------------------------------------------------- 위키 링크


@pytest.mark.parametrize(
    'text,expected',
    [
        ('', []),
        ('링크 없음', []),
        ('[[파이썬]]', ['파이썬']),
        ('[[파이썬]] 과 [[Flask]]', ['파이썬', 'flask']),
        ('[[파이썬]] [[파이썬]]', ['파이썬']),
        ('[[  띄어  쓰기  ]]', ['띄어 쓰기']),
        ('[[Flask 정리|플라스크]]', ['flask 정리']),
        ('[[]]', []),
        ('[[중첩[[안됨]]', ['안됨']),
    ],
)
def test_extract_links(text, expected):
    assert extract_links(text) == expected


def test_extract_links_is_capped():
    text = ' '.join(f'[[메모{i}]]' for i in range(200))
    assert len(extract_links(text)) == 50


@pytest.mark.parametrize(
    'title,expected',
    [('Python', 'python'), ('  앞뒤 공백  ', '앞뒤 공백'), ('여러   공백', '여러 공백')],
)
def test_normalize_title(title, expected):
    assert normalize_title(title) == expected


def test_links_are_stored_on_save(client, db, make_memo):
    make_memo('연결 메모', '[[대상 메모]] 를 참고')
    stored = db.memos.find_one({'title': '연결 메모'})
    assert stored['link_titles'] == ['대상 메모']
    assert stored['title_key'] == '연결 메모'


def test_backlinks_both_directions(client, make_memo):
    target = make_memo('대상 메모', '내용')
    source = make_memo('출처 메모', '[[대상 메모]] 참고')

    incoming = client.get(f'/memo/{target}/links').get_json()
    assert [m['id'] for m in incoming['incoming']] == [source]
    assert incoming['outgoing'] == []

    outgoing = client.get(f'/memo/{source}/links').get_json()
    assert [m['id'] for m in outgoing['outgoing']] == [target]


def test_links_report_missing_targets(client, make_memo):
    memo_id = make_memo('출처', '[[아직 없는 메모]] 참고')
    body = client.get(f'/memo/{memo_id}/links').get_json()
    assert body['missing'] == ['아직 없는 메모']
    assert body['outgoing'] == []


def test_links_update_when_content_changes(client, db, owned):
    memo_id, token = owned('출처', '[[처음]] 참고')
    client.post(
        '/memo/update',
        data=form(memo_id, token, title_give='출처', content_give='[[나중]] 참고'),
    )
    assert db.memos.find_one({'title': '출처'})['link_titles'] == ['나중']


def test_deleted_memo_is_not_a_link_target(client, owned, make_memo):
    target, token = owned('대상', '내용')
    source = make_memo('출처', '[[대상]] 참고')
    client.post('/memo/delete', data=form(target, token))

    body = client.get(f'/memo/{source}/links').get_json()
    assert body['outgoing'] == []
    assert body['missing'] == ['대상']


# ------------------------------------------------------------------ 그래프


def test_graph_returns_nodes_and_edges(client, make_memo):
    target = make_memo('대상', '내용')
    source = make_memo('출처', '[[대상]] 참고')

    body = client.get('/graph').get_json()
    ids = {n['id'] for n in body['nodes']}
    assert {source, target} <= ids
    assert {'from': source, 'to': target} in body['edges']


def test_graph_ignores_links_to_missing_memos(client, make_memo):
    make_memo('출처', '[[없는 메모]] 참고')
    assert client.get('/graph').get_json()['edges'] == []


def test_graph_excludes_self_reference(client, make_memo):
    make_memo('자기 자신', '[[자기 자신]] 참고')
    assert client.get('/graph').get_json()['edges'] == []


# ------------------------------------------------------------------ 이력


def test_revision_is_saved_on_edit(client, owned):
    memo_id, token = owned('제목', '처음 내용')
    client.post(
        '/memo/update',
        data=form(memo_id, token, title_give='제목', content_give='고친 내용'),
    )

    body = client.get(f'/memo/{memo_id}/revisions').get_json()
    assert len(body['revisions']) == 1
    assert body['current']['content'] == '고친 내용'


def test_revision_diff_marks_added_and_removed(client, owned):
    memo_id, token = owned('제목', '첫 줄\n둘째 줄')
    client.post(
        '/memo/update',
        data=form(memo_id, token, title_give='제목', content_give='첫 줄\n바뀐 줄'),
    )

    diff = client.get(f'/memo/{memo_id}/revisions').get_json()['revisions'][0]['diff']
    kinds = {(d['kind'], d['text']) for d in diff}
    assert ('removed', '둘째 줄') in kinds
    assert ('added', '바뀐 줄') in kinds


def test_no_revision_when_nothing_changed(client, owned):
    memo_id, token = owned('제목', '내용')
    client.post(
        '/memo/update',
        data=form(memo_id, token, title_give='제목', content_give='내용', tags_give='새태그'),
    )
    assert client.get(f'/memo/{memo_id}/revisions').get_json()['revisions'] == []


def test_restore_revision_brings_back_old_content(client, owned):
    memo_id, token = owned('제목', '원래 내용')
    client.post(
        '/memo/update',
        data=form(memo_id, token, title_give='제목', content_give='바뀐 내용'),
    )
    rev_id = client.get(f'/memo/{memo_id}/revisions').get_json()['revisions'][0]['id']

    res = client.post('/memo/revision/restore', data=form(memo_id, token, revision_give=rev_id))
    assert res.status_code == 200
    assert res.get_json()['memo']['content'] == '원래 내용'


def test_restore_revision_requires_owner_token(client, owned):
    memo_id, token = owned('제목', '내용')
    client.post('/memo/update', data=form(memo_id, token, title_give='제목', content_give='둘'))
    rev_id = client.get(f'/memo/{memo_id}/revisions').get_json()['revisions'][0]['id']

    res = client.post('/memo/revision/restore', data=form(memo_id, 'wrong', revision_give=rev_id))
    assert res.status_code == 403


def test_revisions_are_capped(client, db, owned):
    from memo.features import MAX_REVISIONS

    memo_id, token = owned('제목', '내용 0')
    for i in range(1, MAX_REVISIONS + 6):
        client.post(
            '/memo/update',
            data=form(memo_id, token, title_give='제목', content_give=f'내용 {i}'),
        )

    from bson.objectid import ObjectId

    stored = db.revisions.count_documents({'memo_id': ObjectId(memo_id)})
    assert stored <= MAX_REVISIONS


# ------------------------------------------------------------------ 통계


def test_activity_counts_by_day(client, make_memo):
    make_memo('오늘 메모')
    body = client.get('/stats/activity?days=30').get_json()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    assert body['counts'][today] == 1
    assert body['total'] == 1
    assert body['streak'] == 1


def test_activity_days_are_clamped(client):
    assert client.get('/stats/activity?days=1').get_json()['days'] == 30
    assert client.get('/stats/activity?days=9999').get_json()['days'] == 366


def test_streak_counts_consecutive_days():
    from memo.features import current_streak

    today = datetime.now(timezone.utc).date()
    counts = {
        today.isoformat(): 1,
        (today - timedelta(days=1)).isoformat(): 2,
        (today - timedelta(days=2)).isoformat(): 1,
        (today - timedelta(days=5)).isoformat(): 1,
    }
    assert current_streak(counts) == 3


def test_streak_is_zero_after_a_gap():
    from memo.features import current_streak

    old = (datetime.now(timezone.utc).date() - timedelta(days=4)).isoformat()
    assert current_streak({old: 3}) == 0


def test_summary_counts(client, make_memo, owned):
    make_memo('평범한 메모', '내용', tags='a')
    make_memo('연결된 메모', '[[평범한 메모]]', tags='b')

    body = client.get('/stats/summary').get_json()
    assert body['total'] == 2
    assert body['linked'] == 1
    assert body['tags'] == 2


# ---------------------------------------------------------------- 빠른 검색


def test_quick_search_matches_title_only(client, make_memo):
    make_memo('제목에 파이썬', '내용에는 없음')
    make_memo('다른 제목', '내용에 파이썬')

    titles = [m['title'] for m in client.get('/search/quick?q=파이썬').get_json()['memos']]
    assert titles == ['제목에 파이썬']


def test_quick_search_without_keyword_returns_recent(client, make_memo):
    for i in range(3):
        make_memo(f'메모 {i}')
    assert len(client.get('/search/quick').get_json()['memos']) == 3


# ------------------------------------------------------------------ 공유


def test_share_link_shows_memo(client, owned):
    memo_id, token = owned('공유할 메모', '내용입니다')
    res = client.post('/memo/share', data=form(memo_id, token))
    assert res.status_code == 200

    page = client.get('/s/' + res.get_json()['token'])
    assert page.status_code == 200
    assert '공유된 메모' in page.get_data(as_text=True)


def test_share_requires_owner_token(client, owned):
    memo_id, _token = owned()
    assert client.post('/memo/share', data=form(memo_id, 'wrong')).status_code == 403


def test_share_token_is_stored_as_hash(client, db, owned):
    memo_id, token = owned()
    share = client.post('/memo/share', data=form(memo_id, token)).get_json()['token']
    from bson.objectid import ObjectId

    stored = db.memos.find_one({'_id': ObjectId(memo_id)})
    assert stored['share_hash'] == share_hash(share)
    assert share not in str(stored)


def test_unknown_share_token_returns_404(client):
    assert client.get('/s/' + new_share_token()).status_code == 404


def test_revoked_share_link_stops_working(client, owned):
    memo_id, token = owned()
    share = client.post('/memo/share', data=form(memo_id, token)).get_json()['token']
    client.post('/memo/share', data=form(memo_id, token, revoke='1'))
    assert client.get('/s/' + share).status_code == 404


def test_burned_memo_can_still_be_restored_by_owner(client, owned):
    memo_id, token = owned('한 번만', '내용')
    share = client.post('/memo/share', data=form(memo_id, token, burn_give='1')).get_json()['token']
    client.post('/s/' + share)  # 확인해서 실제로 소각한다

    assert client.post('/memo/restore', data=form(memo_id, token)).status_code == 200
    assert len(client.get('/memo').get_json()['memos']) == 1


# ---------------------------------------------------------------- 타임캡슐


def test_capsule_hides_content_until_open_time(client, owned):
    memo_id, token = owned('캡슐', '아직 비밀')
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    res = client.post('/memo/capsule', data=form(memo_id, token, open_at_give=future))
    assert res.status_code == 200

    memo = client.get('/memo').get_json()['memos'][0]
    assert memo['locked'] is True
    assert memo['content'] == ''
    assert memo['title'] == '캡슐'
    assert memo['open_at']


def test_capsule_content_is_never_sent_while_locked(client, owned):
    memo_id, token = owned('캡슐', '절대 새면 안 되는 내용')
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post('/memo/capsule', data=form(memo_id, token, open_at_give=future))

    body = client.get('/memo').get_data(as_text=True)
    assert '절대 새면' not in body


def test_capsule_opens_after_the_time_passes(client, db, owned):
    from bson.objectid import ObjectId

    memo_id, token = owned('캡슐', '이제 보임')
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post('/memo/capsule', data=form(memo_id, token, open_at_give=future))

    # 시간이 지난 것처럼 과거로 바꾼다.
    db.memos.update_one(
        {'_id': ObjectId(memo_id)},
        {'$set': {'open_at': datetime.now(timezone.utc) - timedelta(minutes=1)}},
    )
    memo = client.get('/memo').get_json()['memos'][0]
    assert memo['locked'] is False
    assert memo['content'] == '이제 보임'


@pytest.mark.parametrize('bad', ['어제', '2026-13-45', 'not-a-date'])
def test_capsule_rejects_bad_dates(client, owned, bad):
    memo_id, token = owned()
    assert (
        client.post('/memo/capsule', data=form(memo_id, token, open_at_give=bad)).status_code == 400
    )


def test_capsule_rejects_past_dates(client, owned):
    memo_id, token = owned()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert (
        client.post('/memo/capsule', data=form(memo_id, token, open_at_give=past)).status_code
        == 400
    )


def test_capsule_can_be_cleared(client, owned):
    memo_id, token = owned('캡슐', '내용')
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post('/memo/capsule', data=form(memo_id, token, open_at_give=future))
    client.post('/memo/capsule', data=form(memo_id, token, open_at_give=''))

    memo = client.get('/memo').get_json()['memos'][0]
    assert memo['locked'] is False
    assert memo['content'] == '내용'


def test_capsule_requires_owner_token(client, owned):
    memo_id, _token = owned()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert (
        client.post('/memo/capsule', data=form(memo_id, 'wrong', open_at_give=future)).status_code
        == 403
    )


def test_admin_token_can_set_capsule(client, owned):
    memo_id, _token = owned()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert (
        client.post(
            '/memo/capsule', data=form(memo_id, ADMIN_TOKEN, open_at_give=future)
        ).status_code
        == 200
    )


# ------------------------------------------------------------------ 잠금 메모


@pytest.mark.parametrize(
    'text,expected',
    [
        ('memo1.c2FsdHNhbHQ.aXZpdml2aXY.Y2lwaGVy', True),
        ('memo1.short.iv.cipher', False),
        ('평범한 내용', False),
        ('', False),
        ('memo2.c2FsdHNhbHQ.aXZpdml2aXY.Y2lwaGVy', False),
    ],
)
def test_looks_encrypted(text, expected):
    assert looks_encrypted(text) is expected


def test_encrypted_flag_is_set_from_content(client, make_memo):
    make_memo('잠긴 메모', 'memo1.c2FsdHNhbHQ.aXZpdml2aXY.Y2lwaGVydGV4dA')
    memo = client.get('/memo').get_json()['memos'][0]
    assert memo['encrypted'] is True


def test_plain_memo_is_not_flagged(client, make_memo):
    make_memo('평범한 메모', '그냥 글')
    assert client.get('/memo').get_json()['memos'][0]['encrypted'] is False


# ------------------------------------------- 타임캡슐 유출 경로 (회귀 테스트)


def capsule(client, title='캡슐', content='절대 새면 안 되는 내용', tags=''):
    """타임캡슐로 잠근 메모를 만들고 (id, 토큰) 을 돌려준다."""
    res = client.post(
        '/memo', data={'title_give': title, 'content_give': content, 'tags_give': tags}
    ).get_json()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post(
        '/memo/capsule',
        data=form(res['id'], res['owner_token'], open_at_give=future),
    )
    return res['id'], res['owner_token']


def test_capsule_content_not_exposed_through_revisions(client):
    """이력 조회는 토큰도 요구하지 않는다. 여기로 새면 잠금이 무의미하다."""
    memo_id, _token = capsule(client)
    body = client.get(f'/memo/{memo_id}/revisions').get_json()
    assert body['locked'] is True
    assert body['current']['content'] == ''
    assert body['revisions'] == []


def test_capsule_past_revisions_are_hidden_too(client, owned):
    """캡슐로 잠그기 전에 쌓인 이력도 내보내면 안 된다."""
    memo_id, token = owned('제목', '옛날 내용')
    client.post(
        '/memo/update',
        data=form(memo_id, token, title_give='제목', content_give='새 내용'),
    )
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.post('/memo/capsule', data=form(memo_id, token, open_at_give=future))

    body = client.get(f'/memo/{memo_id}/revisions').get_data(as_text=True)
    assert '옛날 내용' not in body
    assert '새 내용' not in body


def test_capsule_content_not_exposed_through_share_link(client):
    memo_id, token = capsule(client)
    share = client.post('/memo/share', data=form(memo_id, token)).get_json()['token']

    page = client.get('/s/' + share).get_data(as_text=True)
    assert '절대 새면' not in page
    assert '\uc808\ub300' not in page  # JSON 이스케이프된 형태도 없어야 한다


def test_capsule_is_not_searchable(client):
    """검색이 되면 '이 낱말이 들어 있는가' 를 맞혀 볼 수 있다."""
    capsule(client)
    assert client.get('/memo?q=절대').get_json()['memos'] == []
    assert client.get('/memo?q=새면').get_json()['memos'] == []
    assert client.get('/memo/count?q=절대').get_json()['total'] == 0


def test_capsule_is_still_listed_with_title(client):
    """감추는 것은 내용뿐이다. 목록에서 사라지면 안 된다."""
    capsule(client)
    memos = client.get('/memo').get_json()['memos']
    assert len(memos) == 1
    assert memos[0]['title'] == '캡슐'
    assert memos[0]['locked'] is True


def test_capsule_becomes_searchable_after_opening(client, db):
    from bson.objectid import ObjectId

    memo_id, _token = capsule(client)
    db.memos.update_one(
        {'_id': ObjectId(memo_id)},
        {'$set': {'open_at': datetime.now(timezone.utc) - timedelta(minutes=1)}},
    )
    assert len(client.get('/memo?q=절대').get_json()['memos']) == 1


# ------------------------------------- 읽으면 사라지는 메모 (GET 으로 소각 금지)


def test_burn_memo_is_not_destroyed_by_a_plain_get(client, owned):
    """링크 미리보기 봇이 주소를 긁어도 사라지면 안 된다."""
    memo_id, token = owned('한 번만', '내용')
    share = client.post('/memo/share', data=form(memo_id, token, burn_give='1')).get_json()['token']

    page = client.get('/s/' + share)
    assert page.status_code == 200
    assert '열고 지우기' in page.get_data(as_text=True)
    assert 'shared-memo' not in page.get_data(as_text=True)  # 본문 데이터가 실리지 않았다

    # 아직 살아 있어야 한다
    assert len(client.get('/memo').get_json()['memos']) == 1


def test_burn_memo_is_destroyed_after_confirming(client, owned):
    memo_id, token = owned('한 번만', '읽고 사라짐')
    share = client.post('/memo/share', data=form(memo_id, token, burn_give='1')).get_json()['token']

    opened = client.post('/s/' + share)
    assert opened.status_code == 200
    assert opened.headers['Cache-Control'] == 'no-store, max-age=0'

    assert client.get('/memo').get_json()['memos'] == []
    assert client.get('/s/' + share).status_code == 404


def test_normal_share_link_still_opens_with_get(client, owned):
    memo_id, token = owned('보통 공유', '내용')
    share = client.post('/memo/share', data=form(memo_id, token)).get_json()['token']
    assert client.get('/s/' + share).status_code == 200
    assert client.get('/s/' + share).status_code == 200  # 두 번 열려도 남아 있다
