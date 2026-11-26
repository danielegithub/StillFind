#!/usr/bin/env python3
"""Download SOLO sulla macchina di compilazione; ogni file ha un SHA-256 fissato."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def prepare(offline=False):
    for item in json.loads((ROOT / "packaging/assets.json").read_text()):
        target = ROOT / item["path"]
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError(f"Impronta errata: {target}. Rimuovi il file e riprepara le risorse.")
            print(f"Verificato: {target.name}", flush=True)
            continue
        if offline:
            raise RuntimeError(f"Risorsa mancante: {target}. Esegui prima packaging/prepare_assets.py.")
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix(target.suffix + ".part")
        print(f"Scarico per la build: {target.name}", flush=True)
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(item["url"], timeout=120) as source, pending.open("wb") as out:
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise RuntimeError(f"Download con impronta errata: {target.name}")
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="verifica soltanto, senza download")
    prepare(parser.parse_args().offline)
