/**
 * 나만의 메모장 프론트엔드.
 *
 * jQuery 없이 표준 DOM API 만 쓴다. 외부 라이브러리는 마크다운 렌더링에
 * 쓰는 marked/DOMPurify 뿐이고, 둘 중 하나라도 없으면 평문으로 안전하게 떨어진다.
 */
(function () {
    'use strict';

    // ---------------------------------------------------------------- 설정

    var cfg = window.MEMO_CONFIG || {};
    var MAX_TITLE = cfg.maxTitle || 100;
    var MAX_CONTENT = cfg.maxContent || 2000;
    var MAX_TAGS = cfg.maxTags || 5;
    var MAX_TAG_LEN = cfg.maxTagLen || 20;
    var PAGE_SIZE = 20;
    var REQUEST_TIMEOUT = 10000;
    // 암호화하면 base64 로 약 2.7배까지 늘어난다. 서버 한도(MAX_CONTENT)를
    // 넘지 않도록 잠금 메모는 더 짧게 제한한다.
    var LOCKED_MAX_CONTENT = 600;
    var DRAFT_KEY = 'memo:draft';
    var TOKENS_KEY = 'memo:tokens';       // 내가 쓴 메모의 소유 토큰
    var ADMIN_KEY = 'memo:admin';         // 예전 메모용 관리자 토큰

    var SORT_VALUES = ['newest', 'oldest', 'like'];
    var state = { q: '', tag: '', sort: 'newest', page: 1, loaded: 0, shift: 0 };
    var reqSeq = 0;          // 목록 응답 경합 방지용 시퀀스
    var countSeq = 0;        // 개수 응답 경합 방지용 시퀀스
    var tagSeq = 0;          // 태그 응답 경합 방지용 시퀀스
    var listBusy = false;
    var searchTimer = null;
    var draftTimer = null;

    var el = {};

    // ---------------------------------------------------------------- 유틸

    function $(id) { return document.getElementById(id); }

    var storageBroken = false;

    /**
     * localStorage 접근. 프라이빗 모드나 저장소 차단 환경에서는 실패한다.
     *
     * 소유 토큰을 여기에 두므로 쓰기 실패를 조용히 넘기면 "저장했습니다" 를
     * 보고도 그 메모를 영영 고칠 수 없게 된다. 실패하면 알린다.
     */
    function storage(action, key, value) {
        try {
            if (action === 'get') return localStorage.getItem(key);
            if (action === 'set') localStorage.setItem(key, value);
            if (action === 'remove') localStorage.removeItem(key);
            return null;
        } catch (e) {
            if (action === 'set' && !storageBroken) {
                storageBroken = true;
                window.setTimeout(function () {
                    toast(
                        '브라우저 저장소를 쓸 수 없어 이 메모를 나중에 고칠 수 없습니다.',
                        'error'
                    );
                }, 0);
            }
            return null;
        }
    }

    // ---------------------------------------------------------------- 소유 토큰

    /** 저장할 때 서버가 발급한 토큰. 이게 있어야 수정·삭제할 수 있다. */
    function tokenStore() {
        try {
            return JSON.parse(storage('get', TOKENS_KEY) || '{}');
        } catch (e) {
            return {};
        }
    }

    function ownerToken(id) {
        return tokenStore()[id] || '';
    }

    function rememberToken(id, token) {
        var all = tokenStore();
        all[id] = token;
        storage('set', TOKENS_KEY, JSON.stringify(all));
    }

    function adminToken() {
        return storage('get', ADMIN_KEY) || '';
    }

    /** 이 메모를 고칠 수 있는지. 서버가 최종 판단하지만 UI 를 미리 맞춘다. */
    function canEdit(memo) {
        if (adminToken()) return true;
        return memo.has_owner ? Boolean(ownerToken(memo.id)) : false;
    }

    function formatDate(iso) {
        if (!iso) return '';
        var d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '';
        try {
            var locale = document.documentElement.lang || 'ko-KR';
            return new Intl.DateTimeFormat(locale, {
                dateStyle: 'medium', timeStyle: 'short',
            }).format(d);
        } catch (e) {
            return d.toLocaleString();
        }
    }

    /** 마크다운을 HTML 로. DOMPurify 로 반드시 소독한다. */
    function renderMarkdown(text) {
        if (!window.marked || !window.DOMPurify) {
            // 인라인 style 은 CSP 에 막히므로 클래스로 처리한다.
            var pre = document.createElement('p');
            pre.className = 'plain-fallback';
            pre.textContent = text;
            return pre.outerHTML;
        }
        return window.DOMPurify.sanitize(window.marked.parse(text), {
            ALLOWED_TAGS: [
                'p', 'br', 'hr', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
                'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a', 'img',
            ],
            ALLOWED_ATTR: ['href', 'src', 'alt', 'title'],
            ALLOW_DATA_ATTR: false,
            FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed', 'svg'],
            FORBID_ATTR: ['style', 'srcset', 'formaction'],
        });
    }

    /**
     * 렌더링된 DOM 에서 [[제목]] 을 눌러 갈 수 있는 링크로 바꾼다.
     *
     * 마크다운 렌더링 뒤 텍스트 노드만 손대므로 태그 구조를 깨지 않고,
     * 문자열을 다시 innerHTML 로 넣지 않아 XSS 경로도 생기지 않는다.
     */
    function linkifyWiki(root) {
        var pattern = /\[\[([^\[\]|]{1,100})(?:\|([^\[\]]{1,100}))?\]\]/;
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        var targets = [];
        while (walker.nextNode()) {
            if (pattern.test(walker.currentNode.nodeValue)) targets.push(walker.currentNode);
        }

        targets.forEach(function (node) {
            var frag = document.createDocumentFragment();
            var text = node.nodeValue;
            var match = pattern.exec(text);
            while (match) {
                frag.appendChild(document.createTextNode(text.slice(0, match.index)));
                var title = match[1].trim();
                var label = (match[2] || match[1]).trim();

                var link = document.createElement('button');
                link.type = 'button';
                link.className = 'wikilink';
                link.textContent = label;
                link.dataset.title = title;
                link.setAttribute('aria-label', '연결된 메모로 이동: ' + title);
                link.addEventListener('click', function () { jumpToTitle(title); });
                frag.appendChild(link);

                text = text.slice(match.index + match[0].length);
                match = pattern.exec(text);
            }
            frag.appendChild(document.createTextNode(text));
            node.parentNode.replaceChild(frag, node);
        });
    }

    function jumpToTitle(title) {
        request('GET', '/search/quick?q=' + encodeURIComponent(title))
            .then(function (data) {
                var key = title.trim().toLowerCase();
                var hit = (data.memos || []).filter(function (m) {
                    return m.title.trim().toLowerCase() === key;
                })[0];

                if (!hit) {
                    toast('“' + title + '” 메모가 아직 없습니다.', null, {
                        label: '지금 쓰기',
                        run: function () {
                            startWriting(title);
                        },
                    });
                    return;
                }
                focusMemo(hit.id);
            })
            .catch(showError);
    }

    function focusMemo(id, attempt) {
        var tries = attempt || 0;
        var card = document.getElementById('card-' + id);
        if (!card) {
            // 목록에 없으면 조건을 풀고 한 번만 더 찾는다. 뒤 페이지에 있는
            // 메모라면 계속 못 찾으므로 재시도를 제한한다.
            if (tries >= 2) {
                toast('그 메모는 지금 목록에 없습니다. 검색으로 찾아 보세요.', 'error');
                return;
            }
            el.search.value = '';
            state.tag = '';
            reload();
            window.setTimeout(function () { focusMemo(id, tries + 1); }, 700);
            return;
        }
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.add('flash');
        card.tabIndex = -1;
        card.focus();
        window.setTimeout(function () { card.classList.remove('flash'); }, 1600);
    }

    function highlightIn(root, keyword) {
        if (!keyword) return;
        var lower = keyword.toLowerCase();
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        var targets = [];
        while (walker.nextNode()) {
            if (walker.currentNode.nodeValue.toLowerCase().indexOf(lower) !== -1) {
                targets.push(walker.currentNode);
            }
        }
        targets.forEach(function (node) {
            var frag = document.createDocumentFragment();
            var text = node.nodeValue;
            var idx = text.toLowerCase().indexOf(lower);
            while (idx !== -1) {
                frag.appendChild(document.createTextNode(text.slice(0, idx)));
                var mark = document.createElement('mark');
                mark.textContent = text.slice(idx, idx + keyword.length);
                frag.appendChild(mark);
                text = text.slice(idx + keyword.length);
                idx = text.toLowerCase().indexOf(lower);
            }
            frag.appendChild(document.createTextNode(text));
            node.parentNode.replaceChild(frag, node);
        });
    }

    // ---------------------------------------------------------------- 토스트

    function toast(message, kind, action) {
        var box = document.createElement('div');
        box.className = 'toast-item' + (kind === 'error' ? ' error' : '');
        var text = document.createElement('span');
        text.textContent = message;
        box.appendChild(text);

        var entry = { node: box, action: null, onDismiss: null };
        var timer = null;

        function dismiss() {
            if (timer) clearTimeout(timer);
            box.remove();
            if (entry.onDismiss) entry.onDismiss();
        }

        function arm(delay) {
            if (timer) clearTimeout(timer);
            timer = setTimeout(dismiss, delay);
        }

        if (action) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'toast-action';
            btn.textContent = action.label;
            btn.addEventListener('click', function () {
                action.run();
                dismiss();
            });
            // 포커스가 안에 있는 동안에는 사라지지 않게 한다.
            box.addEventListener('focusin', function () {
                if (timer) { clearTimeout(timer); timer = null; }
            });
            box.addEventListener('focusout', function () { arm(4000); });
            box.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') dismiss();
            });
            box.appendChild(btn);
            entry.action = btn;
        }

        // 오류는 즉시 읽히도록 alert 영역에, 알림은 status 영역에 넣는다.
        (kind === 'error' ? el.alerts : el.toasts).appendChild(box);
        arm(action ? 12000 : 4000);
        return entry;
    }

    function announce(message) {
        el.status.textContent = message;
    }

    // ---------------------------------------------------------------- 통신

    function request(method, url, body) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT);

        var options = {
            method: method,
            headers: { 'Accept': 'application/json' },
            signal: controller.signal,
        };
        if (body) {
            options.body = new URLSearchParams(withToken(url, body));
            options.headers['Content-Type'] = 'application/x-www-form-urlencoded';
        }

        return fetch(url, options)
            .then(function (res) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    if (!res.ok) {
                        var err = new Error(data.msg || '요청에 실패했습니다.');
                        err.status = res.status;
                        throw err;
                    }
                    return data;
                });
            })
            .catch(function (err) {
                // 상태 코드가 없으면 HTTP 응답 자체를 못 받은 것이다.
                if (err.name === 'AbortError') {
                    throw new Error('응답이 너무 늦어 요청을 취소했습니다.');
                }
                if (err.status === undefined) {
                    throw new Error('네트워크에 연결할 수 없습니다.');
                }
                throw err;
            })
            .finally(function () { clearTimeout(timer); });
    }

    function withToken(url, body) {
        if (url.indexOf('/memo/') !== 0 && url !== '/memo') return body;
        if (body.token_give) return body;
        var token = body.id_give ? ownerToken(body.id_give) : '';
        if (!token) token = adminToken();
        if (token) body.token_give = token;
        return body;
    }

    function showError(err) {
        toast(err.message || '요청에 실패했습니다.', 'error');
    }

    // ---------------------------------------------------------------- 목록

    function skeletons(count) {
        var frag = document.createDocumentFragment();
        for (var i = 0; i < count; i++) {
            var li = document.createElement('li');
            li.className = 'skeleton';
            li.innerHTML = '<div class="bar"></div><div class="bar"></div><div class="bar short"></div>';
            frag.appendChild(li);
        }
        return frag;
    }

    function reload(options) {
        var opts = options || {};
        state.q = el.search.value.trim();
        state.sort = el.sort.value;
        state.page = 1;
        state.loaded = 0;
        state.shift = 0;               // 목록을 새로 읽으므로 삭제 보정도 초기화
        fetchMemos(true, opts.focusAfter);
        refreshCount();
        syncUrl(opts.push);
    }

    function loadMore() {
        if (listBusy) return;
        state.page += 1;
        fetchMemos(false);
    }

    function fetchMemos(replace, focusAfter) {
        var mine = ++reqSeq;
        listBusy = true;
        el.cards.setAttribute('aria-busy', 'true');
        el.moreBtn.setAttribute('aria-disabled', 'true');

        if (replace && !el.cards.firstElementChild) {
            // 첫 로드에만 스켈레톤을 보여 준다. 이후 갱신은 기존 목록을 흐리게 해
            // 화면이 깜빡이지 않게 한다.
            el.cards.replaceChildren(skeletons(4));
        } else {
            el.cards.classList.add('loading');
            if (!replace) el.moreBtn.textContent = '불러오는 중...';
        }

        var params = new URLSearchParams({
            q: state.q, tag: state.tag, sort: state.sort,
            page: String(state.page), size: String(PAGE_SIZE),
        });
        if (state.shift) params.set('shift', String(state.shift));

        request('GET', '/memo?' + params.toString())
            .then(function (data) {
                if (mine !== reqSeq) return;          // 늦게 도착한 응답은 버린다
                if (replace) el.cards.replaceChildren();
                state.loaded += data.memos.length;

                var frag = document.createDocumentFragment();
                data.memos.forEach(function (m) { frag.appendChild(buildCard(m)); });
                var firstNew = frag.firstElementChild;
                el.cards.appendChild(frag);

                el.moreBtn.classList.toggle('hidden', !data.has_more);

                if (state.loaded === 0) {
                    var li = document.createElement('li');
                    li.className = 'empty';
                    li.textContent = (state.q || state.tag)
                        ? '조건에 맞는 메모가 없습니다.'
                        : '아직 메모가 없습니다. 위에서 첫 메모를 남겨보세요.';
                    el.cards.appendChild(li);
                }

                announce(replace
                    ? state.loaded + '개를 표시했습니다.'
                    : data.memos.length + '개를 더 불러왔습니다. 현재 ' + state.loaded + '개.');

                if (focusAfter) {
                    restoreFocus(focusAfter);
                } else if (!replace && firstNew) {
                    // 더 보기로 추가된 첫 카드로 포커스를 옮긴다.
                    firstNew.tabIndex = -1;
                    firstNew.focus();
                }
            })
            .catch(function (err) {
                if (mine !== reqSeq) return;
                if (!replace) state.page -= 1;        // 실패한 페이지는 되돌린다
                if (replace) {
                    el.cards.replaceChildren(errorRow('li', '목록을 불러오지 못했습니다.', function () {
                        reload();
                    }));
                }
                showError(err);
            })
            .finally(function () {
                if (mine !== reqSeq) return;
                listBusy = false;
                el.cards.removeAttribute('aria-busy');
                el.cards.classList.remove('loading');
                el.moreBtn.removeAttribute('aria-disabled');
                el.moreBtn.textContent = '더 보기';
            });
    }

    function errorRow(tag, message, retry) {
        var li = document.createElement(tag);
        li.className = 'list-error';
        li.textContent = message + ' ';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary';
        btn.textContent = '다시 시도';
        btn.addEventListener('click', retry);
        li.appendChild(btn);
        return li;
    }

    function refreshCount() {
        var mine = ++countSeq;
        var params = new URLSearchParams({ q: state.q, tag: state.tag });
        request('GET', '/memo/count?' + params.toString())
            .then(function (data) {
                if (mine !== countSeq) return;
                var shown = data.exact ? String(data.total) : data.total + '+';
                el.count.textContent = (state.q || state.tag)
                    ? '조건에 맞는 메모 ' + shown + '개'
                    : '전체 ' + shown + '개';
            })
            .catch(function () {
                if (mine !== countSeq) return;
                el.count.textContent = '';
            });
    }

    // ---------------------------------------------------------------- 카드

    function buildCard(m) {
        var li = document.createElement('li');
        li.className = 'memo-card'
            + (m.pinned ? ' pinned' : '')
            + (m.locked ? ' locked' : '')
            + (m.encrypted ? ' encrypted' : '');
        li.id = 'card-' + m.id;
        li.dataset.id = m.id;
        li.dataset.like = String(m.like || 0);
        // ---- 보기 모드
        var view = document.createElement('div');
        view.className = 'view';

        var title = document.createElement('h3');
        title.className = 'title';
        if (m.pinned) {
            var pin = document.createElement('span');
            pin.setAttribute('aria-hidden', 'true');
            pin.textContent = '📌 ';
            title.appendChild(pin);
            var sr = document.createElement('span');
            sr.className = 'visually-hidden';
            sr.textContent = '고정됨. ';
            title.appendChild(sr);
        }
        title.appendChild(document.createTextNode(m.title));
        highlightIn(title, state.q);

        var content = document.createElement('div');
        content.className = 'content';
        if (m.locked) {
            content.appendChild(lockedNotice(m));
        } else if (m.encrypted) {
            content.appendChild(encryptedNotice(li, m));
        } else {
            content.innerHTML = renderMarkdown(m.content);
        }
        hardenContent(content);
        if (!m.locked && !m.encrypted) {
            linkifyWiki(content);
            highlightIn(content, state.q);
        }

        var tags = document.createElement('ul');
        tags.className = 'tags';
        (m.tags || []).forEach(function (t) {
            var item = document.createElement('li');
            item.appendChild(tagChip(t, null, t === state.tag));
            tags.appendChild(item);
        });

        var meta = document.createElement('p');
        meta.className = 'meta';
        meta.appendChild(metaText(m));

        var buttons = document.createElement('div');
        buttons.className = 'buttons';
        var editable = canEdit(m);

        if (editable) {
            buttons.appendChild(cardButton('수정', 'btn-primary', 'edit', m, function () {
                if (m.locked) {
                    toast('열릴 때가 되어야 고칠 수 있습니다.', 'error');
                    return;
                }
                if (m.encrypted) {
                    toast('잠긴 메모는 고칠 수 없습니다. 지우고 새로 쓰세요.', 'error');
                    return;
                }
                startEdit(li);
            }));
            buttons.appendChild(cardButton('삭제', 'btn-danger', 'delete', m, function () {
                deleteMemo(li);
            }));
        }
        buttons.appendChild(cardButton('좋아요 🔥', 'btn-quiet', 'like', m, function () {
            likeMemo(li);
        }));
        if (editable) {
            buttons.appendChild(cardButton(
                m.pinned ? '고정 해제' : '고정', 'btn-pin', 'pin', m, function () {
                    pinMemo(li);
                }));
            buttons.appendChild(cardButton('더보기', 'btn-quiet', 'more', m, function () {
                openMemoMenu(li, m);
            }));
        } else {
            var note = document.createElement('span');
            note.className = 'hint';
            note.textContent = '다른 사람이 쓴 메모입니다';
            buttons.appendChild(note);
        }

        view.append(title, content, tags, meta, buttons);
        li.appendChild(view);

        // 잠긴 메모는 편집기를 열지 않는다. 암호문을 그대로 고치면 잠금이
        // 풀려 버리고, 타임캡슐은 내용이 비어 있어 원본이 지워진다.
        if (editable && !m.locked && !m.encrypted) li.appendChild(buildEditor(li, m));
        return li;
    }

    function buildEditor(card, m) {
        var edit = document.createElement('div');
        edit.className = 'edit hidden';
        edit.appendChild(editField('제목', 'input', 'edit-title', m.title, MAX_TITLE, m.id));
        edit.appendChild(editField('내용', 'textarea', 'edit-content', m.content, MAX_CONTENT, m.id));
        edit.appendChild(editField(
            '태그 (쉼표로 구분, 최대 ' + MAX_TAGS + '개)', 'input', 'edit-tags',
            (m.tags || []).join(', '), 200, m.id));

        var editError = document.createElement('p');
        editError.className = 'field-error';
        editError.id = 'edit-error-' + m.id;      // aria-describedby 가 가리킬 대상
        editError.setAttribute('role', 'alert');
        edit.appendChild(editError);

        var save = document.createElement('button');
        save.type = 'button';
        save.className = 'btn btn-sm btn-success';
        save.textContent = '저장';
        save.addEventListener('click', function () { submitEdit(card); });

        var cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'btn btn-sm btn-secondary edit-cancel';
        cancel.textContent = '취소';
        cancel.addEventListener('click', function () { cancelEdit(card); });

        edit.append(save, cancel);
        edit.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') cancelEdit(card);
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submitEdit(card);
        });
        return edit;
    }

    /** 아직 열릴 때가 안 된 타임캡슐 메모. 서버가 내용을 아예 안 보낸다. */
    function lockedNotice(m) {
        var box = document.createElement('p');
        box.className = 'locked-note';
        var when = m.open_at ? new Date(m.open_at) : null;
        box.textContent = when && !Number.isNaN(when.getTime())
            ? '⏳ ' + formatDate(m.open_at) + ' 에 열립니다.'
            : '⏳ 아직 열 수 없는 메모입니다.';
        return box;
    }

    /** 브라우저에서 암호화된 메모. 비밀번호를 받아 그 자리에서 푼다. */
    function encryptedNotice(card, m) {
        var box = document.createElement('div');

        var line = document.createElement('p');
        line.className = 'locked-note';
        line.textContent = '🔒 잠긴 메모입니다. 서버도 내용을 알지 못합니다.';
        box.appendChild(line);

        var field = document.createElement('div');
        field.className = 'field';
        var label = document.createElement('label');
        label.setAttribute('for', 'unlock-' + m.id);
        label.className = 'visually-hidden';
        label.textContent = '비밀번호';
        var input = document.createElement('input');
        input.id = 'unlock-' + m.id;
        input.type = 'password';
        input.className = 'form-control search-field';
        input.autocomplete = 'off';
        input.placeholder = '비밀번호';
        field.append(label, input);

        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm btn-quiet';
        button.textContent = '열기';

        var errorEl = document.createElement('p');
        errorEl.className = 'field-error';
        errorEl.setAttribute('role', 'alert');

        function attempt() {
            if (!input.value) {
                errorEl.textContent = '비밀번호를 입력하세요.';
                input.focus();
                return;
            }
            button.disabled = true;
            window.MemoCrypto.decrypt(m.content, input.value)
                .then(function (plain) {
                    var shown = document.createElement('div');
                    shown.innerHTML = renderMarkdown(plain);
                    hardenContent(shown);
                    linkifyWiki(shown);
                    box.replaceWith(shown);
                })
                .catch(function (err) {
                    errorEl.textContent = err.message;
                    input.select();
                })
                .finally(function () { button.disabled = false; });
        }

        button.addEventListener('click', attempt);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') attempt();
        });

        box.append(field, button, errorEl);
        return box;
    }

    function hardenContent(root) {
        root.querySelectorAll('a[href]').forEach(function (a) {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer nofollow ugc');
        });
        root.querySelectorAll('img').forEach(function (img) {
            img.setAttribute('loading', 'lazy');
            img.setAttribute('decoding', 'async');
        });
    }

    function metaText(m) {
        var parts = ['작성 ' + formatDate(m.created_at)];
        if (m.updated_at) parts.push('수정 ' + formatDate(m.updated_at));
        parts.push('좋아요 ' + (m.like || 0));
        if (m.links && m.links.length) parts.push('연결 ' + m.links.length);
        if (m.revisions) parts.push('이력 ' + m.revisions);
        if (m.shared) parts.push('공유 중');
        return document.createTextNode(parts.join(' · '));
    }

    function cardButton(label, variant, action, memo, handler) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm ' + variant;
        btn.textContent = label;
        btn.dataset.action = action;
        // 같은 이름의 버튼이 화면에 수십 개 있으므로 어느 메모인지 이름에 담는다.
        btn.setAttribute('aria-label', label.replace(' 🔥', '') + ': ' + memo.title);
        btn.addEventListener('click', handler);
        return btn;
    }

    function editField(labelText, tag, cls, value, maxLength, memoId) {
        var wrap = document.createElement('div');
        wrap.className = 'field';
        var id = cls + '-' + memoId;

        var label = document.createElement('label');
        label.setAttribute('for', id);
        label.textContent = labelText;

        var input = document.createElement(tag);
        input.id = id;
        input.className = 'form-control ' + cls;
        input.maxLength = maxLength;
        if (tag === 'textarea') input.rows = 5;
        input.value = value;

        wrap.append(label, input);
        return wrap;
    }

    function tagChip(name, count, active) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'chip';
        btn.textContent = '#' + name + (count != null ? ' ' + count : '');
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        btn.setAttribute(
            'aria-label',
            count != null
                ? '태그 필터: ' + name + ' (메모 ' + count + '개)'
                : '태그 필터: ' + name
        );
        btn.addEventListener('click', function () { selectTag(name); });
        return btn;
    }

    // ------------------------------------------------------- 포커스 유지

    function restoreFocus(token) {
        if (!token) return;
        var card = document.getElementById('card-' + token.id);
        var target = card && card.querySelector('[data-action="' + token.action + '"]');
        if (target) {
            target.focus();
            return;
        }
        // 카드가 사라졌으면 목록 자체로 포커스를 보낸다.
        el.cards.focus();
    }

    // ---------------------------------------------------------------- 태그

    function loadTagBar() {
        var mine = ++tagSeq;
        request('GET', '/tags')
            .then(function (data) {
                if (mine !== tagSeq) return;
                var tags = data.tags || [];
                el.tagbar.replaceChildren();
                if (!tags.length) return;

                var allItem = document.createElement('li');
                var all = document.createElement('button');
                all.type = 'button';
                all.className = 'chip';
                all.textContent = '전체';
                all.setAttribute('aria-pressed', state.tag === '' ? 'true' : 'false');
                all.setAttribute('aria-label', '태그 필터 해제');
                all.addEventListener('click', function () { selectTag(''); });
                allItem.appendChild(all);
                el.tagbar.appendChild(allItem);

                tags.forEach(function (t) {
                    var item = document.createElement('li');
                    item.appendChild(tagChip(t.tag, t.count, t.tag === state.tag));
                    el.tagbar.appendChild(item);
                });
            })
            .catch(function () {
                if (mine !== tagSeq) return;
                el.tagbar.replaceChildren(
                    errorRow('li', '태그를 불러오지 못했습니다.', loadTagBar));
            });
    }

    function selectTag(tag) {
        state.tag = (state.tag === tag) ? '' : tag;
        reload({ push: true });      // 뒤로가기로 되돌아올 수 있게 한다
        loadTagBar();
    }

    // ---------------------------------------------------------------- 검증

    function setFieldError(input, errorEl, message) {
        if (message) {
            input.setAttribute('aria-invalid', 'true');
            input.setAttribute('aria-describedby', errorEl.id || '');
            errorEl.textContent = message;
            input.focus();
            return false;
        }
        input.removeAttribute('aria-invalid');
        errorEl.textContent = '';
        return true;
    }

    function validateInputs(titleEl, contentEl, tagsEl, errorEl) {
        var title = titleEl.value.trim();
        var content = contentEl.value.trim();
        var tags = (tagsEl ? tagsEl.value : '').split(',')
            .map(function (t) { return t.trim(); })
            .filter(Boolean);

        if (!title) return setFieldError(titleEl, errorEl, '제목을 입력하세요.');
        if (title.length > MAX_TITLE) {
            return setFieldError(titleEl, errorEl, '제목은 ' + MAX_TITLE + '자 이하로 입력하세요.');
        }
        if (!content) return setFieldError(contentEl, errorEl, '내용을 입력하세요.');
        if (content.length > MAX_CONTENT) {
            return setFieldError(contentEl, errorEl, '내용은 ' + MAX_CONTENT + '자 이하로 입력하세요.');
        }
        if (tagsEl) {
            if (tags.length > MAX_TAGS) {
                return setFieldError(tagsEl, errorEl, '태그는 최대 ' + MAX_TAGS + '개까지 쓸 수 있습니다.');
            }
            var tooLong = tags.filter(function (t) { return t.length > MAX_TAG_LEN; });
            if (tooLong.length) {
                return setFieldError(
                    tagsEl, errorEl, '태그는 ' + MAX_TAG_LEN + '자 이하로 입력하세요: ' + tooLong[0]);
            }
            setFieldError(tagsEl, errorEl, null);
        }
        setFieldError(titleEl, errorEl, null);
        setFieldError(contentEl, errorEl, null);
        return true;
    }

    // ---------------------------------------------------------------- 쓰기

    function saveMemo() {
        if (!validateInputs(el.title, el.content, el.tags, el.writerError)) return;
        if (el.saveBtn.disabled) return;

        var passphrase = el.lockPass ? el.lockPass.value : '';
        var content = el.content.value.trim();

        // 암호문은 base64 라 원문보다 길어진다. 서버 한도는 암호문 기준이므로
        // 여기서 미리 막지 않으면 "2000자 이하" 라는 엉뚱한 오류를 보게 된다.
        if (passphrase && content.length > LOCKED_MAX_CONTENT) {
            setFieldError(
                el.content,
                el.writerError,
                '잠근 메모는 ' + LOCKED_MAX_CONTENT + '자까지 쓸 수 있습니다.'
            );
            return;
        }

        el.saveBtn.disabled = true;

        // 잠금을 켜면 브라우저에서 먼저 암호화한 뒤 그 결과만 보낸다.
        var prepared = passphrase
            ? window.MemoCrypto.encrypt(content, passphrase)
            : Promise.resolve(content);

        prepared
            .then(function (payload) {
                return request('POST', '/memo', {
                    title_give: el.title.value.trim(),
                    content_give: payload,
                    tags_give: el.tags.value.trim(),
                });
            })
            .then(function (data) {
                // 서버가 준 소유 토큰을 보관해야 이후 수정·삭제를 할 수 있다.
                if (data.owner_token) rememberToken(data.id, data.owner_token);
                el.title.value = '';
                el.content.value = '';
                el.tags.value = '';
                storage('remove', DRAFT_KEY);
                if (el.lockPass) {
                    el.lockPass.value = '';
                    el.lockToggle.checked = false;
                    el.lockField.classList.add('hidden');
                }
                updateCharCount();
                el.title.focus();
                toast('메모를 저장했습니다.');
                reload();
                loadTagBar();
            })
            .catch(showError)
            .finally(function () { el.saveBtn.disabled = false; });
    }

    /** 편집 중인 카드가 있으면 목록 갱신 전에 사용자에게 알린다. */
    function hasOpenEditor() {
        return Boolean(el.cards.querySelector('.edit:not(.hidden)'));
    }

    function startEdit(card) {
        card.querySelector('.view').classList.add('hidden');
        card.querySelector('.edit').classList.remove('hidden');
        card.querySelector('.edit-title').focus();
    }

    function cancelEdit(card) {
        card.querySelector('.edit').classList.add('hidden');
        card.querySelector('.view').classList.remove('hidden');
        var btn = card.querySelector('[data-action="edit"]');
        if (btn) btn.focus();
    }

    function submitEdit(card) {
        var titleEl = card.querySelector('.edit-title');
        var contentEl = card.querySelector('.edit-content');
        var tagsEl = card.querySelector('.edit-tags');
        var errorEl = card.querySelector('.edit .field-error');
        if (!validateInputs(titleEl, contentEl, tagsEl, errorEl)) return;

        var saveBtn = card.querySelector('.edit .btn-success');
        if (saveBtn.disabled) return;
        saveBtn.disabled = true;

        request('POST', '/memo/update', {
            id_give: card.dataset.id,
            title_give: titleEl.value.trim(),
            content_give: contentEl.value.trim(),
            tags_give: tagsEl.value.trim(),
        })
            .then(function (data) {
                // 서버가 돌려준 최신 메모로 해당 카드만 갈아 끼운다.
                var fresh = buildCard(data.memo);
                card.replaceWith(fresh);
                var btn = fresh.querySelector('[data-action="edit"]');
                if (btn) btn.focus();
                toast('메모를 수정했습니다.');
                loadTagBar();
            })
            .catch(function (err) {
                saveBtn.disabled = false;
                showError(err);
            });
    }

    function deleteMemo(card) {
        var id = card.dataset.id;
        var btn = card.querySelector('[data-action="delete"]');
        if (btn.disabled) return;
        btn.disabled = true;

        var next = card.nextElementSibling || card.previousElementSibling;

        request('POST', '/memo/delete', { id_give: id })
            .then(function () {
                card.remove();
                state.loaded = Math.max(0, state.loaded - 1);
                state.shift += 1;        // 다음 페이지가 한 칸 당겨진 것을 보정
                refreshCount();
                loadTagBar();
                announce('메모를 삭제했습니다. 남은 메모 ' + state.loaded + '개.');

                var undo = toast('메모를 삭제했습니다.', null, {
                    label: '실행 취소',
                    run: function () { restoreMemo(id); },
                });
                // 실행 취소 버튼으로 바로 갈 수 있어야 키보드로도 쓸 수 있다.
                if (undo && undo.action) {
                    undo.action.focus();
                    undo.onDismiss = function () { focusAfterDelete(next); };
                } else {
                    focusAfterDelete(next);
                }
            })
            .catch(function (err) {
                btn.disabled = false;
                showError(err);
            });
    }

    function focusAfterDelete(next) {
        if (next && next.isConnected) {
            var btn = next.querySelector('[data-action="like"]');
            if (btn) { btn.focus(); return; }
        }
        el.cards.focus();
    }

    /** 삭제한 메모를 그대로 되살린다. 좋아요와 작성 시각이 유지된다. */
    function restoreMemo(id) {
        request('POST', '/memo/restore', { id_give: id })
            .then(function () {
                toast('메모를 되살렸습니다.');
                state.shift = Math.max(0, state.shift - 1);
                reload();
                loadTagBar();
            })
            .catch(showError);
    }

    function likeMemo(card) {
        var id = card.dataset.id;
        var btn = card.querySelector('[data-action="like"]');
        if (btn.disabled) return;
        btn.disabled = true;

        var meta = card.querySelector('.meta');
        var before = meta.textContent;
        var optimistic = Number(card.dataset.like) + 1;

        // 즉시 반영하고, 실패하면 되돌린다.
        meta.textContent = before.replace(/좋아요 \d+/, '좋아요 ' + optimistic);
        card.dataset.like = String(optimistic);

        request('POST', '/memo/like', { id_give: id })
            .then(function (data) {
                card.dataset.like = String(data.like);
                meta.textContent = before.replace(/좋아요 \d+/, '좋아요 ' + data.like);
            })
            .catch(function (err) {
                meta.textContent = before;
                card.dataset.like = String(optimistic - 1);
                showError(err);
            })
            .finally(function () { btn.disabled = false; });
    }

    function pinMemo(card) {
        var id = card.dataset.id;
        var btn = card.querySelector('[data-action="pin"]');
        if (btn.disabled) return;
        btn.disabled = true;

        request('POST', '/memo/pin', { id_give: id })
            .then(function (data) {
                toast(data.msg);
                // 고정은 정렬 순서를 바꾸므로 목록을 다시 읽되 포커스는 되돌린다.
                reload({ focusAfter: { id: id, action: 'pin' } });
            })
            .catch(function (err) {
                btn.disabled = false;
                showError(err);
            });
    }

    // ------------------------------------------------- 메모별 부가 기능

    function openMemoMenu(card, memo) {
        window.MemoMenu.open(memo);
    }

    function restoreRevision(memoId, revisionId) {
        request('POST', '/memo/revision/restore', {
            id_give: memoId,
            revision_give: revisionId,
        })
            .then(function () {
                toast('예전 내용으로 되돌렸습니다.');
                reload();
                loadTagBar();
            })
            .catch(showError);
    }

    function startWriting(title) {
        el.title.value = title;
        el.content.focus();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function focusWriter() {
        el.title.focus();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // ---------------------------------------------------------------- 초안

    function saveDraft() {
        // 잠금 메모는 암호화 전 평문이 디스크에 남으면 안 된다.
        if (el.lockToggle && el.lockToggle.checked) {
            storage('remove', DRAFT_KEY);
            return;
        }
        var draft = {
            title: el.title.value, content: el.content.value, tags: el.tags.value,
        };
        if (!draft.title && !draft.content && !draft.tags) {
            storage('remove', DRAFT_KEY);
            return;
        }
        storage('set', DRAFT_KEY, JSON.stringify(draft));
    }

    function restoreDraft() {
        var raw = storage('get', DRAFT_KEY);
        if (!raw) return;
        var draft;
        try { draft = JSON.parse(raw); } catch (e) { return; }
        if (!draft || (!draft.title && !draft.content)) return;

        toast('작성 중이던 글이 있습니다.', null, {
            label: '이어쓰기',
            run: function () {
                el.title.value = draft.title || '';
                el.content.value = draft.content || '';
                el.tags.value = draft.tags || '';
                updateCharCount();
                el.title.focus();
            },
        });
    }

    // ---------------------------------------------------------------- 기타

    function updateCharCount() {
        var length = el.content.value.length;
        el.charCount.textContent = length + ' / ' + MAX_CONTENT;
        el.charCount.classList.toggle('over', length >= MAX_CONTENT);
    }

    function toggleTheme() {
        var root = document.documentElement;
        var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        root.setAttribute('data-bs-theme', next);
        storage('set', 'theme', next);
        applyThemeButton(next);
        // 모바일 주소창 색도 함께 바꾼다. 미디어 쿼리만으로는 수동 전환을 못 따라온다.
        document.querySelectorAll('meta[name="theme-color"]').forEach(function (tag) {
            tag.removeAttribute('media');
            tag.setAttribute('content', next === 'dark' ? '#16181c' : '#ffffff');
        });
    }

    function applyThemeButton(theme) {
        el.themeBtn.textContent = theme === 'dark' ? '☀️ 라이트 모드' : '🌙 다크 모드';
        el.themeBtn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
    }

    /** 검색·태그·정렬 상태를 주소창에 반영해 새로고침과 공유가 되게 한다. */
    function syncUrl(push) {
        var params = new URLSearchParams();
        if (state.q) params.set('q', state.q);
        if (state.tag) params.set('tag', state.tag);
        if (state.sort !== 'newest') params.set('sort', state.sort);
        var query = params.toString();
        var url = query ? '?' + query : window.location.pathname;
        if (url === window.location.search || (!query && !window.location.search)) return;
        // 사용자가 명시적으로 고른 조건은 기록에 남기고, 타이핑 중 검색은 덮어쓴다.
        if (push) {
            window.history.pushState(null, '', url);
        } else {
            window.history.replaceState(null, '', url);
        }
    }

    function readUrl() {
        var params = new URLSearchParams(window.location.search);
        var sort = params.get('sort') || 'newest';
        state.q = params.get('q') || '';
        state.tag = params.get('tag') || '';
        state.sort = SORT_VALUES.indexOf(sort) === -1 ? 'newest' : sort;
        el.search.value = state.q;
        el.sort.value = state.sort;
    }

    // ---------------------------------------------------------------- 시작

    function init() {
        el = {
            title: $('title'), content: $('content'), tags: $('tags'),
            saveBtn: $('save-btn'), writerError: $('writer-error'),
            charCount: $('char-count'), search: $('search'), sort: $('sort'),
            count: $('count'), tagbar: $('tagbar'), cards: $('cards'),
            moreBtn: $('more-btn'), themeBtn: $('theme-toggle'),
            toasts: $('toasts'), alerts: $('alerts'), status: $('status'),
            adminInput: $('admin-token'), adminSave: $('admin-save'),
            lockToggle: $('lock-toggle'), lockField: $('lock-field'), lockPass: $('lock-pass'),
            graphBtn: $('graph-btn'), activityBtn: $('activity-btn'),
        };

        if (window.marked) {
            window.marked.setOptions({ breaks: true, gfm: true });
        }

        applyThemeButton(document.documentElement.getAttribute('data-theme'));
        el.themeBtn.addEventListener('click', toggleTheme);

        el.saveBtn.addEventListener('click', saveMemo);
        el.moreBtn.addEventListener('click', loadMore);

        el.search.addEventListener('input', function () {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(function () {
                if (hasOpenEditor()
                    && !window.confirm('수정 중인 메모가 있습니다. 목록을 새로 불러올까요?')) {
                    return;
                }
                reload();
            }, 250);
        });
        el.sort.addEventListener('change', function () { reload({ push: true }); });

        [el.title, el.content, el.tags].forEach(function (input) {
            input.addEventListener('keydown', function (e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') saveMemo();
            });
            input.addEventListener('input', function () {
                window.clearTimeout(draftTimer);
                draftTimer = window.setTimeout(saveDraft, 500);
            });
        });
        el.content.addEventListener('input', updateCharCount);

        window.addEventListener('popstate', function () {
            readUrl();
            reload();
            loadTagBar();
        });

        if (el.adminSave) {
            el.adminInput.value = adminToken();
            el.adminSave.addEventListener('click', function () {
                var value = el.adminInput.value.trim();
                if (value) {
                    storage('set', ADMIN_KEY, value);
                    toast('관리자 토큰을 저장했습니다.');
                } else {
                    storage('remove', ADMIN_KEY);
                    toast('관리자 토큰을 지웠습니다.');
                }
                reload();
            });
        }

        // 부가 화면(팔레트, 그래프, 이력)과 메모 메뉴에 공용 동작을 넘긴다.
        var bridge = {
            request: request,
            toast: toast,
            showError: showError,
            reload: function () { reload(); },
            focusMemo: focusMemo,
            focusWriter: focusWriter,
            startWriting: startWriting,
            restoreRevision: restoreRevision,
        };
        window.MemoPanels.init(bridge);
        window.MemoMenu.init(bridge);

        if (el.graphBtn) {
            el.graphBtn.addEventListener('click', window.MemoPanels.openGraph);
        }
        if (el.activityBtn) {
            el.activityBtn.addEventListener('click', window.MemoPanels.openActivity);
        }
        if (el.lockToggle) {
            el.lockToggle.addEventListener('change', function () {
                el.lockField.classList.toggle('hidden', !el.lockToggle.checked);
                if (el.lockToggle.checked) el.lockPass.focus();
                else el.lockPass.value = '';
            });
            if (!window.MemoCrypto.available()) {
                el.lockToggle.disabled = true;
                el.lockToggle.parentElement.title = '이 브라우저는 잠금 메모를 지원하지 않습니다.';
            }
        }

        readUrl();
        updateCharCount();
        restoreDraft();
        reload();
        loadTagBar();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
