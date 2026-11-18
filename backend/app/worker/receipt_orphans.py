"""30-day orphan cleanup: preview first, then apply that exact private manifest.

uv run python -m app.worker.receipt_orphans --preview backups/receipt-orphans.json
uv run python -m app.worker.receipt_orphans --apply backups/receipt-orphans.json

Does not run automatically. SQL rechecks age, live jobs and all attachment references
under the same trip lock used by expense saves before queuing deletion.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.providers.cloudbase_pg import CloudBasePgClient


def write_manifest(path, cutoff, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {"cutoff": cutoff, "candidates": rows}, stream, ensure_ascii=False, indent=2
        )


async def run(args):
    cloud = CloudBasePgClient.from_environment()
    try:
        if args.preview:
            cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
            rows = await cloud.rpc("tsb_receipt_orphan_preview", {"p_cutoff": cutoff})
            path = Path(args.preview)
            # Exclusive create avoids silently replacing an already reviewed manifest.
            await asyncio.to_thread(write_manifest, path, cutoff, rows)
            print(f"Preview only: {len(rows)} candidates. Private manifest: {path}")
        else:
            manifest = json.loads(Path(args.apply).read_text(encoding="utf-8"))
            cutoff = datetime.fromisoformat(manifest["cutoff"])
            if cutoff.tzinfo is None or cutoff > datetime.now(UTC) - timedelta(days=30):
                raise ValueError("Manifest must retain at least 30 days")
            count = 0
            for row in manifest["candidates"]:
                result = await cloud.rpc(
                    "tsb_queue_receipt_orphan",
                    {"p_image_id": row["id"], "p_cutoff": cutoff.isoformat()},
                )
                count += bool(result.get("queued"))
            print(
                f"Queued {count} rechecked orphans. Run the existing receipt_cleanup worker to delete objects."
            )
    finally:
        await cloud.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", metavar="MANIFEST")
    mode.add_argument("--apply", metavar="MANIFEST")
    asyncio.run(run(parser.parse_args()))
