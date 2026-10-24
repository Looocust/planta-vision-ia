"""Fixtures compartidas de las pruebas del agente (modelo guionado + stub de visión + DB temporal)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from eval.agent.vision_stub import VisionStub  # noqa: E402
from services.agent.llm import ScriptedChatModel  # noqa: E402
from services.agent.plant.db import init_db  # noqa: E402
from services.agent.service import AgentService  # noqa: E402
from services.agent.vision_client import VisionClient  # noqa: E402

COIL = ROOT / "data" / "raw" / "coil-100"


def make_service(tmp_path, script, stub: VisionStub | None = None):
    stub = stub or VisionStub()
    con = init_db(tmp_path / "plant.db", reseed=True)
    vc = VisionClient(base_url="http://vision.test", transport=stub.transport(), retries=1, backoff_s=0.01)
    model = ScriptedChatModel(script=list(script))
    svc = AgentService(model=model, con=con, vision=vc, checkpointer=InMemorySaver(), model_name="fake",
                       upload_root=tmp_path / "uploads")
    return svc, model, stub


def upload_coil(svc, stub, name="obj5__30", scenario="act", plant="P1", line="P1-L1", user="u_sup_p1",
                role="supervisor"):
    data = stub.register_file(COIL / f"{name}.png", scenario)
    return svc.upload(data, f"{name}.png", plant, line, user, role)["image_id"]
