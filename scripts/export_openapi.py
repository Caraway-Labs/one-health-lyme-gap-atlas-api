import json
from pathlib import Path

from lyme_gap_atlas_api.app import app

Path("openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
