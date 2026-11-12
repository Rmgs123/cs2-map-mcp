"""Start the cs2map status page (the panel) by hand:

    python tools/panel.py [--port 8717]

The MCP server starts it by itself when config/settings.json has "panel": true. The page shows what the tools
are doing, the frames, the load of the PC and the user's permissions; see docs/specs/panel.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cs2map import panel  # noqa: E402

if __name__ == '__main__':
    panel.main()
