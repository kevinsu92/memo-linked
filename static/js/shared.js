/** 공유 링크로 연 읽기 전용 화면. */
(function () {
    'use strict';

    function render(memo) {
        var titleEl = document.getElementById('shared-title');
        var contentEl = document.getElementById('shared-content');
        var tagsEl = document.getElementById('shared-tags');
        var metaEl = document.getElementById('shared-meta');

        titleEl.textContent = memo.title;

        (memo.tags || []).forEach(function (tag) {
            var li = document.createElement('li');
            var span = document.createElement('span');
            span.className = 'chip';
            span.textContent = '#' + tag;
            li.appendChild(span);
            tagsEl.appendChild(li);
        });

        if (memo.created_at) {
            var date = new Date(memo.created_at);
            metaEl.textContent = Number.isNaN(date.getTime())
                ? ''
                : '작성 ' + date.toLocaleString(document.documentElement.lang || 'ko-KR');
        }

        if (memo.locked) {
            contentEl.textContent = memo.open_at
                ? new Date(memo.open_at).toLocaleString(document.documentElement.lang || 'ko-KR')
                    + ' 에 열립니다.'
                : '아직 열 수 없는 메모입니다.';
            return;
        }

        if (memo.encrypted) {
            document.getElementById('unlock').classList.remove('hidden');
            contentEl.textContent = '비밀번호를 입력하면 내용이 보입니다.';
            wireUnlock(memo, contentEl);
            return;
        }
        contentEl.innerHTML = renderMarkdown(memo.content);
        hardenLinks(contentEl);
    }

    function wireUnlock(memo, contentEl) {
        var input = document.getElementById('shared-pass');
        var button = document.getElementById('unlock-btn');
        var errorEl = document.getElementById('unlock-error');

        function attempt() {
            var passphrase = input.value;
            if (!passphrase) {
                errorEl.textContent = '비밀번호를 입력하세요.';
                input.focus();
                return;
            }
            button.disabled = true;
            errorEl.textContent = '';

            window.MemoCrypto.decrypt(memo.content, passphrase)
                .then(function (plain) {
                    contentEl.innerHTML = renderMarkdown(plain);
                    hardenLinks(contentEl);
                    document.getElementById('unlock').classList.add('hidden');
                })
                .catch(function (err) {
                    errorEl.textContent = err.message;
                    input.focus();
                    input.select();
                })
                .finally(function () {
                    button.disabled = false;
                });
        }

        button.addEventListener('click', attempt);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') attempt();
        });
        input.focus();
    }

    function renderMarkdown(text) {
        if (!window.marked || !window.DOMPurify) {
            var p = document.createElement('p');
            p.className = 'plain-fallback';
            p.textContent = text;
            return p.outerHTML;
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

    function hardenLinks(root) {
        root.querySelectorAll('a[href]').forEach(function (a) {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer nofollow ugc');
        });
    }

    function init() {
        var node = document.getElementById('shared-memo');
        if (!node) return;
        var memo;
        try {
            memo = JSON.parse(node.textContent);
        } catch (e) {
            return;
        }
        if (window.marked) window.marked.setOptions({ breaks: true, gfm: true });
        render(memo);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
