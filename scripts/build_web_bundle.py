"""Package the agent for the in-browser build: web/public/py/trolley.zip plus the product snapshot.

Server-only entry points (CLI, MCP server) are left out; they can't run in the browser.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "trolley"
OUT = ROOT / "web" / "public" / "py"
SERVER_ONLY = {"cli.py", "mcp_server.py"}


def main() -> None:
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(OUT / "trolley.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SRC.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.parent == SRC and path.name in SERVER_ONLY:
                continue
            zf.write(path, Path("trolley") / path.relative_to(SRC))
            count += 1
    for name in ("catalogue.json.gz", "prices.json"):
        shutil.copy(ROOT / "data" / name, OUT / "data" / name)
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) // 1024
    print(f"Wrote web/public/py ({count} source files, {size} KB with data)")


if __name__ == "__main__":
    main()
