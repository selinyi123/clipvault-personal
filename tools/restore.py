"""Disaster recovery: rebuild a SQLite database from backup JSONL (GHB-1, C6).

Proves the backup is actually usable — this is the v1.0 acceptance gate.
Never overwrites an existing database; writes to a fresh path.

  python tools/restore.py <backup_repo_path> <out.db>
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "desktop"))

from clipvault.backup import jsonl_store  # noqa: E402
from clipvault.store import db  # noqa: E402
from clipvault.store.clips_repo import ClipsRepo  # noqa: E402


def restore(repo_path: str, out_db: str) -> int:
    out = Path(out_db)
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing database: {out}")

    # Deduplicate by id; the last occurrence (latest backup) wins.
    by_id = {}
    for line in jsonl_store.iter_jsonl(repo_path):
        clip = jsonl_store.deserialize_clip(line)
        by_id[clip.id] = clip

    conn = db.connect(str(out))
    db.migrate(conn)
    repo = ClipsRepo(conn)
    for clip in by_id.values():
        repo.insert(clip)
    conn.close()
    return len(by_id)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python tools/restore.py <backup_repo_path> <out.db>")
        return 2
    count = restore(argv[0], argv[1])
    print(f"restored {count} clips -> {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
