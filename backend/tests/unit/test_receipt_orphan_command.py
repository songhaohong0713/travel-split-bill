import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from app.worker import receipt_orphans


def test_preview_never_queues_deletions_and_apply_only_submits_reviewed_ids(
    tmp_path, monkeypatch
):
    class Cloud:
        def __init__(self):
            self.calls = []

        async def rpc(self, name, payload):
            self.calls.append((name, payload))
            return (
                [{"id": "reviewed"}] if name.endswith("preview") else {"queued": True}
            )

        async def aclose(self):
            pass

    cloud = Cloud()
    monkeypatch.setattr(
        receipt_orphans.CloudBasePgClient, "from_environment", lambda: cloud
    )
    manifest = tmp_path / "private-preview.json"
    asyncio.run(receipt_orphans.run(SimpleNamespace(preview=str(manifest), apply=None)))
    assert [name for name, _ in cloud.calls] == ["tsb_receipt_orphan_preview"]
    assert (
        datetime.now(UTC)
        - datetime.fromisoformat(json.loads(manifest.read_text())["cutoff"])
    ).days >= 30
    asyncio.run(receipt_orphans.run(SimpleNamespace(preview=None, apply=str(manifest))))
    assert cloud.calls[-1][0] == "tsb_queue_receipt_orphan"
    assert cloud.calls[-1][1]["p_image_id"] == "reviewed"
