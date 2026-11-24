"""Write the API contract to ``frontend/openapi.json``.

The dashboard's TypeScript types are generated from this file
(``npm --prefix frontend run gen:api``); CI checks both are up to date.
Run: ``uv run python scripts/export_openapi.py``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from moneylab.api.app import create_app
from moneylab.service import Lab

OUT = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"


def main() -> None:
    with tempfile.TemporaryDirectory() as home:
        spec = create_app(Lab.open(Path(home)), web_dir=Path(home) / "none").openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
