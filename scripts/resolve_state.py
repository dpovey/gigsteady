"""Read-only: print what Resolve has open. Usage: /usr/bin/python3 scripts/resolve_state.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard, state  # noqa: E402

guard()
print(json.dumps(state(connect()), indent=1))
