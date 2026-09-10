"""Flask 확장 객체.

flask-limiter 가 설치돼 있지 않으면 아무 일도 하지 않는 대체 객체를 쓴다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger('memo.extensions')


class _NoopLimiter:
    """flask-limiter 가 없을 때 쓰는 대체 객체. 데코레이터가 그대로 통과한다."""

    def limit(self, *_args: Any, **_kwargs: Any) -> Callable:
        def decorator(func: Callable) -> Callable:
            return func

        return decorator

    def exempt(self, func: Callable) -> Callable:
        return func

    def init_app(self, _app: Any) -> None:
        logger.warning('flask-limiter 가 없어 애플리케이션 레이트 리밋을 건너뜁니다.')


try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    # 실제 클라이언트 IP 는 ProxyFix 가 X-Forwarded-For 에서 복원해 준다.
    limiter: Any = Limiter(key_func=get_remote_address)
except ImportError:  # pragma: no cover
    limiter = _NoopLimiter()
