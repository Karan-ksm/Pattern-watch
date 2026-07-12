"""Production entry point.

    gunicorn wsgi:app --workers 1 --bind 0.0.0.0:$PORT

Exactly ONE worker: the poll thread and the live traffic snapshot
live in this process's memory. More workers would mean several pollers
burning API credits and visitors seeing inconsistent pictures.
"""

import config
from main import build_web_app

app = build_web_app(config.AIRPORT)
