# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrattori per testo semplice, HTML e RTF."""
from __future__ import annotations

import html
import re
from pathlib import Path


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace")


def extract_html(path: str) -> str:
    raw = _decode(Path(path).read_bytes())
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\b[^>]*>", "\n", raw)
    raw = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6])\s*>", "\n\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return html.unescape(raw)


_RTF_SKIP_DESTS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "pict", "object", "datafield", "fldinst",
    "info", "listtable", "listoverridetable", "revtbl", "rsidtbl", "themedata",
    "colorschememapping", "latentstyles", "datastore", "xmlnstbl", "mmathPr",
    "operator", "author", "creatim", "revtim", "printim", "buptim", "generator",
    "pnseclvl", "pntxtb", "pntxta", "bkmkstart", "bkmkend", "xe", "tc", "rxe",
})


def extract_rtf(path: str) -> str:
    """Estrae testo RTF ignorando gruppi di metadati, font, stili e immagini embedded."""
    raw = Path(path).read_bytes().decode("latin-1")
    out: list[str] = []
    stack: list[tuple[int, bool]] = []
    uc = 1
    ignorable = False
    skip_chars = 0
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == "{":
            stack.append((uc, ignorable))
            i += 1
        elif ch == "}":
            if stack:
                uc, ignorable = stack.pop()
            else:
                ignorable = False
            i += 1
        elif ch == "\\":
            i += 1
            if i >= n:
                break
            nxt = raw[i]
            if nxt in ("\\", "{", "}"):
                if not ignorable:
                    if skip_chars > 0:
                        skip_chars -= 1
                    else:
                        out.append(nxt)
                i += 1
            elif nxt == "*":
                ignorable = True
                i += 1
            elif nxt == "~":
                if not ignorable:
                    out.append(" ")
                i += 1
            elif nxt in ("-", "_"):
                if not ignorable and nxt == "_":
                    out.append("-")
                i += 1
            elif nxt in ("\r", "\n"):
                if not ignorable:
                    out.append("\n")
                i += 1
            elif nxt == "'":
                hex_s = raw[i + 1:i + 3]
                if len(hex_s) == 2 and all(c in "0123456789abcdefABCDEF" for c in hex_s):
                    if not ignorable:
                        if skip_chars > 0:
                            skip_chars -= 1
                        else:
                            out.append(bytes.fromhex(hex_s).decode("cp1252", "replace"))
                    i += 3
                else:
                    i += 1
            elif nxt.isalpha():
                j = i
                while j < n and raw[j].isalpha():
                    j += 1
                word = raw[i:j]
                k = j
                if k < n and raw[k] == "-":
                    k += 1
                while k < n and raw[k].isdigit():
                    k += 1
                param = raw[j:k]
                if k < n and raw[k] == " ":
                    k += 1
                i = k
                if word in _RTF_SKIP_DESTS:
                    ignorable = True
                elif word == "uc" and param:
                    try:
                        uc = max(0, int(param))
                    except ValueError:
                        pass
                elif word == "u" and param:
                    if not ignorable:
                        try:
                            code = int(param)
                            if code < 0:
                                code += 65536
                            out.append(chr(code % 65536))
                        except ValueError:
                            pass
                    skip_chars = uc
                elif not ignorable:
                    if word in ("par", "line"):
                        out.append("\n\n" if word == "par" else "\n")
                    elif word == "tab":
                        out.append("\t")
                    elif word == "emdash":
                        out.append("—")
                    elif word == "endash":
                        out.append("–")
                    elif word in ("lquote", "rquote"):
                        out.append("'")
                    elif word in ("ldblquote", "rdblquote"):
                        out.append('"')
            else:
                i += 1
        elif ch in ("\r", "\n"):
            i += 1
        else:
            if not ignorable:
                if skip_chars > 0:
                    skip_chars -= 1
                else:
                    out.append(ch)
            i += 1
    return "".join(out)


def extract_text_file(path: str) -> str:
    return _decode(Path(path).read_bytes())
