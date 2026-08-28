"""Browser-hosted entry point for Streamlit Community Cloud.

The Docker deployment continues to run ``app/dashboard.py`` directly. This
small wrapper gives browser-only deployments a stable repository-root entry
point and a writable project-local SQLite path.
"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("OSINT_DB_PATH", str(PROJECT_ROOT / "data" / "osint-dashboard.sqlite"))
os.environ.setdefault("OSINT_HOST_MODE", "Browser-hosted Streamlit")

from app.dashboard import *  # noqa: F401,F403,E402
