# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Controllo delle traduzioni: ogni `tr("...")` del codice deve avere tutte le lingue.

Uso:  python -m stillfind.tools.i18n_check            verifica (esce con 1 se qualcosa non va)
      python -m stillfind.tools.i18n_check --missing  stampa in JSON le voci da tradurre
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from stillfind.core.i18n import Languages


class CatalogCheck:
    """Confronta i testi usati nel codice con i cataloghi in `stillfind/locales/`."""

    TARGETS = tuple(code for code in Languages.CODES if code != Languages.SOURCE)
    PLACEHOLDER = re.compile(r"\{(\w+)\}")

    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[1]

    @staticmethod
    def _function(node: ast.Call) -> str | None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        return name if name in ("tr", "trn", "mark") else None

    def code_keys(self) -> tuple[dict[str, tuple[bool, str]], list[str]]:
        """{testo italiano: (è un plurale, dove si trova)} e l'elenco degli usi non validi."""
        keys: dict[str, tuple[bool, str]] = {}
        problems: list[str] = []
        for path in sorted(self.root.rglob("*.py")):
            if path.name == "i18n_check.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not (kind := self._function(node)):
                    continue
                where = f"{path.relative_to(self.root.parent)}:{node.lineno}"
                args = node.args[:2 if kind == "trn" else 1]
                if any(isinstance(a, (ast.JoinedStr, ast.BinOp)) for a in args):
                    problems.append(f"{where}: {kind}() vuole testi letterali, non f-string né concatenazioni")
                    continue
                literals = [a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if len(literals) < len(args) or not args:
                    # tr(variabile) va bene: il testo è dichiarato altrove con mark("...")
                    if kind == "tr" and args and isinstance(args[0], (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
                        continue
                    if args:
                        problems.append(f"{where}: {kind}() vuole testi letterali")
                    continue
                keys.setdefault(literals[0], (kind == "trn", where))
        return keys, problems

    def catalog(self) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        for path in sorted((self.root / "locales").glob("*.json")):
            for source, versions in json.loads(path.read_text(encoding="utf-8")).items():
                entries.setdefault(source, {}).update(versions)
        return entries

    def _placeholders(self, text) -> set[str]:
        return set(self.PLACEHOLDER.findall(text if isinstance(text, str) else " ".join(text.values())))

    def problems(self) -> list[str]:
        keys, problems = self.code_keys()
        entries = self.catalog()
        for source, (plural, where) in keys.items():
            versions = entries.get(source)
            if versions is None:
                problems.append(f"{where}: manca nel catalogo «{source}»")
                continue
            expected = self._placeholders(source) - ({"n"} if plural else set())
            for code in self.TARGETS:
                text = versions.get(code)
                if text in (None, "", {}):
                    problems.append(f"{where}: manca {code} per «{source}»")
                    continue
                if plural != isinstance(text, dict) or (plural and not {"one", "other"} <= set(text)):
                    problems.append(f"{where}: {code} di «{source}» deve avere la forma "
                                    f"{'plurale' if plural else 'semplice'}")
                    continue
                found = self._placeholders(text) - ({"n"} if plural else set())
                if found != expected:
                    problems.append(f"{where}: segnaposto di {code} diversi in «{source}»: "
                                    f"{sorted(found)} ≠ {sorted(expected)}")
        problems += [f"catalogo: «{source}» non è usato dal codice" for source in entries if source not in keys]
        return problems

    def missing(self) -> dict[str, dict]:
        keys, _ = self.code_keys()
        entries = self.catalog()
        out = {}
        for source, (plural, _) in keys.items():
            gaps = [code for code in self.TARGETS if not entries.get(source, {}).get(code)]
            if gaps:
                out[source] = {code: ({"one": "", "other": ""} if plural else "") for code in gaps}
        return out


if __name__ == "__main__":
    check = CatalogCheck()
    if "--missing" in sys.argv:
        print(json.dumps(check.missing(), ensure_ascii=False, indent=1))
        sys.exit(0)
    found = check.problems()
    print("\n".join(found) if found else "Cataloghi completi")
    sys.exit(1 if found else 0)
