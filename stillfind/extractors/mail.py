# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Email salvate (.eml): mittente, destinatari, data, oggetto, corpo e nomi degli allegati.

Non usa `email.parser`: la libreria standard importa `urllib.parse`, che StillFind blocca.
Il formato (RFC 5322 / MIME) è letto a mano; solo `email.header` serve per le intestazioni codificate.
"""
from __future__ import annotations

import base64
import binascii
import html
import quopri
import re
from email.header import decode_header
from pathlib import Path

from stillfind.extractors.base import ExtractionRequest, Extractor
from stillfind.extractors.text import HtmlExtractor


class EmlExtractor(Extractor):
    """Email salvate: intestazioni, corpo e, con `members`, il testo degli allegati."""

    extensions = ("eml",)
    HEADERS = (("Da", "from"), ("A", "to"), ("Cc", "cc"), ("Data", "date"), ("Oggetto", "subject"),
               ("ID messaggio", "message-id"), ("In risposta a", "in-reply-to"), ("Riferimenti", "references"))
    MAX_DEPTH = 8

    @staticmethod
    def _decode_words(value: str) -> str:
        try:
            parts = decode_header(value)
        except Exception:
            return value
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                try:
                    text = text.decode(enc or "utf-8", errors="replace")
                except LookupError:
                    text = text.decode("utf-8", errors="replace")
            out.append(text)
        return "".join(out)


    @staticmethod
    def _split(raw: bytes) -> tuple[dict[str, str], bytes]:
        """Intestazioni (chiave minuscola -> valore, righe unite) e corpo."""
        m = re.search(rb"\r?\n\r?\n", raw)
        head, body = (raw[:m.start()], raw[m.end():]) if m else (raw, b"")
        text = head.decode("utf-8", errors="replace")
        text = re.sub(r"\r?\n[ \t]+", " ", text)  # righe di continuazione
        headers: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                headers.setdefault(k.strip().lower(), v.strip())
        return headers, body


    @staticmethod
    def _param(header: str, name: str) -> str:
        # RFC 2231, usato anche per i nomi di allegati con caratteri accentati.
        encoded = re.findall(rf'(?i);\s*{name}\*(\d*)\*?\s*=\s*(?:"([^"]*)"|([^;\s]+))', header or "")
        if encoded:
            value = "".join(quoted or simple for _, quoted, simple in sorted(encoded, key=lambda x: int(x[0] or 0)))
            charset, separator, data = value.partition("'")
            if separator and "'" in data:
                data = data.split("'", 1)[1]
                raw = re.sub(rb"%([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m[1].decode()), data.encode())
                try:
                    return raw.decode(charset or "utf-8", errors="replace")
                except LookupError:
                    return raw.decode("utf-8", errors="replace")
            return value
        m = re.search(rf'(?i);\s*{name}\s*=\s*(?:"([^"]*)"|([^;\s]+))', header or "")
        return (m.group(1) or m.group(2)) if m else ""


    @staticmethod
    def _decode_body(body: bytes, headers: dict[str, str]) -> str:
        enc = headers.get("content-transfer-encoding", "").lower()
        if enc == "base64":
            try:
                body = base64.b64decode(body, validate=False)
            except (binascii.Error, ValueError):
                pass
        elif enc == "quoted-printable":
            body = quopri.decodestring(body)
        charset = EmlExtractor._param(headers.get("content-type", ""), "charset") or "utf-8"
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")


    @classmethod
    def _walk(cls, raw: bytes, bodies: list[str], attachments: list[str], depth: int = 0, members=None) -> None:
        from stillfind.extractors.archives import ArchiveLimit
        from stillfind.core.errors import Stopped
        headers, body = cls._split(raw)
        ctype = headers.get("content-type", "text/plain").lower()
        mime = ctype.split(";", 1)[0].strip()
        if mime.startswith("multipart/") and depth < cls.MAX_DEPTH:
            boundary = cls._param(headers.get("content-type", ""), "boundary")
            if boundary:
                for chunk in re.split(rb"\r?\n?--" + re.escape(boundary.encode()) + rb"(?:--)?[ \t]*\r?\n?", body):
                    if chunk.strip():
                        cls._walk(chunk.lstrip(b"\r\n"), bodies, attachments, depth + 1, members)
            return
        disp = headers.get("content-disposition", "")
        filename = cls._decode_words(cls._param(disp, "filename") or cls._param(headers.get("content-type", ""), "name"))
        if not filename and (disp.lower().startswith("attachment") or mime.startswith("image/") or mime == "application/pdf"):
            types = {"application/pdf":"pdf", "text/plain":"txt", "text/html":"html", "text/csv":"csv",
                     "image/jpeg":"jpg", "image/png":"png", "image/tiff":"tif",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":"xlsx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document":"docx"}
            if mime in types:
                filename = "Allegato " + str(len(attachments)+1) + "." + types[mime]
        if filename or disp.lower().startswith("attachment"):
            if filename:
                attachments.append(filename)
                if members is not None and members.chars:
                    encoding = headers.get("content-transfer-encoding", "").lower()
                    try:
                        if encoding == "base64":
                            payload = base64.b64decode(body)
                        elif encoding == "quoted-printable":
                            payload = quopri.decodestring(body)
                        else:
                            payload = body
                        members.document(filename, payload)
                    except (Stopped, ArchiveLimit):
                        raise
                    except Exception as exc:
                        members.unreadable(filename, exc)
            return
        if mime == "message/rfc822" and depth < cls.MAX_DEPTH:
            cls._walk(body, bodies, attachments, depth + 1, members)
            return
        if mime == "text/plain":
            bodies.append(cls._decode_body(body, headers))
        elif mime == "text/html":
            text = cls._decode_body(body, headers)
            bodies.append(HtmlExtractor.strip_markup(text))

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        from stillfind.extractors.archives import Members
        cfg = request.cfg
        members = Members(cfg, request.stop)
        text = self.read(request.path, request.info, members if not cfg.get("_inside_container") else None)
        members.report(request.info)
        return text + "".join(members.parts), members.ocr

    @classmethod
    def read(cls, path: str, info=None, members=None) -> str:
        raw = Path(path).read_bytes()
        headers, _ = cls._split(raw)
        if info is not None:
            from stillfind.core.facts import dates
            stamp = next(dates(headers.get("date", "")), None)
            if stamp:
                info["document_date"] = stamp[0], "email"
        lines = [f"{label}: {cls._decode_words(headers[key])}" for label, key in cls.HEADERS if headers.get(key)]
        bodies: list[str] = []
        attachments: list[str] = []
        cls._walk(raw, bodies, attachments, members=members)
        # nelle email "alternative" il testo e l'HTML dicono la stessa cosa: si tiene il testo semplice
        text = next((b for b in bodies if b.strip()), "")
        if attachments:
            lines.append("Allegati: " + ", ".join(attachments))
        return "\n".join(lines) + "\n\n" + text.strip()
