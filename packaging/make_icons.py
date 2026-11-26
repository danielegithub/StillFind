#!/usr/bin/env python3
"""Genera le icone degli installatori (.ico per Windows, .png per Linux) dal marchio in stillfind/ui/brand/."""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "icons")
out.mkdir(parents=True, exist_ok=True)
mark = Image.open(ROOT / "stillfind/ui/brand/mark.png").convert("RGBA")
mark.save(out / "stillfind.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
mark.resize((256, 256), Image.LANCZOS).save(out / "stillfind.png")
print(out)
