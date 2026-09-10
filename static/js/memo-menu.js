/**
 * 메모 하나에 딸린 부가 기능 모음: 연결 보기, 공유 링크, 타임캡슐.
 *
 * app.js 가 window.MemoMenu.open(memo) 로 부른다. 공용 동작(요청, 토스트,
 * 목록 갱신)은 app.js 가 넘겨주는 bridge 를 통해 쓴다.
 */
window.MemoMenu = (function () {
    'use strict';

    var api = null;

    function init(bridge) {
        api = bridge;
    }

    function button(label, run) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-quiet menu-item';
        btn.textContent = label;
        btn.addEventListener('click', run);
        return btn;
    }

    function hint(text) {
        var p = document.createElement('p');
        p.className = 'hint';
        p.textContent = text;
        return p;
    }

    function heading(text) {
        var h = document.createElement('h3');
        h.textContent = text;
        return h;
    }

    // ------------------------------------------------------------ 메뉴

    function open(memo) {
        var panel = window.MemoPanels.dialog(memo.title);

        panel.body.appendChild(button('수정 이력 보기', function () {
            panel.close();
            window.MemoPanels.openRevisions(memo.id, memo.title);
        }));
        panel.body.appendChild(button('연결된 메모 보기', function () {
            panel.close();
            openLinks(memo);
        }));

        panel.body.appendChild(document.createElement('hr'));
        panel.body.appendChild(shareSection(memo, panel));
        panel.body.appendChild(document.createElement('hr'));
        panel.body.appendChild(capsuleSection(memo, panel));
    }

    // ------------------------------------------------------------ 연결

    function openLinks(memo) {
        var panel = window.MemoPanels.dialog('연결: ' + memo.title);
        panel.body.textContent = '불러오는 중...';

        api.request('GET', '/memo/' + memo.id + '/links')
            .then(function (data) {
                panel.body.replaceChildren();
                panel.body.appendChild(
                    linkGroup('이 메모가 가리키는 메모', data.outgoing, panel.close)
                );
                panel.body.appendChild(
                    linkGroup('이 메모를 가리키는 메모', data.incoming, panel.close)
                );

                if (data.missing.length) {
                    var section = document.createElement('section');
                    section.appendChild(heading('아직 없는 메모'));
                    section.appendChild(hint('링크는 걸려 있지만 아직 쓰지 않은 메모입니다.'));
                    data.missing.forEach(function (title) {
                        section.appendChild(button(title + ' 쓰기', function () {
                            panel.close();
                            api.startWriting(title);
                        }));
                    });
                    panel.body.appendChild(section);
                }
            })
            .catch(function (err) {
                panel.body.replaceChildren(hint(err.message));
            });
    }

    function linkGroup(title, memos, closePanel) {
        var section = document.createElement('section');
        section.appendChild(heading(title + ' (' + memos.length + ')'));

        if (!memos.length) {
            section.appendChild(hint('없습니다.'));
            return section;
        }
        memos.forEach(function (memo) {
            section.appendChild(button(memo.title, function () {
                closePanel();
                api.focusMemo(memo.id);
            }));
        });
        return section;
    }

    // ------------------------------------------------------------ 공유

    function shareSection(memo, panel) {
        var section = document.createElement('section');
        section.appendChild(heading('공유 링크'));
        section.appendChild(hint('링크를 아는 사람이 읽기만 할 수 있습니다.'));

        var burnLabel = document.createElement('label');
        burnLabel.className = 'checkbox';
        var burn = document.createElement('input');
        burn.type = 'checkbox';
        burnLabel.appendChild(burn);
        burnLabel.appendChild(document.createTextNode(' 한 번 읽으면 사라지게 하기'));
        section.appendChild(burnLabel);

        var output = document.createElement('div');
        output.className = 'share-output';
        section.appendChild(output);

        section.appendChild(button('링크 만들기', function () {
            api.request('POST', '/memo/share', {
                id_give: memo.id,
                burn_give: burn.checked ? '1' : '0',
            })
                .then(function (data) {
                    showLink(output, window.location.origin + '/s/' + data.token);
                    api.reload();
                })
                .catch(api.showError);
        }));

        if (memo.shared) {
            section.appendChild(button('공유 중단', function () {
                api.request('POST', '/memo/share', { id_give: memo.id, revoke: '1' })
                    .then(function () {
                        panel.close();
                        api.toast('공유를 중단했습니다.');
                        api.reload();
                    })
                    .catch(api.showError);
            }));
        }
        return section;
    }

    function showLink(container, url) {
        container.replaceChildren();

        var label = document.createElement('label');
        label.className = 'visually-hidden';
        label.setAttribute('for', 'share-url');
        label.textContent = '공유 주소';

        var field = document.createElement('input');
        field.id = 'share-url';
        field.className = 'form-control';
        field.readOnly = true;
        field.value = url;
        field.addEventListener('focus', function () { field.select(); });

        container.append(label, field);
        container.appendChild(button('복사', function () {
            field.select();
            if (!navigator.clipboard) {
                api.toast('주소를 직접 복사해 주세요.');
                return;
            }
            navigator.clipboard.writeText(url)
                .then(function () { api.toast('링크를 복사했습니다.'); })
                .catch(function () { api.toast('주소를 직접 복사해 주세요.'); });
        }));
        field.focus();
    }

    // ------------------------------------------------------------ 타임캡슐

    function capsuleSection(memo, panel) {
        var section = document.createElement('section');
        section.appendChild(heading('타임캡슐'));
        section.appendChild(hint('지정한 때가 되기 전에는 서버도 내용을 내보내지 않습니다.'));

        var field = document.createElement('div');
        field.className = 'field';

        var label = document.createElement('label');
        label.setAttribute('for', 'capsule-input');
        label.textContent = '열릴 날짜와 시각';

        var input = document.createElement('input');
        input.id = 'capsule-input';
        input.type = 'datetime-local';
        input.className = 'form-control search-field';
        if (memo.open_at) {
            var when = new Date(memo.open_at);
            if (!Number.isNaN(when.getTime())) {
                input.value = new Date(when.getTime() - when.getTimezoneOffset() * 60000)
                    .toISOString()
                    .slice(0, 16);
            }
        }
        field.append(label, input);
        section.appendChild(field);

        section.appendChild(button('이 날짜에 열기', function () {
            if (!input.value) {
                api.toast('날짜를 고르세요.', 'error');
                input.focus();
                return;
            }
            // datetime-local 은 브라우저 현지 시각이다. 서버에는 UTC 로 보낸다.
            api.request('POST', '/memo/capsule', {
                id_give: memo.id,
                open_at_give: new Date(input.value).toISOString(),
            })
                .then(function (data) {
                    panel.close();
                    api.toast(data.msg);
                    api.reload();
                })
                .catch(api.showError);
        }));

        if (memo.open_at) {
            section.appendChild(button('잠금 풀기', function () {
                api.request('POST', '/memo/capsule', { id_give: memo.id, open_at_give: '' })
                    .then(function (data) {
                        panel.close();
                        api.toast(data.msg);
                        api.reload();
                    })
                    .catch(api.showError);
            }));
        }
        return section;
    }

    return { init: init, open: open };
})();
