"""애플리케이션 팩토리."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException, TooManyRequests
from werkzeug.middleware.proxy_fix import ProxyFix

from memo import config as config_module
from memo.db import backfill, ensure_indexes, make_client
from memo.extensions import limiter
from memo.features import bp as features_bp
from memo.routes import bp as memo_bp

try:  # python-dotenv 가 없는 환경에서도 실행되도록 선택적 로드
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger('memo')

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


class RequestIdFilter(logging.Filter):
    """모든 로그 줄에 요청 id 를 붙여 워커가 섞여도 추적할 수 있게 한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(g, 'request_id', '-') if g else '-'
        return True


def configure_logging(level: int) -> None:
    """루트 로거를 한 번만 설정한다.

    팩토리가 여러 번 불려도(테스트) 핸들러가 쌓이거나 gunicorn 설정을
    덮어쓰지 않도록 이미 우리 핸들러가 있으면 레벨만 조정한다.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, '_memo_handler', False):
            root.setLevel(level)
            return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter('%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s')
    )
    handler.addFilter(RequestIdFilter())
    handler._memo_handler = True  # type: ignore[attr-defined]
    root.handlers = [handler]
    root.setLevel(level)


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    """Flask 앱을 만든다.

    설정을 인자로 주입할 수 있어 테스트가 환경 변수를 건드리지 않아도 된다.
    """
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config.from_mapping(config_module.from_env())
    if overrides:
        app.config.update(overrides)

    # limits 라이브러리는 기본적으로 'limits' 라는 별도 DB 를 쓴다. 인증을 켠
    # MongoDB 에서는 앱 사용자가 그 DB 에 권한이 없어 모든 요청이 503 이 된다.
    # 설정이 모두 확정된 뒤(주입값 포함) 앱 DB 를 쓰도록 맞춘다.
    app.config.setdefault('RATELIMIT_STORAGE_OPTIONS', {})
    app.config['RATELIMIT_STORAGE_OPTIONS'].setdefault('database_name', app.config['DB_NAME'])

    configure_logging(app.config['LOG_LEVEL'])

    # 운영 환경에서 보안 설정이 비어 있으면 조용히 넘어가지 않는다.
    problems = config_module.check_production(app.config)
    if problems:
        if app.config['APP_ENV'] == 'production':
            raise RuntimeError('운영 설정 오류: ' + ' / '.join(problems))
        for problem in problems:
            logger.warning('설정 경고: %s', problem)

    # nginx 뒤에 있다. 프록시가 실제로 설정하는 헤더만 신뢰한다.
    # X-Forwarded-Host 는 nginx 가 덮어쓰지 않으므로 신뢰하지 않는다.
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=0
    )

    app.extensions['mongo'] = make_client(app.config['MONGO_URI'])

    register_hooks(app)
    register_error_handlers(app)
    register_cli(app)
    app.register_blueprint(memo_bp)
    app.register_blueprint(features_bp)
    # flask-limiter 는 RATELIMIT_* 설정을 직접 읽는다. 꺼져 있으면 통과시킨다.
    limiter.init_app(app)
    return app


def register_hooks(app: Flask) -> None:
    @app.before_request
    def assign_request_id() -> None:
        """요청 id 를 정한다. 클라이언트 값은 형식을 확인한 뒤에만 받아들인다."""
        supplied = request.headers.get('X-Request-ID', '')
        g.request_id = supplied if REQUEST_ID_RE.match(supplied) else uuid4().hex[:12]

    @app.before_request
    def block_cross_site_writes() -> ResponseTuple | None:
        """교차 사이트에서 온 쓰기 요청을 막는다 (CSRF 완화).

        폼 전송과 fetch 는 항상 Origin 을 보낸다. Origin 이 없으면 Referer 로
        판단하고, 둘 다 없으면 통과시킨다 (curl 등 비브라우저 요청).
        """
        site_origin = app.config['SITE_ORIGIN']
        if not site_origin or request.method in SAFE_METHODS:
            return None

        origin = request.headers.get('Origin')
        if origin is None:
            referer = request.headers.get('Referer')
            if not referer:
                return None
            parts = urlsplit(referer)
            origin = f'{parts.scheme}://{parts.netloc}'

        if origin != site_origin:
            logger.warning('교차 사이트 쓰기 차단: origin=%s', origin)
            return jsonify({'result': 'fail', 'msg': '교차 사이트 요청은 허용되지 않습니다.'}), 403
        return None

    @app.after_request
    def add_request_id_header(response: Response) -> Response:
        response.headers['X-Request-ID'] = getattr(g, 'request_id', '-')
        return response


ResponseTuple = tuple[Response, int]


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(TooManyRequests)
    def handle_rate_limit(_exc: TooManyRequests) -> ResponseTuple:
        """레이트 리밋 초과. 라이브러리 내부 문구 대신 사용자 문구를 쓴다."""
        return jsonify(
            {
                'result': 'fail',
                'msg': '요청이 너무 잦습니다. 잠시 뒤에 다시 시도하세요.',
            }
        ), 429

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException) -> ResponseTuple:
        """404/405/413 같은 HTTP 예외는 원래 상태 코드를 유지한다."""
        return jsonify({'result': 'fail', 'msg': exc.description}), exc.code or 500

    @app.errorhandler(PyMongoError)
    def handle_db_error(_exc: PyMongoError) -> ResponseTuple:
        logger.exception('DB 오류')
        return jsonify({'result': 'fail', 'msg': '데이터베이스 오류가 발생했습니다.'}), 503

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception) -> ResponseTuple:
        # 테스트에서는 진짜 예외가 보여야 디버깅이 된다.
        if app.testing:
            raise exc
        logger.exception('예상치 못한 오류')
        return jsonify({'result': 'fail', 'msg': '서버 오류가 발생했습니다.'}), 500


def register_cli(app: Flask) -> None:
    @app.cli.command('init-db')
    def init_db_command() -> None:
        """인덱스를 만들고 예전 데이터를 보정한다. 배포 시 1회 실행."""
        db = app.extensions['mongo'][app.config['DB_NAME']]
        names = ensure_indexes(db)
        count = backfill(db)
        print(f'인덱스 {len(names)}개 확인, 메모 {count}건 보정')


def main() -> None:  # pragma: no cover - 개발 서버 전용
    """로컬 개발 서버. 운영에서는 gunicorn 을 쓴다."""
    app = create_app()
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    host = os.environ.get('HOST', '127.0.0.1')
    if debug and host not in ('127.0.0.1', 'localhost'):
        raise SystemExit('디버그 모드는 127.0.0.1 에서만 켤 수 있습니다.')
    app.run(host, port=int(os.environ.get('PORT', 5000)), debug=debug)
