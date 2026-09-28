#!/usr/bin/env python3
"""把网页和页面要读的数据拷到 dist/。原始 CSV 不进 dist。"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PAGES = ["index.html", "macro.html"]
DATA_FILES = ["data/macro/dashboard.json"]


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for p in PAGES:
        shutil.copy2(ROOT / p, DIST / p)
    shutil.copytree(ROOT / "assets", DIST / "assets")
    for rel in DATA_FILES:
        src = ROOT / rel
        if src.exists():
            (DIST / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, DIST / rel)
        else:
            print(f"缺少 {rel}，页面会提示数据还没生成")
    (DIST / ".nojekyll").write_text("")
    (DIST / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    print(f"已构建到 {DIST}")


if __name__ == "__main__":
    main()
