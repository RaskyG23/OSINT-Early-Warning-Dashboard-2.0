"""Browser-hosted entry point for Streamlit Community Cloud.

The Docker deployment continues to run ``app/dashboard.py`` directly. This
small wrapper gives browser-only deployments a stable repository-root entry
point and a writable project-local SQLite path.
"""

import os
import runpy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("OSINT_DB_PATH", str(PROJECT_ROOT / "data" / "osint-dashboard.sqlite"))
os.environ.setdefault("OSINT_HOST_MODE", "Browser-hosted Streamlit")

# Execute the dashboard as the Streamlit page rather than merely importing it.
# This keeps Streamlit's script context attached to every widget and component
# on Community Cloud while preserving ``app.dashboard`` as the Docker entry.
runpy.run_module("app.dashboard", run_name="__main__")
