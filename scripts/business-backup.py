"""Export via service-role RPC or prepare SQL for a separate empty restore target."""

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.ops.business_backup import (
    pack_backup,
    restore_sql,
    unpack_backup,
)
from app.providers.cloudbase_pg import CloudBasePgClient


async def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("--env-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("prepare-restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("输出文件已存在，不覆盖")
    if args.action == "export":
        if os.getenv("CLOUDBASE_ENV_ID") != args.env_id:
            raise SystemExit("环境ID与配置不一致")
        client = CloudBasePgClient.from_environment()
        try:
            payload = await client.rpc("tsb_export_business_backup", {})
        finally:
            await client.aclose()
        archive = pack_backup(payload)
        unpack_backup(archive)
        args.output.write_bytes(archive)
        print(
            f"Exported {len(payload['tables'])} tables; SHA256 {hashlib.sha256(archive).hexdigest()}; {args.output}"
        )
    else:
        sql = restore_sql(args.archive.read_bytes())
        args.output.write_text(sql, encoding="utf-8")
        print(f"Restore SQL prepared, not executed: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
