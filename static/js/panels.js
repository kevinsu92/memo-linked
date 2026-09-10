/**
 * 부가 화면: 명령 팔레트, 연결 그래프, 활동 히트맵, 수정 이력.
 *
 * app.js 가 window.MemoPanels 를 통해 부른다. 파일을 나눠 두면 각 화면을
 * 따로 읽고 고칠 수 있다.
 */
window.MemoPanels = (function () {
    'use strict';

    var api = null; // app.js 가 넘겨주는 공용 함수 모음

    function init(bridge) {
        api = bridge;
        wirePalette();
    }

    // ------------------------------------------------------------ 공통

    function dialog(titleText) {
        var back = document.createElement('div');
        back.className = 'overlay';

        var box = document.createElement('div');
        box.className = 'panel';
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        box.setAttribute('aria-label', titleText);

        var head = document.createElement('div');
        head.className = 'panel-head';
        var heading = document.createElement('h2');
        heading.textContent = titleText;
        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn btn-sm btn-quiet';
        close.textContent = '닫기';
        head.append(heading, close);

        var body = document.createElement('div');
        body.className = 'panel-body';

        box.append(head, body);
        back.appendChild(box);
        document.body.appendChild(back);

        var opener = document.activeElement;

        function dismiss() {
            back.remove();
            document.removeEventListener('keydown', onKey);
            if (opener && opener.focus) opener.focus();
        }

        function onKey(e) {
            if (e.key === 'Escape') dismiss();
            if (e.key !== 'Tab') return;
            // 포커스가 대화상자 밖으로 나가지 않게 가둔다.
            var items = box.querySelectorAll(
                'button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])'
            );
            if (!items.length) return;
            var first = items[0];
            var last = items[items.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }

        close.addEventListener('click', dismiss);
        back.addEventListener('mousedown', function (e) {
            if (e.target === back) dismiss();
        });
        document.addEventListener('keydown', onKey);

        return { root: back, body: body, close: dismiss };
    }

    // ------------------------------------------------- 명령 팔레트 (Ctrl+K)

    function wirePalette() {
        document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                openPalette();
            }
        });
    }

    function openPalette() {
        if (document.querySelector('.palette')) return;

        var back = document.createElement('div');
        back.className = 'overlay palette-back';
        var box = document.createElement('div');
        box.className = 'panel palette';
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        box.setAttribute('aria-label', '메모 찾아가기');

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'form-control';
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-expanded', 'true');
        input.setAttribute('aria-controls', 'palette-list');
        input.setAttribute('aria-autocomplete', 'list');
        input.placeholder = '메모 제목으로 이동, 또는 명령 (그래프, 활동, 새 메모)';

        var list = document.createElement('ul');
        list.className = 'palette-list';
        list.id = 'palette-list';
        list.setAttribute('role', 'listbox');

        box.append(input, list);
        back.appendChild(box);
        document.body.appendChild(back);

        var opener = document.activeElement;
        var items = [];
        var cursor = 0;
        var timer = null;

        function dismiss() {
            back.remove();
            document.removeEventListener('keydown', onKey, true);
            if (opener && opener.focus) opener.focus();
        }

        var COMMANDS = [
            { label: '연결 그래프 보기', run: function () { dismiss(); openGraph(); } },
            { label: '활동 기록 보기', run: function () { dismiss(); openActivity(); } },
            {
                label: '새 메모 쓰기',
                run: function () {
                    dismiss();
                    api.focusWriter();
                },
            },
        ];

        function draw(memos, keyword) {
            list.replaceChildren();
            items = [];

            COMMANDS.filter(function (c) {
                return !keyword || c.label.indexOf(keyword) !== -1;
            }).forEach(function (command) {
                items.push({ label: '⌘ ' + command.label, run: command.run });
            });

            memos.forEach(function (memo) {
                items.push({
                    label: memo.title,
                    hint: (memo.tags || []).map(function (t) { return '#' + t; }).join(' '),
                    run: function () {
                        dismiss();
                        api.focusMemo(memo.id);
                    },
                });
            });

            if (!items.length) {
                var empty = document.createElement('li');
                empty.className = 'palette-empty';
                empty.textContent = '결과가 없습니다.';
                list.appendChild(empty);
                return;
            }

            items.forEach(function (item, index) {
                var li = document.createElement('li');
                li.className = 'palette-item' + (index === cursor ? ' active' : '');
                li.setAttribute('role', 'option');
                li.setAttribute('aria-selected', index === cursor ? 'true' : 'false');
                li.id = 'palette-item-' + index;

                var label = document.createElement('span');
                label.textContent = item.label;
                li.appendChild(label);

                if (item.hint) {
                    var hint = document.createElement('span');
                    hint.className = 'palette-hint';
                    hint.textContent = item.hint;
                    li.appendChild(hint);
                }

                li.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    item.run();
                });
                list.appendChild(li);
            });
            input.setAttribute('aria-activedescendant', 'palette-item-' + cursor);
        }

        function search() {
            var keyword = input.value.trim();
            api.request('GET', '/search/quick?q=' + encodeURIComponent(keyword))
                .then(function (data) {
                    cursor = 0;
                    draw(data.memos || [], keyword);
                })
                .catch(function () { draw([], keyword); });
        }

        function move(delta) {
            if (!items.length) return;
            cursor = (cursor + delta + items.length) % items.length;
            var nodes = list.querySelectorAll('.palette-item');
            nodes.forEach(function (node, index) {
                node.classList.toggle('active', index === cursor);
                node.setAttribute('aria-selected', index === cursor ? 'true' : 'false');
            });
            input.setAttribute('aria-activedescendant', 'palette-item-' + cursor);
            if (nodes[cursor]) nodes[cursor].scrollIntoView({ block: 'nearest' });
        }

        function onKey(e) {
            if (e.key === 'Escape') { e.preventDefault(); dismiss(); }
            if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
            if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
            if (e.key === 'Enter' && items[cursor]) { e.preventDefault(); items[cursor].run(); }
        }

        input.addEventListener('input', function () {
            window.clearTimeout(timer);
            timer = window.setTimeout(search, 150);
        });
        document.addEventListener('keydown', onKey, true);
        back.addEventListener('mousedown', function (e) {
            if (e.target === back) dismiss();
        });

        input.focus();
        search();
    }

    // ------------------------------------------------------- 연결 그래프

    function openGraph() {
        var panel = dialog('메모 연결 그래프');
        panel.body.textContent = '불러오는 중...';

        api.request('GET', '/graph')
            .then(function (data) {
                panel.body.replaceChildren();
                if (!data.nodes.length) {
                    panel.body.appendChild(note('아직 메모가 없습니다.'));
                    return;
                }
                if (!data.edges.length) {
                    panel.body.appendChild(
                        note('아직 연결이 없습니다. 내용에 [[다른 메모 제목]] 을 적어 보세요.')
                    );
                }
                panel.body.appendChild(drawGraph(data, panel.close));
            })
            .catch(function (err) {
                panel.body.replaceChildren(note(err.message));
            });
    }

    function note(text) {
        var p = document.createElement('p');
        p.className = 'hint';
        p.textContent = text;
        return p;
    }

    /**
     * 연결 그래프를 SVG 로 그린다.
     *
     * 물리 시뮬레이션을 애니메이션 없이 정해진 횟수만 돌려 좌표를 정한다.
     * 결과가 매번 같고 저사양 기기에서도 부담이 없다.
     */
    function drawGraph(data, closePanel) {
        var W = 640;
        var H = 420;
        var nodes = data.nodes.map(function (n, i) {
            var angle = (i / data.nodes.length) * Math.PI * 2;
            return {
                id: n.id,
                title: n.title,
                like: n.like,
                degree: 0,
                x: W / 2 + Math.cos(angle) * 150,
                y: H / 2 + Math.sin(angle) * 150,
            };
        });
        var index = {};
        nodes.forEach(function (n) { index[n.id] = n; });

        var edges = data.edges.filter(function (e) {
            return index[e.from] && index[e.to];
        });
        edges.forEach(function (e) {
            index[e.from].degree += 1;
            index[e.to].degree += 1;
        });

        for (var step = 0; step < 220; step++) {
            // 서로 밀어내기
            for (var i = 0; i < nodes.length; i++) {
                for (var j = i + 1; j < nodes.length; j++) {
                    var a = nodes[i];
                    var b = nodes[j];
                    var dx = a.x - b.x;
                    var dy = a.y - b.y;
                    var dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
                    var push = 900 / (dist * dist);
                    a.x += (dx / dist) * push;
                    a.y += (dy / dist) * push;
                    b.x -= (dx / dist) * push;
                    b.y -= (dy / dist) * push;
                }
            }
            // 연결된 것끼리 당기기
            edges.forEach(function (e) {
                var a = index[e.from];
                var b = index[e.to];
                var dx = b.x - a.x;
                var dy = b.y - a.y;
                var dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
                var pull = (dist - 90) * 0.01;
                a.x += (dx / dist) * pull;
                a.y += (dy / dist) * pull;
                b.x -= (dx / dist) * pull;
                b.y -= (dy / dist) * pull;
            });
            // 화면 안으로
            nodes.forEach(function (n) {
                n.x = Math.max(40, Math.min(W - 40, n.x));
                n.y = Math.max(30, Math.min(H - 30, n.y));
            });
        }

        var NS = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
        svg.setAttribute('class', 'graph');
        svg.setAttribute('role', 'img');
        svg.setAttribute(
            'aria-label',
            '메모 ' + nodes.length + '개와 연결 ' + edges.length + '개를 나타낸 그래프'
        );

        edges.forEach(function (e) {
            var line = document.createElementNS(NS, 'line');
            line.setAttribute('x1', index[e.from].x);
            line.setAttribute('y1', index[e.from].y);
            line.setAttribute('x2', index[e.to].x);
            line.setAttribute('y2', index[e.to].y);
            line.setAttribute('class', 'graph-edge');
            svg.appendChild(line);
        });

        nodes.forEach(function (n) {
            var group = document.createElementNS(NS, 'g');
            group.setAttribute('class', 'graph-node');
            group.setAttribute('tabindex', '0');
            group.setAttribute('role', 'button');
            group.setAttribute('aria-label', n.title + ', 연결 ' + n.degree + '개');

            var circle = document.createElementNS(NS, 'circle');
            circle.setAttribute('cx', n.x);
            circle.setAttribute('cy', n.y);
            circle.setAttribute('r', String(6 + Math.min(10, n.degree * 2)));
            group.appendChild(circle);

            var label = document.createElementNS(NS, 'text');
            label.setAttribute('x', n.x);
            label.setAttribute('y', n.y - 12);
            label.setAttribute('text-anchor', 'middle');
            label.textContent = n.title.length > 14 ? n.title.slice(0, 13) + '…' : n.title;
            group.appendChild(label);

            function go() {
                closePanel();
                api.focusMemo(n.id);
            }
            group.addEventListener('click', go);
            group.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
            });
            svg.appendChild(group);
        });

        var wrap = document.createElement('div');
        wrap.className = 'graph-wrap';
        wrap.appendChild(svg);

        var legend = document.createElement('p');
        legend.className = 'hint';
        legend.textContent =
            '메모 ' + nodes.length + '개, 연결 ' + edges.length + '개. 동그라미를 누르면 그 메모로 갑니다.';
        wrap.appendChild(legend);
        return wrap;
    }

    // ------------------------------------------------------- 활동 히트맵

    function openActivity() {
        var panel = dialog('활동 기록');
        panel.body.textContent = '불러오는 중...';

        Promise.all([
            api.request('GET', '/stats/activity?days=180'),
            api.request('GET', '/stats/summary'),
        ])
            .then(function (results) {
                panel.body.replaceChildren();
                panel.body.appendChild(summaryRow(results[1]));
                panel.body.appendChild(drawHeatmap(results[0]));
            })
            .catch(function (err) {
                panel.body.replaceChildren(note(err.message));
            });
    }

    function summaryRow(data) {
        var wrap = document.createElement('dl');
        wrap.className = 'summary';
        [
            ['메모', data.total],
            ['태그', data.tags],
            ['연결된 메모', data.linked],
            ['잠긴 메모', data.locked + data.encrypted],
        ].forEach(function (pair) {
            var dt = document.createElement('dt');
            dt.textContent = pair[0];
            var dd = document.createElement('dd');
            dd.textContent = String(pair[1]);
            wrap.append(dt, dd);
        });
        return wrap;
    }

    function drawHeatmap(data) {
        var NS = 'http://www.w3.org/2000/svg';
        var counts = data.counts || {};
        var days = data.days;
        var cell = 11;
        var gap = 3;
        var weeks = Math.ceil(days / 7) + 1;

        var svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', '0 0 ' + (weeks * (cell + gap) + 10) + ' ' + (7 * (cell + gap) + 10));
        svg.setAttribute('class', 'heatmap');
        svg.setAttribute('role', 'img');
        svg.setAttribute(
            'aria-label',
            '최근 ' + days + '일 동안 메모 ' + data.total + '개를 썼습니다. 연속 ' + data.streak + '일.'
        );

        var today = new Date();
        for (var back = days; back >= 0; back--) {
            var date = new Date(today);
            date.setDate(today.getDate() - back);
            var key = date.toISOString().slice(0, 10);
            var count = counts[key] || 0;

            var column = Math.floor((days - back) / 7);
            var row = date.getDay();

            var rect = document.createElementNS(NS, 'rect');
            rect.setAttribute('x', String(column * (cell + gap) + 5));
            rect.setAttribute('y', String(row * (cell + gap) + 5));
            rect.setAttribute('width', String(cell));
            rect.setAttribute('height', String(cell));
            rect.setAttribute('rx', '2');
            rect.setAttribute('class', 'heat level-' + Math.min(4, count));

            var title = document.createElementNS(NS, 'title');
            title.textContent = key + ': ' + count + '개';
            rect.appendChild(title);
            svg.appendChild(rect);
        }

        var wrap = document.createElement('div');
        wrap.className = 'heatmap-wrap';
        wrap.appendChild(svg);

        var caption = document.createElement('p');
        caption.className = 'hint';
        caption.textContent = data.streak > 0
            ? '연속 ' + data.streak + '일째 쓰는 중입니다.'
            : '오늘 한 줄 남겨 보세요.';
        wrap.appendChild(caption);
        return wrap;
    }

    // ------------------------------------------------------- 수정 이력

    function openRevisions(memoId, title) {
        var panel = dialog('수정 이력: ' + title);
        panel.body.textContent = '불러오는 중...';

        api.request('GET', '/memo/' + memoId + '/revisions')
            .then(function (data) {
                panel.body.replaceChildren();
                if (!data.revisions.length) {
                    panel.body.appendChild(note('아직 고친 적이 없습니다.'));
                    return;
                }
                data.revisions.forEach(function (rev) {
                    panel.body.appendChild(revisionRow(rev, memoId, panel.close));
                });
            })
            .catch(function (err) {
                panel.body.replaceChildren(note(err.message));
            });
    }

    function revisionRow(rev, memoId, closePanel) {
        var box = document.createElement('section');
        box.className = 'revision';

        var head = document.createElement('h3');
        var when = rev.saved_at ? new Date(rev.saved_at) : null;
        head.textContent = when && !Number.isNaN(when.getTime())
            ? when.toLocaleString(document.documentElement.lang || 'ko-KR')
            : '시각 불명';
        box.appendChild(head);

        if (rev.title) {
            var titleLine = document.createElement('p');
            titleLine.className = 'hint';
            titleLine.textContent = '당시 제목: ' + rev.title;
            box.appendChild(titleLine);
        }

        if (!rev.diff.length) {
            box.appendChild(note('내용 변경 없음'));
        } else {
            var diff = document.createElement('pre');
            diff.className = 'diff';
            rev.diff.forEach(function (line) {
                var span = document.createElement('span');
                span.className = 'diff-' + line.kind;
                span.textContent = (line.kind === 'added' ? '+ ' : '- ') + line.text + '\n';
                diff.appendChild(span);
            });
            box.appendChild(diff);
        }

        var restore = document.createElement('button');
        restore.type = 'button';
        restore.className = 'btn btn-sm btn-quiet';
        restore.textContent = '이 내용으로 되돌리기';
        restore.addEventListener('click', function () {
            closePanel();
            api.restoreRevision(memoId, rev.id);
        });
        box.appendChild(restore);
        return box;
    }

    return {
        init: init,
        dialog: dialog,
        openGraph: openGraph,
        openActivity: openActivity,
        openRevisions: openRevisions,
    };
})();
