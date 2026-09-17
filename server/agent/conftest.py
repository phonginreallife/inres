"""
Pytest configuration.

The agent is run as a flat package set from its own directory (``uvicorn
main:app``), not as an installed distribution, so tests need that directory on
``sys.path`` to import ``session``, ``config``, and friends.
"""

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent

if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
