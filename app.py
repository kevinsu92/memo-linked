"""로컬 개발 서버 실행용.

    python app.py

운영 환경은 gunicorn 이 wsgi.py 를 쓴다.
"""

from memo.app import main

if __name__ == '__main__':
    main()
