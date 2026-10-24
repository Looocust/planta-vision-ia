"""Exporta los JSON Schema de entrada de cada tool (pydantic) a docs/tool_schemas/."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.agent.tools import TOOLS  # noqa: E402

out = ROOT / "docs" / "tool_schemas"
out.mkdir(parents=True, exist_ok=True)
for name, spec in TOOLS.items():
    schema = spec.input_model.model_json_schema()
    schema["x-kind"] = spec.kind
    schema["x-side-effects"] = spec.side_effects
    (out / f"{name}.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2))
print(f"{len(TOOLS)} esquemas en {out}")
