"""PyInstaller entry point for the standalone clipvault.exe.

Bundles the Python runtime + stdlib so the desktop node runs with no Python
install. Data files (SQL migrations, Web UI assets) are added via the spec /
--add-data so Path(__file__).parent lookups resolve inside the bundle.
"""

import sys

from clipvault.main import main

if __name__ == "__main__":
    sys.exit(main())
