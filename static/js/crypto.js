/**
 * 잠금 메모 암·복호화.
 *
 * 브라우저에서만 처리하고 비밀번호는 어디에도 보내지 않는다. 서버는
 * "memo1.<salt>.<iv>.<암호문>" 형태의 문자열만 받아 그대로 보관한다.
 *
 * 알고리즘: PBKDF2-SHA256(20만 회)로 키를 만들고 AES-GCM 256 으로 감싼다.
 */
window.MemoCrypto = (function () {
    'use strict';

    var PREFIX = 'memo1';
    var ITERATIONS = 200000;

    function available() {
        return Boolean(window.crypto && window.crypto.subtle && window.TextEncoder);
    }

    function toBase64Url(bytes) {
        var binary = '';
        var view = new Uint8Array(bytes);
        for (var i = 0; i < view.length; i++) {
            binary += String.fromCharCode(view[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function fromBase64Url(text) {
        var padded = text.replace(/-/g, '+').replace(/_/g, '/');
        while (padded.length % 4) padded += '=';
        var binary = atob(padded);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes;
    }

    function deriveKey(passphrase, salt) {
        var encoder = new TextEncoder();
        return window.crypto.subtle
            .importKey('raw', encoder.encode(passphrase), 'PBKDF2', false, ['deriveKey'])
            .then(function (material) {
                return window.crypto.subtle.deriveKey(
                    { name: 'PBKDF2', salt: salt, iterations: ITERATIONS, hash: 'SHA-256' },
                    material,
                    { name: 'AES-GCM', length: 256 },
                    false,
                    ['encrypt', 'decrypt']
                );
            });
    }

    function isEncrypted(text) {
        return typeof text === 'string' && text.indexOf(PREFIX + '.') === 0
            && text.split('.').length === 4;
    }

    function encrypt(plaintext, passphrase) {
        if (!available()) {
            return Promise.reject(new Error('이 브라우저는 잠금 메모를 지원하지 않습니다.'));
        }
        var salt = window.crypto.getRandomValues(new Uint8Array(16));
        var iv = window.crypto.getRandomValues(new Uint8Array(12));

        return deriveKey(passphrase, salt)
            .then(function (key) {
                return window.crypto.subtle.encrypt(
                    { name: 'AES-GCM', iv: iv },
                    key,
                    new TextEncoder().encode(plaintext)
                );
            })
            .then(function (cipher) {
                return [PREFIX, toBase64Url(salt), toBase64Url(iv), toBase64Url(cipher)].join('.');
            });
    }

    function decrypt(payload, passphrase) {
        if (!isEncrypted(payload)) {
            return Promise.reject(new Error('잠긴 메모가 아닙니다.'));
        }
        var parts = payload.split('.');
        var salt = fromBase64Url(parts[1]);
        var iv = fromBase64Url(parts[2]);
        var cipher = fromBase64Url(parts[3]);

        return deriveKey(passphrase, salt)
            .then(function (key) {
                return window.crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv }, key, cipher);
            })
            .then(function (plain) {
                return new TextDecoder().decode(plain);
            })
            .catch(function () {
                // 복호화 실패는 대부분 비밀번호가 틀린 경우다.
                throw new Error('비밀번호가 맞지 않습니다.');
            });
    }

    return {
        available: available,
        encrypt: encrypt,
        decrypt: decrypt,
    };
})();
