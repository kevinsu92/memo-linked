/**
 * 테마 초기화.
 *
 * <head> 에서 동기적으로 실행해 첫 페인트 전에 테마를 확정한다.
 * 이 파일이 없거나 늦게 실행되면 다크 모드 사용자가 흰 화면 깜빡임을 본다.
 */
(function () {
    'use strict';
    var theme;
    try {
        theme = localStorage.getItem('theme');
    } catch (e) {
        theme = null;
    }
    if (theme !== 'dark' && theme !== 'light') {
        theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark'
            : 'light';
    }
    var root = document.documentElement;
    root.setAttribute('data-theme', theme);
    // Bootstrap 5.3 자체 다크 팔레트도 같이 전환한다.
    root.setAttribute('data-bs-theme', theme);

    // 사용자가 직접 고르지 않았다면 OS 설정 변경을 그대로 따라간다.
    if (window.matchMedia) {
        var query = window.matchMedia('(prefers-color-scheme: dark)');
        var onChange = function (e) {
            var saved;
            try {
                saved = localStorage.getItem('theme');
            } catch (err) {
                saved = null;
            }
            if (saved === 'dark' || saved === 'light') return;
            var next = e.matches ? 'dark' : 'light';
            root.setAttribute('data-theme', next);
            root.setAttribute('data-bs-theme', next);
        };
        if (query.addEventListener) {
            query.addEventListener('change', onChange);
        } else if (query.addListener) {
            query.addListener(onChange);
        }
    }
})();
