"""메모끼리 잇는 위키 링크.

내용에 `[[제목]]` 을 적으면 그 제목을 가진 메모로 이어진다. 대상 메모에서는
자기를 가리키는 메모 목록(백링크)을 볼 수 있다. 링크는 제목으로 걸리므로
아직 없는 메모도 미리 가리킬 수 있다.
"""

from __future__ import annotations

import re

# [[제목]] 또는 [[제목|보여줄 글자]]
WIKI_LINK_RE = re.compile(r'\[\[([^\[\]|]{1,100})(?:\|([^\[\]]{1,100}))?\]\]')

MAX_LINKS = 50


def normalize_title(title: str) -> str:
    """제목을 링크 대조용 키로 바꾼다.

    대소문자와 앞뒤 공백, 연속 공백 차이는 같은 메모로 본다.
    """
    return ' '.join(str(title).split()).lower()


def extract_links(content: str) -> list[str]:
    """내용에서 [[제목]] 을 뽑아 정규화한 목록으로 만든다. 중복은 제거한다."""
    found: list[str] = []
    for match in WIKI_LINK_RE.finditer(content or ''):
        key = normalize_title(match.group(1))
        if key and key not in found:
            found.append(key)
        if len(found) >= MAX_LINKS:
            break
    return found
