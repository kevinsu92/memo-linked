/**
 * 서버가 심어 둔 설정 JSON 을 읽어 전역에 올린다.
 *
 * 인라인 스크립트를 쓰지 않으므로 Content-Security-Policy 에
 * 'unsafe-inline' 을 열어 줄 필요가 없다.
 */
(function () {
    'use strict';
    var node = document.getElementById('memo-config');
    if (!node) return;
    try {
        window.MEMO_CONFIG = JSON.parse(node.textContent);
    } catch (e) {
        window.MEMO_CONFIG = {};
    }
})();
