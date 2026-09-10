"""gunicorn 진입점.

gunicorn --workers 2 wsgi:app
"""

from memo import create_app

app = create_app()
