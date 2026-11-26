#!/usr/bin/env python3
"""Genera le icone dell'app (.icns, .ico, .png) dal marchio in stillfind/ui/brand/: la «S» su un riquadro bianco
con gli angoli arrotondati, come le altre icone di sistema."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def make(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    size = 1024
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((40, 40, size - 40, size - 40), radius=210, fill=(255, 255, 255, 255))
    mark = Image.open(ROOT / "stillfind/ui/brand/mark.png").convert("RGBA").resize((660, 660), Image.LANCZOS)
    tile.alpha_composite(mark, ((size - 660) // 2, (size - 660) // 2))
    tile.save(out / "stillfind.icns")
    tile.save(out / "stillfind.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    tile.resize((256, 256), Image.LANCZOS).save(out / "stillfind.png")
    return out


if __name__ == "__main__":
    print(make(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "icons")))
