# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""File firmati `.p7m` (CMS / PKCS#7 SignedData): estrae il documento contenuto, anche firmato più volte.

Lettore BER/DER minimo scritto qui: `asn1crypto` non si può usare perché importa `urllib.parse` e `socket`,
che StillFind blocca. La firma non viene verificata: per la ricerca serve solo il contenuto."""
from __future__ import annotations

import base64
import binascii
import re
import zipfile
from io import BytesIO

SIGNED_DATA_OID = bytes.fromhex("2a864886f70d010702")
MAX_DEPTH = 6          # annidamento massimo della struttura ASN.1
MAX_SIGNATURES = 6     # .pdf.p7m.p7m.p7m...


def _tlv(data: bytes, pos: int, end: int):
    """Un elemento BER: (tag, costruito, inizio contenuto, fine contenuto, posizione successiva)."""
    if pos + 2 > end:
        raise ValueError("fine dati")
    first = data[pos]
    constructed = bool(first & 0x20)
    tag = first
    pos += 1
    if first & 0x1F == 0x1F:  # tag su più byte
        while pos < end and data[pos] & 0x80:
            pos += 1
        pos += 1
    length = data[pos]
    pos += 1
    if length == 0x80:  # lunghezza indefinita: finisce con 00 00
        if not constructed:
            raise ValueError("lunghezza indefinita non valida")
        start, cur = pos, pos
        while True:
            if cur + 2 <= end and data[cur] == 0 and data[cur + 1] == 0:
                return tag, constructed, start, cur, cur + 2
            _, _, _, _, cur = _tlv(data, cur, end)
    if length & 0x80:
        n = length & 0x7F
        if n > 4 or pos + n > end:
            raise ValueError("lunghezza non valida")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    if pos + length > end:
        raise ValueError("elemento oltre la fine dei dati")
    return tag, constructed, pos, pos + length, pos + length


def _children(data: bytes, start: int, end: int):
    pos = start
    while pos < end:
        tag, constructed, s, e, pos = _tlv(data, pos, end)
        yield tag, constructed, s, e


def _octets(data: bytes, start: int, end: int, depth: int = 0) -> bytes:
    """Contenuto di un OCTET STRING, anche costruito (a pezzi)."""
    if depth > MAX_DEPTH:
        raise ValueError("troppo annidato")
    out = bytearray()
    for tag, constructed, s, e in _children(data, start, end):
        if tag & 0x1F == 4:
            out += _octets(data, s, e, depth + 1) if constructed else data[s:e]
    return bytes(out)


def unwrap_cms(data: bytes) -> bytes | None:
    """Documento contenuto in un CMS SignedData, o None se `data` non è un file firmato (o è «detached»)."""
    try:
        tag, _, s, e, _ = _tlv(data, 0, len(data))
        if tag != 0x30:
            return None
        items = list(_children(data, s, e))
        if len(items) < 2 or items[0][0] != 0x06 or data[items[0][2]:items[0][3]] != SIGNED_DATA_OID:
            return None
        explicit = items[1]                                   # [0] EXPLICIT SignedData
        signed = next(_children(data, explicit[2], explicit[3]))  # SEQUENCE
        parts = list(_children(data, signed[2], signed[3]))
        # version, digestAlgorithms (SET), encapContentInfo (SEQUENCE), ...
        encap = next(p for p in parts if p[0] == 0x30)
        enc_items = list(_children(data, encap[2], encap[3]))
        content = next((c for c in enc_items if c[0] == 0xA0), None)
        if content is None:
            return None
        return _octets(data, content[2], content[3])
    except (ValueError, StopIteration, IndexError):
        return None


def _binary(raw: bytes) -> bytes:
    """Il file può essere DER/BER oppure testo base64 (anche con intestazione PEM)."""
    if raw[:1] == b"\x30":
        return raw
    text = re.sub(rb"-----(BEGIN|END)[^-]*-----", b"", raw)
    try:
        return base64.b64decode(re.sub(rb"\s+", b"", text), validate=True)
    except (binascii.Error, ValueError):
        return raw


def unwrap_all(raw: bytes) -> bytes | None:
    """Toglie tutte le firme annidate. None se il file non contiene un documento."""
    data, found = raw, False
    for _ in range(MAX_SIGNATURES):
        inner = unwrap_cms(_binary(data))
        if inner is None:
            break
        data, found = inner, True
    return data if found else None


def guess_ext(name: str, data: bytes) -> str:
    """Tipo del documento contenuto: dal nome (`fattura.xml.p7m`) o, se manca, dal contenuto."""
    base = re.sub(r"(?i)(\.p7m)+$", "", name)
    if "." in base:
        return base.rsplit(".", 1)[1].lower()
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "bin"
        for prefix, ext in (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx")):
            if any(n.startswith(prefix) for n in names):
                return ext
        return "bin"
    if data.lstrip()[:5] == b"<?xml" or data.lstrip()[:1] == b"<":
        return "xml"
    if data.startswith(b"{\\rtf"):
        return "rtf"
    return "txt"
