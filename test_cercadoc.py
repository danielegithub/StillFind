#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Test automatici per CercaDoc (usa solo la libreria standard unittest)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
import zipfile
from pathlib import Path
from unittest import mock

# Usa una directory CERCADOC_HOME temporanea prima di importare cercadoc (i percorsi si leggono all'importazione)
_TEST_HOME = tempfile.mkdtemp(prefix="cercadoc_test_home_")
unittest.addModuleCleanup(shutil.rmtree, _TEST_HOME, ignore_errors=True)
os.environ["CERCADOC_HOME"] = _TEST_HOME

from cercadoc import agent, background, models, modelcheck, netlock, semantic, ui_text, config, db, duplicates, errors, export, extractors, indexer, offline, preview, stemmer  # noqa: E402
from cercadoc import search as searcher  # noqa: E402
from cercadoc.extractors import ocr, office, pdf  # noqa: E402


def _make_xlsx(path: Path) -> None:
    """Crea un file .xlsx minimale valido con shared strings, inlineStr, numeri e nomi fogli."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>
                <sheet name="Fatture 2025" sheetId="1" r:id="rId1"/>
                <sheet name="Note" sheetId="2" r:id="rId2"/>
              </sheets>
            </workbook>""")
        z.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
              <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
            </Relationships>""")
        z.writestr("xl/sharedStrings.xml", """<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Cliente Rossi</t></si>
              <si><t>Canone mensile</t></si>
            </sst>""")
        z.writestr("xl/worksheets/sheet1.xml", """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="s"><v>0</v></c>
                  <c r="B1"><v>1450.75</v></c>
                  <c r="C1"><v>20250042</v></c>
                </row>
                <row r="2">
                  <c r="A2" t="s"><v>1</v></c>
                  <c r="B2" t="e"><v>#DIV/0!</v></c>
                  <c r="C2" t="b"><v>1</v></c>
                </row>
              </sheetData>
            </worksheet>""")
        z.writestr("xl/worksheets/sheet2.xml", """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="inlineStr"><is><t>Appunto inline importante</t></is></c>
                </row>
              </sheetData>
            </worksheet>""")


def _der(tag: int, body: bytes) -> bytes:
    n = len(body)
    length = bytes([n]) if n < 128 else (bytes([0x80 | (n.bit_length() + 7) // 8]) + n.to_bytes((n.bit_length() + 7) // 8, "big"))
    return bytes([tag]) + length + body


def _pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb(num: int, value) -> bytes:
    """Un campo protobuf: intero (varint) oppure byte/testo (a lunghezza)."""
    if isinstance(value, int):
        return _pb_varint(num << 3) + _pb_varint(value)
    value = value.encode() if isinstance(value, str) else value
    return _pb_varint(num << 3 | 2) + _pb_varint(len(value)) + value


def _onnx_node(op: str, domain: str = "", subgraph: bytes | None = None) -> bytes:
    body = _pb(4, op) + (_pb(7, domain) if domain else b"")
    if subgraph is not None:
        body += _pb(5, _pb(1, "then_branch") + _pb(6, subgraph))  # attributo «g» (sottografo)
    return body


def _onnx_model(nodes: list[bytes], tensors: list[bytes] = (), functions: list[bytes] = (), opset_domain: str = "") -> bytes:
    graph = b"".join(_pb(1, n) for n in nodes) + b"".join(_pb(5, t) for t in tensors)
    model = _pb(1, 8) + _pb(7, graph) + _pb(8, _pb(1, opset_domain) + _pb(2, 17))
    return model + b"".join(_pb(25, f) for f in functions)


def _make_p7m(content: bytes, indefinite: bool = False) -> bytes:
    """CMS SignedData minimo (senza firme vere) che contiene `content`."""
    oid_signed = _der(0x06, bytes.fromhex("2a864886f70d010702"))
    oid_data = _der(0x06, bytes.fromhex("2a864886f70d010701"))
    if indefinite:  # BER come nei file reali: lunghezze indefinite e OCTET STRING a pezzi
        half = len(content) // 2
        octets = (b"\x24\x80" + _der(0x04, content[:half]) + _der(0x04, content[half:]) + b"\x00\x00")
        encap = b"\x30\x80" + oid_data + b"\xa0\x80" + octets + b"\x00\x00\x00\x00"
        signed = b"\x30\x80" + _der(0x02, b"\x01") + _der(0x31, b"") + encap + b"\x00\x00"
        return b"\x30\x80" + oid_signed + b"\xa0\x80" + signed + b"\x00\x00\x00\x00"
    encap = _der(0x30, oid_data + _der(0xA0, _der(0x04, content)))
    signed = _der(0x30, _der(0x02, b"\x01") + _der(0x31, b"") + encap)
    return _der(0x30, oid_signed + _der(0xA0, signed))


class CercaDocTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cercadoc_docs_"))
        config.CONFIG_PATH.unlink(missing_ok=True)  # ogni test parte da una configurazione pulita
        con = db.connect()
        db.reset_index(con)
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- Offline lock
    def test_snowball_stemmer_groups_italian_forms_without_accents(self):
        self.assertEqual(stemmer.stem("contratti"), stemmer.stem("Contratto"))
        self.assertEqual(stemmer.stem("fatturazione"), stemmer.stem("fatture"))
        self.assertEqual(stemmer.stem("mangiando"), stemmer.stem("mangiare"))
        self.assertEqual(stemmer.stem("perché"), "perc")  # radice senza accenti, come l'indice
        self.assertEqual(stemmer.stem_text("Contratti contratto di affitto"),
                         " ".join(sorted({"contratt", "affitt"})))

    def test_version_and_about_texts(self):
        from cercadoc import about
        text = about.version_text()
        for needle in ("CercaDoc", about.__version__, "GPL-3.0-or-later", "Daniele Sisto", "Nessuna garanzia"):
            self.assertIn(needle, text + about.NOTICE)
        self.assertIn("SENZA ALCUNA GARANZIA", about.NOTICE)

    def test_extract_eml_headers_body_and_attachments(self):
        import base64
        body = base64.b64encode("Pagamento della caparra è stato ricevuto.".encode()).decode()
        raw = ("From: =?utf-8?q?Mario_Rossi?= <mario@example.it>\r\nTo: lucia@example.it\r\n"
               "Subject: =?utf-8?q?Caparra_citt=C3=A0?=\r\nDate: Mon, 3 Nov 2025 10:00:00 +0100\r\n"
               "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"BB\"\r\n\r\n"
               "--BB\r\nContent-Type: multipart/alternative; boundary=\"AA\"\r\n\r\n"
               "--AA\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n"
               f"{body}\r\n--AA\r\nContent-Type: text/html\r\n\r\n<p>solo html</p>\r\n--AA--\r\n"
               "--BB\r\nContent-Type: application/pdf; name=\"contratto.pdf\"\r\n"
               "Content-Disposition: attachment; filename=\"contratto.pdf\"\r\n\r\nJVBERi0=\r\n--BB--\r\n")
        f = self.tmp / "mail.eml"
        f.write_bytes(raw.encode())
        text, used_ocr = extractors.extract(str(f), "eml", config.load_config())
        self.assertFalse(used_ocr)
        for needle in ("Mario Rossi", "mario@example.it", "Caparra città", "caparra è stato ricevuto",
                       "Allegati: contratto.pdf"):
            self.assertIn(needle, text)
        self.assertNotIn("solo html", text)
        self.assertNotIn("JVBERi0", text)

    def test_extract_fatturapa_readable_text(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <p:FatturaElettronica xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">
         <FatturaElettronicaHeader>
          <CedentePrestatore><DatiAnagrafici><IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>01234567890</IdCodice></IdFiscaleIVA>
           <Anagrafica><Denominazione>Idraulica Bianchi Srl</Denominazione></Anagrafica></DatiAnagrafici></CedentePrestatore>
          <CessionarioCommittente><DatiAnagrafici><CodiceFiscale>RSSMRA80A01H501U</CodiceFiscale>
           <Anagrafica><Nome>Mario</Nome><Cognome>Rossi</Cognome></Anagrafica></DatiAnagrafici></CessionarioCommittente>
         </FatturaElettronicaHeader>
         <FatturaElettronicaBody>
          <DatiGenerali><DatiGeneraliDocumento><TipoDocumento>TD01</TipoDocumento><Divisa>EUR</Divisa>
           <Data>2025-03-10</Data><Numero>FT-77/2025</Numero><ImportoTotaleDocumento>1220.00</ImportoTotaleDocumento>
          </DatiGeneraliDocumento></DatiGenerali>
          <DatiBeniServizi><DettaglioLinee><NumeroLinea>1</NumeroLinea><Descrizione>Riparazione caldaia</Descrizione>
           <Quantita>1.00</Quantita><PrezzoUnitario>1000.00</PrezzoUnitario><PrezzoTotale>1000.00</PrezzoTotale>
           <AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee></DatiBeniServizi>
         </FatturaElettronicaBody></p:FatturaElettronica>"""
        f = self.tmp / "fattura.xml"
        f.write_text(xml, encoding="utf-8")
        text, _ = extractors.extract(str(f), "xml", config.load_config())
        for needle in ("Cedente: Idraulica Bianchi Srl", "Partita IVA cedente: IT 01234567890",
                       "Cessionario: Mario Rossi", "Fattura numero FT-77/2025 del 2025-03-10",
                       "Riparazione caldaia", "1220.00"):
            self.assertIn(needle, text)
        self.assertNotIn("<", text)
        plain = self.tmp / "altro.xml"
        plain.write_text("<nota>testo qualunque</nota>", encoding="utf-8")
        self.assertIn("<nota>", extractors.extract(str(plain), "xml", config.load_config())[0])

    def test_suggest_corrects_typos_from_indexed_words_only(self):
        (self.tmp / "a.txt").write_text("La fattura del condominio e il contratto di affitto.", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        indexer.index_all(cfg)
        con = db.connect()
        self.assertEqual(searcher.suggest(con, "fatura"), "fattura")
        self.assertEqual(searcher.suggest(con, "contrato affitto"), "contratto affitto")
        self.assertEqual(searcher.suggest(con, "fatt"), None)       # prefisso di una parola reale
        self.assertEqual(searcher.suggest(con, "fattura"), None)    # già corretta
        self.assertEqual(searcher.suggest(con, "zzzzzz"), None)     # nessuna parola vicina
        con.close()

    def test_export_csv_columns_and_excel_friendly_encoding(self):
        rows = [{"name": "a.txt", "folder": "/x", "path": "/x/a.txt", "ext": "txt", "mtime": 0.0, "size": 12,
                 "snippet": f"il {searcher.HIT_START}canone{searcher.HIT_END} è alto"}]
        out = self.tmp / "ris.csv"
        self.assertEqual(export.export_csv(rows, str(out)), 1)
        raw = out.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        lines = raw.decode("utf-8-sig").splitlines()
        self.assertEqual(lines[0].split(";"), list(export.COLUMNS))
        self.assertIn("il canone è alto", lines[1])
        self.assertNotIn(searcher.HIT_START, lines[1])

    def test_metadata_title_author_subject_are_searchable_but_not_text(self):
        docx = self.tmp / "relazione.docx"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>Testo base</w:t></w:r></w:p></w:body></w:document>')
            z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="c" xmlns:dc="d"><dc:title>Piano Zeta</dc:title>'
                       '<dc:creator>Anna Verdi</dc:creator><dc:subject>Budget 2026</dc:subject></cp:coreProperties>')
        odt = self.tmp / "nota.odt"
        with zipfile.ZipFile(odt, "w") as z:
            z.writestr("content.xml", '<o:doc xmlns:o="o" xmlns:t="t"><t:p>Corpo</t:p></o:doc>')
            z.writestr("meta.xml", '<o:meta xmlns:o="o" xmlns:dc="d" xmlns:m="m"><dc:title>Verbale Gamma</dc:title>'
                       '<m:initial-creator>Luca Neri</m:initial-creator></o:meta>')
        from cercadoc.extractors.meta import read_metadata
        self.assertEqual(read_metadata(str(docx), "docx"), "Titolo: Piano Zeta\nAutore: Anna Verdi\nOggetto: Budget 2026")
        self.assertEqual(read_metadata(str(odt), "odt"), "Titolo: Verbale Gamma\nAutore: Luca Neri")
        self.assertEqual(read_metadata(str(self.tmp / "manca.pdf"), "pdf"), "")  # file illeggibile: nessun errore
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        indexer.index_all(cfg)
        con = db.connect()
        self.assertEqual([r["name"] for r in searcher.search(con, "Zeta")], ["relazione.docx"])
        self.assertEqual([r["name"] for r in searcher.search(con, "Neri")], ["nota.odt"])
        con.close()

    def test_pdf_metadata_without_text_stays_notext(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf non installato")
        w = PdfWriter()
        w.add_blank_page(200, 200)
        w.add_metadata({"/Title": "Scansione Omega", "/Author": "Ufficio"})
        f = self.tmp / "scan.pdf"
        with open(f, "wb") as fh:
            w.write(fh)
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}], ocr=False)
        indexer.index_all(cfg)
        con = db.connect()
        self.assertEqual([r["name"] for r in searcher.search(con, "Omega")], ["scan.pdf"])
        self.assertEqual(con.execute("SELECT notext FROM docs WHERE name='scan.pdf'").fetchone()[0], 1)
        con.close()

    def test_duplicates_confirmed_with_full_sha256(self):
        (self.tmp / "a.txt").write_text("contratto di affitto identico", encoding="utf-8")
        (self.tmp / "copia di a.txt").write_text("contratto di affitto identico", encoding="utf-8")
        (self.tmp / "b.txt").write_text("contratto di affitto diverso!!", encoding="utf-8")  # stessa lunghezza
        head, tail = b"H" * 70_000, b"T" * 70_000
        (self.tmp / "grande1.txt").write_bytes(head + b"x" * 1000 + tail)
        (self.tmp / "grande2.txt").write_bytes(head + b"y" * 1000 + tail)  # stessa impronta, contenuto diverso
        (self.tmp / "vuoto1.txt").write_bytes(b"")
        (self.tmp / "vuoto2.txt").write_bytes(b"")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        indexer.index_all(cfg)
        con = db.connect()
        fps = dict(con.execute("SELECT name, fp FROM docs WHERE name LIKE 'grande%'").fetchall())
        self.assertEqual(fps["grande1.txt"], fps["grande2.txt"])  # l'impronta da sola non basta
        groups = duplicates.find_duplicates(con)
        con.close()
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(Path(p).name for p in groups[0]["paths"]), ["a.txt", "copia di a.txt"])
        self.assertEqual(groups[0]["wasted"], groups[0]["size"])
        self.assertEqual(groups[0]["sha256"], duplicates.sha256_file(groups[0]["paths"][0]))

    def test_duplicates_stop_event_interrupts_hashing(self):
        f = self.tmp / "x.bin"
        f.write_bytes(b"0" * 2_500_000)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(errors.Stopped):
            duplicates.sha256_file(str(f), ev)

    def test_p7m_extracts_signed_document_in_all_encodings(self):
        import base64
        cfg = config.load_config()
        text = "Contratto di locazione firmato digitalmente".encode()
        plain = self.tmp / "contratto.txt.p7m"
        plain.write_bytes(_make_p7m(text))
        self.assertIn("locazione firmato", extractors.extract(str(plain), "p7m", cfg)[0])
        ber = self.tmp / "ber.txt.p7m"
        ber.write_bytes(_make_p7m(text, indefinite=True))
        self.assertIn("locazione firmato", extractors.extract(str(ber), "p7m", cfg)[0])
        b64 = self.tmp / "b64.txt.p7m"
        b64.write_bytes(b"-----BEGIN PKCS7-----\n" + base64.encodebytes(_make_p7m(text)) + b"-----END PKCS7-----\n")
        self.assertIn("locazione firmato", extractors.extract(str(b64), "p7m", cfg)[0])
        twice = self.tmp / "doppia.txt.p7m.p7m"
        twice.write_bytes(_make_p7m(_make_p7m(text)))
        self.assertIn("locazione firmato", extractors.extract(str(twice), "p7m", cfg)[0])

    def test_p7m_detects_inner_type_and_reads_signed_fatturapa(self):
        cfg = config.load_config()
        fattura = ("<p:FatturaElettronica xmlns:p='x'><FatturaElettronicaBody><DatiGenerali><DatiGeneraliDocumento>"
                   "<Numero>99/A</Numero><Data>2025-01-02</Data></DatiGeneraliDocumento></DatiGenerali>"
                   "</FatturaElettronicaBody></p:FatturaElettronica>").encode()
        f = self.tmp / "IT0123_00001.xml.p7m"
        f.write_bytes(_make_p7m(fattura))
        self.assertIn("Fattura numero 99/A", extractors.extract(str(f), "p7m", cfg)[0])
        # senza estensione interna nel nome: il tipo si riconosce dal contenuto
        from cercadoc.extractors import p7m
        self.assertEqual(p7m.guess_ext("scheda.p7m", b"%PDF-1.7"), "pdf")
        self.assertEqual(p7m.guess_ext("scheda.p7m", b"<?xml version='1.0'?>"), "xml")
        self.assertEqual(p7m.guess_ext("a.pdf.p7m.p7m", b"qualunque"), "pdf")

    def test_p7m_without_content_raises_clear_error(self):
        f = self.tmp / "estranea.p7m"
        f.write_bytes(b"questo non e' un file firmato")
        with self.assertRaises(RuntimeError):
            extractors.extract(str(f), "p7m", config.load_config())

    def test_search_filters_ext_dates_and_folder(self):
        import datetime as dt
        fat = self.tmp / "Fatture" / "2025"
        alt = self.tmp / "Altro"
        fat.mkdir(parents=True)
        alt.mkdir()
        files = {fat / "gennaio.txt": dt.datetime(2025, 1, 15), fat / "dicembre.txt": dt.datetime(2025, 12, 20),
                 alt / "vecchio.txt": dt.datetime(2023, 6, 1), alt / "nota.md": dt.datetime(2025, 3, 1),
                 self.tmp / "Fatturee.txt": dt.datetime(2025, 5, 5)}
        for f, when in files.items():
            f.write_text("pagamento canone", encoding="utf-8")
            os.utime(f, (when.timestamp(), when.timestamp()))
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": True}])
        indexer.index_all(cfg)
        con = db.connect()
        names = lambda q: sorted(r["name"] for r in searcher.search(con, q))  # noqa: E731
        self.assertEqual(names("canone ext:md"), ["nota.md"])
        self.assertEqual(names("canone after:2025-03"), ["Fatturee.txt", "dicembre.txt", "nota.md"])
        self.assertEqual(names("canone before:2025-01"), ["gennaio.txt", "vecchio.txt"])  # tutto gennaio compreso
        self.assertEqual(names("canone after:2025 before:2025-03-01"), ["gennaio.txt", "nota.md"])
        self.assertEqual(names("canone in:Fatture"), ["dicembre.txt", "gennaio.txt"])  # anche sottocartelle, non «Fatturee»
        self.assertEqual(names('canone in:"2025"'), ["dicembre.txt", "gennaio.txt"])
        self.assertEqual(names("ext:md"), ["nota.md"])  # solo filtri: elenco dei documenti
        self.assertEqual(names("canone after:ieri"), [])  # filtro non valido: resta testo da cercare
        q, flt = searcher.parse_filters("contratto ext:PDF in:\"Pratiche 2025\" after:2024-13")
        self.assertEqual(q, "contratto after:2024-13")
        self.assertEqual(flt, {"ext": "pdf", "in": "Pratiche 2025"})
        self.assertEqual(searcher.query_terms("canone ext:pdf in:Fatture"), ["canone"])
        con.close()

    def test_search_by_type_group_and_date_menu_filters(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 24, 12, 0).timestamp()
        for name, days in (("nuovo.md", 2), ("vecchio.md", 400), ("scheda.html", 3), ("nota.txt", 20)):
            f = self.tmp / name
            f.write_text("importo del canone", encoding="utf-8")
            os.utime(f, (now - days * 86400, now - days * 86400))
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}],
                   extensions=["txt", "md", "html"])
        indexer.index_all(cfg)
        con = db.connect()
        names = lambda **kw: sorted(r["name"] for r in searcher.search(con, "canone", **kw))  # noqa: E731
        group = list(ui_text.category_exts("Documenti di testo"))
        self.assertEqual(names(ext=group), ["nota.txt", "nuovo.md", "vecchio.md"])
        self.assertEqual(names(ext=["html"]), ["scheda.html"])
        self.assertEqual(names(after=ui_text.date_after("Ultima settimana", now)), ["nuovo.md", "scheda.html"])
        self.assertEqual(names(after=ui_text.date_after("Ultimo mese", now), ext=group), ["nota.txt", "nuovo.md"])
        self.assertEqual(names(after=ui_text.date_after("Quest'anno", now)), ["nota.txt", "nuovo.md", "scheda.html"])
        self.assertEqual([r["name"] for r in searcher.list_docs(con, group, "name", flt={"after": now - 30 * 86400})],
                         ["nota.txt", "nuovo.md"])
        con.close()

    def test_ui_text_dates_folders_and_categories_in_plain_italian(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 24, 15, 30).timestamp()
        day = lambda y, m, d: dt.datetime(y, m, d, 9, 0).timestamp()  # noqa: E731
        self.assertEqual(ui_text.relative_date(day(2026, 11, 24), now), "oggi")
        self.assertEqual(ui_text.relative_date(day(2026, 11, 23), now), "ieri")
        self.assertEqual(ui_text.relative_date(day(2026, 11, 21), now), "3 giorni fa")
        self.assertEqual(ui_text.relative_date(day(2026, 1, 12), now), "12 gen")
        self.assertEqual(ui_text.relative_date(day(2024, 3, 5), now), "5 mar 2024")
        self.assertEqual(ui_text.friendly_folder("/u/Documenti/Contratti/2025", ["/u/Documenti"], "/"),
                         "Documenti › Contratti › 2025")
        self.assertEqual(ui_text.friendly_folder("/u/Documenti", ["/u/Documenti"], "/"), "Documenti")
        self.assertEqual(ui_text.friendly_folder("/a/b/c/d", [], "/"), "c › d")
        self.assertEqual(ui_text.friendly_folder("/u/D/a/b/c/d", ["/u/D"], "/"), "D › … › c › d")
        self.assertEqual(ui_text.present_categories(["pdf", "docx", "p7m", "zzz"]),
                         ["Documenti di testo", "PDF", "File firmati"])
        self.assertIsNone(ui_text.date_after(ui_text.ANY_DATE, now))
        self.assertEqual(ui_text.date_after("Quest'anno", now), dt.datetime(2026, 1, 1).timestamp())
        self.assertEqual(ui_text.thousands(12345), "12.345")
        # ogni estensione sta in una sola categoria
        flat = [e for _, group in ui_text.CATEGORIES for e in group]
        self.assertEqual(len(flat), len(set(flat)))

    def test_short_type_filter_word_colon_extension(self):
        parse = searcher.parse_filters
        self.assertEqual(parse("atto notarile:pdf"), ("atto notarile", {"ext": "pdf"}))
        self.assertEqual(parse("notarile:PDF atto"), ("notarile atto", {"ext": "pdf"}))
        self.assertEqual(parse("atto notarile ext:pdf"), ("atto notarile", {"ext": "pdf"}))
        self.assertEqual(parse("tipo:docx contratto"), ("contratto", {"ext": "docx"}))
        # non tocca i codici e le frasi con i due punti, né i filtri di colonna
        self.assertEqual(parse("10:30 verbale"), ("10:30 verbale", {}))
        self.assertEqual(parse("prot: 123"), ("prot: 123", {}))
        self.assertEqual(parse("name:pdf"), ("name:pdf", {}))
        self.assertEqual(parse("rapporto:pdfx"), ("rapporto:pdfx", {}))
        # in una ricerca vera: parole separate e solo PDF
        (self.tmp / "a.txt").write_text("atto notarile di vendita", encoding="utf-8")
        (self.tmp / "b.md").write_text("atto notarile di vendita", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        indexer.index_all(cfg)
        con = db.connect()
        self.assertEqual([r["name"] for r in searcher.search(con, "atto notarile:txt")], ["a.txt"])
        self.assertEqual(sorted(r["name"] for r in searcher.search(con, "atto notarile")), ["a.txt", "b.md"])
        con.close()

    def test_short_words_are_not_prefix_searches(self):
        self.assertEqual(searcher.build_query("a"), '("a")')
        self.assertEqual(searcher.build_query("di"), '("di")')
        self.assertEqual(searcher.build_query("con"), '("con"*)')

    def test_background_thread_priority_is_lowered_only_for_that_thread(self):
        out = {}

        def work():
            background.lower_thread_priority()
            if sys.platform == "darwin":
                import ctypes
                out["prio"] = ctypes.CDLL(None).getpriority(3, 0)
            elif not sys.platform.startswith("win"):
                out["prio"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())

        th = threading.Thread(target=work)
        th.start()
        th.join()
        if sys.platform == "darwin":
            import ctypes
            self.assertEqual(out["prio"], 1)  # il thread è in modalità «background»
            self.assertEqual(ctypes.CDLL(None).getpriority(3, 0), 0)  # il thread principale resta normale
        elif not sys.platform.startswith("win"):
            self.assertGreaterEqual(out["prio"], 10)
            self.assertLess(os.getpriority(os.PRIO_PROCESS, threading.get_native_id()), 10)  # il thread principale no

    def test_indexing_waits_on_battery_and_resumes_when_plugged_in(self):
        for i in range(3):
            (self.tmp / f"d{i}.txt").write_text(f"documento numero {i}", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}], pause_on_battery=True)
        answers = iter([True, True, True, False])  # batteria per tre controlli, poi alimentatore
        seen = {"paused": 0}

        def progress(st):
            seen["paused"] += bool(st.get("paused"))

        with mock.patch.object(background, "on_battery", side_effect=lambda *a, **k: next(answers, False)), \
                mock.patch.object(indexer, "POWER_POLL_SECONDS", 0.01):
            st = indexer.index_all(cfg, progress)
        self.assertGreaterEqual(seen["paused"], 1)       # la finestra ha potuto mostrare «in pausa»
        self.assertEqual(st["added"], 3)                  # e poi ha letto tutto
        # senza l'opzione non controlla nemmeno la batteria
        (self.tmp / "nuovo.txt").write_text("altro documento", encoding="utf-8")
        cfg["pause_on_battery"] = False
        with mock.patch.object(background, "on_battery", side_effect=AssertionError("non va chiamata")):
            self.assertEqual(indexer.index_all(cfg)["added"], 1)

    def test_battery_pause_can_be_stopped(self):
        (self.tmp / "a.txt").write_text("documento", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}], pause_on_battery=True)
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        with mock.patch.object(background, "on_battery", return_value=True), \
                mock.patch.object(indexer, "POWER_POLL_SECONDS", 0.05):
            t0 = time.time()
            st = indexer.index_all(cfg, stop=stop)
        self.assertTrue(st.get("stopped"))
        self.assertLess(time.time() - t0, 3)

    def test_pdf_reading_stops_at_the_character_limit(self):
        class Page:
            reads = 0

            def extract_text(self):
                Page.reads += 1
                return "parola " * 100  # 700 caratteri

        class Reader:
            is_encrypted = False
            pages = [Page() for _ in range(50)]
            metadata = None

            def __init__(self, path):
                pass

        with mock.patch("pypdf.PdfReader", Reader):
            text, used_ocr = pdf.extract_pdf("finto.pdf", dict(config.load_config(), max_chars_per_file=2000, ocr=False))
        self.assertEqual(Page.reads, 3)  # 700 × 3 > 2000: le altre 47 pagine non si leggono
        self.assertFalse(used_ocr)
        self.assertGreater(len(text), 2000)

    def test_light_scan_does_not_probe_dependencies_when_nothing_failed(self):
        (self.tmp / "a.txt").write_text("documento", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        indexer.index_all(cfg)
        with mock.patch.object(indexer, "_pypdf_available", side_effect=AssertionError("controllo inutile")), \
                mock.patch.object(indexer, "_pdf_render_available", side_effect=AssertionError("controllo inutile")):
            st = indexer.index_all(cfg)  # seconda scansione, niente di nuovo e nessun errore da ritentare
        self.assertEqual((st["added"], st["updated"]), (0, 0))

    def test_stemmer_and_heavy_modules_load_only_when_needed(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, %r); import cercadoc.cli, cercadoc.indexer, cercadoc.search; "
                "heavy = [m for m in ('snowballstemmer', 'pypdf', 'customtkinter', 'tkinter') if m in sys.modules]; "
                "print(heavy); from cercadoc.stemmer import stem; stem('contratti'); "
                "print('snowballstemmer' in sys.modules)") % str(Path(__file__).resolve().parent)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, CERCADOC_HOME=_TEST_HOME)).stdout.split("\n")
        self.assertEqual(out[0], "[]")       # importare indicizzatore e ricerca non carica nulla di pesante
        self.assertEqual(out[1], "True")     # lo stemmer si carica alla prima parola

    def test_dry_run_scan_reports_changes_without_touching_the_index(self):
        f = self.tmp / "a.txt"
        f.write_text("primo documento", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        st = indexer.index_all(cfg, dry_run=True)
        self.assertEqual(st["changes"], 1)
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 0)  # non ha scritto nulla
        con.close()
        indexer.index_all(cfg)
        self.assertEqual(indexer.index_all(cfg, dry_run=True)["changes"], 0)
        f.write_text("primo documento modificato", encoding="utf-8")
        self.assertEqual(indexer.index_all(cfg, dry_run=True)["changes"], 1)
        indexer.index_all(cfg)
        f.unlink()
        self.assertEqual(indexer.index_all(cfg, dry_run=True)["changes"], 1)

    def test_agent_starts_the_worker_only_when_there_are_changes(self):
        (self.tmp / "a.txt").write_text("documento", encoding="utf-8")
        cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}])
        config.save_config(cfg)
        started = []

        class FakeWorker:
            def wait(self):
                indexer.index_all(cfg)  # il «lavoratore» fa il suo lavoro

        ag = agent.Agent(spawn=lambda: started.append(1) or FakeWorker(), log=lambda *_: None)
        self.assertTrue(ag.tick())           # file nuovo: parte il lavoratore
        self.assertFalse(ag.tick())          # nulla di nuovo: non parte
        self.assertEqual(len(started), 1)
        (self.tmp / "a.txt").write_text("documento cambiato", encoding="utf-8")
        cfg["pause_on_battery"] = True
        config.save_config(cfg)
        with mock.patch.object(background, "on_battery", return_value=True):
            self.assertFalse(ag.tick())      # a batteria non parte nemmeno se c'è da leggere
        with mock.patch.object(background, "on_battery", return_value=False):
            self.assertTrue(ag.tick())
        self.assertEqual(len(started), 2)
        config.save_config(dict(config.load_config(), folders=[]))
        self.assertFalse(ag.tick())          # senza cartelle non fa nulla

    def test_agent_allows_a_single_instance(self):
        first = agent.acquire_lock()
        self.assertIsNotNone(first)
        self.assertIsNone(agent.acquire_lock())  # un secondo agente non parte
        first.close()
        again = agent.acquire_lock()
        self.assertIsNotNone(again)
        again.close()

    def test_worker_process_indexes_and_exits(self):
        import json
        import subprocess
        (self.tmp / "a.txt").write_text("contenuto del lavoratore", encoding="utf-8")
        config.save_config(dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}]))
        root = str(Path(__file__).resolve().parent)
        run = subprocess.run([sys.executable, "-m", "cercadoc", "--worker"], cwd=root, capture_output=True, text=True,
                             env=dict(os.environ, CERCADOC_HOME=_TEST_HOME), timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        last = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual((last["phase"], last["added"]), ("done", 1))
        con = db.connect()
        self.assertEqual([r["name"] for r in searcher.search(con, "lavoratore")], ["a.txt"])
        con.close()

    def test_agent_never_loads_heavy_modules(self):
        import subprocess
        (self.tmp / "a.txt").write_text("documento", encoding="utf-8")
        config.save_config(dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}]))
        code = ("import sys; sys.path.insert(0, %r); from cercadoc import agent; "
                "a = agent.Agent(spawn=lambda: type('W', (), {'wait': lambda s: None})(), log=lambda *_: None); "
                "a.tick(); "
                "print([m for m in ('pypdf', 'snowballstemmer', 'customtkinter', 'tkinter', 'numpy', 'onnxruntime') "
                "if m in sys.modules])") % str(Path(__file__).resolve().parent)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, CERCADOC_HOME=_TEST_HOME)).stdout.strip()
        self.assertEqual(out, "[]")

    def test_vector_store_searches_in_chunks_without_loading_everything(self):
        import tracemalloc
        import numpy as np
        from cercadoc.vectors import VectorStore
        rng = np.random.default_rng(0)
        dim, n = 128, 30_000
        data = rng.standard_normal((n, dim)).astype(np.float32)
        store = VectorStore(self.tmp / "vettori", dim)
        store.add(range(1000, 1000 + n), data)
        self.assertEqual(store.vec_path.stat().st_size, n * dim)  # 1 byte per numero
        self.assertEqual((len(store), store.count()), (n, n))
        tracemalloc.start()
        top = store.search(data[1234], k=5)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        self.assertEqual(top[0][0], 1000 + 1234)          # il vettore stesso è il più simile
        self.assertGreater(top[0][1], 0.97)
        self.assertEqual(len(top), 5)
        self.assertLess(peak, 4_000_000)                    # caricarlo tutto in float32 sarebbero 15 MB
        store.remove([1000 + 1234])
        self.assertNotIn(1000 + 1234, [i for i, _ in store.search(data[1234], k=5)])
        self.assertEqual(store.count(), n - 1)
        self.assertEqual(store.compact(), 1)
        self.assertEqual((len(store), store.vec_path.stat().st_size), (n - 1, (n - 1) * dim))
        self.assertEqual(store.search(data[77], k=1)[0][0], 1000 + 77)
        with self.assertRaises(ValueError):
            store.add([1], np.zeros((1, dim + 1)))
        self.assertEqual(VectorStore(self.tmp / "vuoto", 4).search([1, 0, 0, 0]), [])

    def _write_model(self, data: bytes, name: str = "m.onnx") -> tuple[Path, str]:
        import hashlib
        p = self.tmp / name
        p.write_bytes(data)
        return p, hashlib.sha256(data).hexdigest()

    def test_model_check_accepts_a_clean_model_and_returns_the_verified_bytes(self):
        data = _onnx_model([_onnx_node("MatMul"), _onnx_node("Softmax"), _onnx_node("Add")])
        p, sha = self._write_model(data)
        rep = modelcheck.verify_model(p, sha)
        self.assertEqual((rep.nodes, sorted(op for _, op in rep.ops)), (3, ["Add", "MatMul", "Softmax"]))
        self.assertEqual(modelcheck.read_verified(p, sha), data)  # si carica proprio ciò che è stato controllato

    def test_model_check_rejects_a_changed_file_even_by_one_byte(self):
        data = _onnx_model([_onnx_node("Relu")])
        p, sha = self._write_model(data)
        p.write_bytes(data + b"\x00")  # un solo byte in più
        with self.assertRaises(modelcheck.ModelRejected) as cm:
            modelcheck.verify_model(p, sha)
        self.assertIn("SHA-256", cm.exception.args[0][0])
        with self.assertRaises(modelcheck.ModelRejected):
            modelcheck.read_verified(self.tmp / "manca.onnx", sha)

    def test_model_check_rejects_non_standard_operations_everywhere(self):
        cases = {
            "dominio personalizzato": _onnx_model([_onnx_node("Spia", "com.altro")]),
            "operazione inventata": _onnx_model([_onnx_node("ApriConnessione")]),
            "nascosta in un sottografo": _onnx_model([_onnx_node("If", subgraph=_pb(1, _onnx_node("Spia", "com.altro")))]),
            "dentro una funzione": _onnx_model([_onnx_node("Relu")], functions=[_pb(10, "com.altro") + _pb(7, _onnx_node("Relu"))]),
            "opset personalizzato": _onnx_model([_onnx_node("Relu")], opset_domain="com.altro"),
        }
        for label, data in cases.items():
            p, sha = self._write_model(data, "c.onnx")
            with self.assertRaises(modelcheck.ModelRejected, msg=label):
                modelcheck.verify_model(p, sha)
        # ma un dominio ammesso esplicitamente (es. le operazioni integrate di onnxruntime) passa
        p, sha = self._write_model(cases["dominio personalizzato"], "c.onnx")
        rep = modelcheck.verify_model(p, sha, allowed_domains={"", "ai.onnx", "com.altro"})
        self.assertIn("com.altro", rep.domains)

    def test_model_check_rejects_weights_stored_in_external_files(self):
        external = _pb(8, "pesi") + _pb(14, 1) + _pb(13, _pb(1, "location") + _pb(2, "../../segreto.bin"))
        p, sha = self._write_model(_onnx_model([_onnx_node("Relu")], tensors=[external]))
        with self.assertRaises(modelcheck.ModelRejected) as cm:
            modelcheck.verify_model(p, sha)
        self.assertIn("file esterni", cm.exception.args[0][0])
        embedded = _pb(8, "pesi") + _pb(9, b"\x00" * 64)  # pesi dentro il file: va bene
        p, sha = self._write_model(_onnx_model([_onnx_node("Relu")], tensors=[embedded]), "ok.onnx")
        modelcheck.verify_model(p, sha)

    def test_model_check_rejects_files_that_are_not_models(self):
        for label, data in {"testo": b"non sono un modello", "troncato": _onnx_model([_onnx_node("Relu")])[:-3],
                            "vuoto": b"", "script": b"#!/bin/sh\ncurl http://example.org\n"}.items():
            p, sha = self._write_model(data, "x.onnx")
            with self.assertRaises(modelcheck.ModelRejected, msg=label):
                modelcheck.verify_model(p, sha)

    def test_model_check_agrees_with_the_official_onnx_library(self):
        import json
        import subprocess
        script = (
            "import sys, json\nimport onnx\nfrom onnx import helper, TensorProto\n"
            "def mk(nodes, ops=()):\n"
            "    g = helper.make_graph(nodes, 'g', [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])],"
            " [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])])\n"
            "    return helper.make_model(g, opset_imports=[helper.make_opsetid('', 17), *ops])\n"
            "d = sys.argv[1]\n"
            "onnx.save(mk([helper.make_node('Relu', ['x'], ['y'])]), d + '/pulito.onnx')\n"
            "onnx.save(mk([helper.make_node('Spia', ['x'], ['y'], domain='com.altro')], [helper.make_opsetid('com.altro', 1)]), d + '/sporco.onnx')\n")
        out = subprocess.run([sys.executable, "-c", script, str(self.tmp)], capture_output=True, text=True)
        if out.returncode != 0:
            self.skipTest("libreria onnx non installata")
        for name, ok in (("pulito.onnx", True), ("sporco.onnx", False)):
            sha = modelcheck.sha256_file(self.tmp / name)
            if ok:
                modelcheck.verify_model(self.tmp / name, sha)
            else:
                with self.assertRaises(modelcheck.ModelRejected):
                    modelcheck.verify_model(self.tmp / name, sha)

    def test_known_model_registry_pins_a_sha256(self):
        from cercadoc import models
        for name, info in models.MODELS.items():
            self.assertRegex(info["sha256"], r"^[0-9a-f]{64}$", name)
        with self.assertRaises(modelcheck.ModelRejected):
            models.check_model("inventato", self.tmp)
        with self.assertRaises(modelcheck.ModelRejected):
            models.check_model("multilingual-e5-small", self.tmp)  # file assente

    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "isolamento solo su macOS e Linux")
    def test_system_level_network_isolation_blocks_the_network(self):
        if netlock.wrapper() is None:
            self.skipTest("nessun meccanismo di isolamento disponibile")
        result = netlock.check_isolation()
        if result is None:
            self.skipTest("isolamento non verificabile qui")
        self.assertTrue(result)
        cmd, how = netlock.wrap_command(["echo", "ciao"])
        self.assertIsNotNone(how)
        self.assertEqual(cmd[-2:], ["echo", "ciao"])

    @unittest.skipUnless(sys.platform == "darwin", "prova con sandbox-exec")
    def test_worker_runs_normally_inside_the_network_sandbox(self):
        (self.tmp / "a.txt").write_text("documento nel recinto", encoding="utf-8")
        config.save_config(dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}]))
        cwd = os.getcwd()
        os.chdir(Path(__file__).resolve().parent)
        try:
            proc = agent.spawn_worker(sandbox=True)
            out, _ = proc.communicate(timeout=60)
        finally:
            os.chdir(cwd)
        self.assertEqual(proc.returncode, 0)
        con = db.connect()
        self.assertEqual([r["name"] for r in searcher.search(con, "recinto")], ["a.txt"])
        con.close()

    def test_agent_falls_back_when_the_sandbox_cannot_start(self):
        (self.tmp / "a.txt").write_text("documento", encoding="utf-8")
        config.save_config(dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}]))
        logs = []

        class Failing:
            def wait(self):
                return 1  # il recinto non è partito

        ag = agent.Agent(spawn=lambda: Failing(), log=logs.append)
        self.assertTrue(ag.sandbox)
        with mock.patch.object(netlock, "wrapper", return_value=(["x"], "finto")):
            ag.tick()
        self.assertFalse(ag.sandbox)  # la volta dopo si parte senza recinto
        self.assertTrue(any("senza recinto" in m or "senza" in m for m in logs))
        config.save_config(dict(config.load_config(), network_sandbox=False))
        self.assertFalse(agent.Agent(log=logs.append).sandbox)  # disattivabile dalle impostazioni

    def test_offline_verified(self):
        self.assertTrue(offline.self_test_offline())

    # --- Excerpts con accenti e a capo
    def test_excerpts_accent_insensitive_nfc_and_nfd(self):
        content_nfc = "Prima riga.\nEcco perché la città di Forlì è bella.\nTerza riga."
        res = preview.excerpts(content_nfc, searcher.query_terms("perche citta"))
        self.assertTrue(res, "excerpts deve trovare parole accentate cercando senza accenti")
        piece, marks = res[0]
        highlighted = [piece[a:b] for a, b in marks]
        self.assertIn("perché", highlighted)
        self.assertIn("città", highlighted)

        # NFD (accenti decomposti, come spesso succede su macOS)
        content_nfd = unicodedata.normalize("NFD", "Test su università e perché qui.")
        res_nfd = preview.excerpts(content_nfd, ["universita", "perche"])
        self.assertTrue(res_nfd)
        piece_nfd, marks_nfd = res_nfd[0]
        hl_nfd = [unicodedata.normalize("NFC", piece_nfd[a:b]) for a, b in marks_nfd]
        self.assertIn("università", hl_nfd)
        self.assertIn("perché", hl_nfd)

    def test_excerpts_highlights_aligned_after_newlines(self):
        content = "Linea 1\nLinea 2\nLinea 3\nQui c'è contratto finale."
        res = preview.excerpts(content, ["contratto"])
        self.assertEqual(len(res), 1)
        piece, marks = res[0]
        self.assertIn("\n", piece)
        self.assertEqual([piece[a:b] for a, b in marks], ["contratto"])

    # --- XLSX con numeri, valori, inlineStr e nomi fogli
    def test_extract_xlsx_numbers_and_sheets(self):
        p = self.tmp / "bilancio.xlsx"
        _make_xlsx(p)
        txt = extractors.extract_xlsx(str(p))
        self.assertIn("[Fatture 2025]", txt)
        self.assertIn("Cliente Rossi | 1450.75 | 20250042", txt)
        self.assertIn("Canone mensile | VERO", txt)
        self.assertNotIn("#DIV/0!", txt)
        self.assertIn("[Note]", txt)
        self.assertIn("Appunto inline importante", txt)

    def test_zip_bomb_limit(self):
        p = self.tmp / "bomb.docx"
        with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<w:document><w:p><w:t>" + ("A" * 5000) + "</w:t></w:p></w:document>")
        with mock.patch.object(office, "MAX_ZIP_XML_BYTES", 1024):
            with self.assertRaises(RuntimeError):
                with zipfile.ZipFile(p) as z:
                    office._read_zip_member(z, "word/document.xml", max_bytes=1024)

    # --- RTF senza fonttbl/colortbl/pict
    def test_extract_rtf_strips_metadata_and_pict(self):
        p = self.tmp / "nota.rtf"
        p.write_bytes(
            br"{\rtf1\ansi\deff0"
            br"{\fonttbl{\f0 Arial;}{\f1 Times New Roman;}}"
            br"{\colortbl;\red0\green0\blue0;}"
            br"{\stylesheet{\s0 Normal;}}"
            br"{\pict\pngblip 89504e470d0a1a0a0000000d49484452}"
            br"{\*\generator Riched20;}"
            br"\par Contratto d\'e8locazione \u232? valido.\par Seconda riga.}"
        )
        txt = extractors.extract_rtf(str(p))
        self.assertNotIn("Arial", txt)
        self.assertNotIn("Times New Roman", txt)
        self.assertNotIn("89504e47", txt)
        self.assertNotIn("Riched20", txt)
        self.assertIn("Contratto dèlocazione è valido.", txt)
        self.assertIn("Seconda riga.", txt)

    # --- Query con caratteri comuni (:, (, ))
    def test_build_query_handles_colons_and_parens(self):
        q1 = searcher.build_query("riunione 10:30 (copia)")
        self.assertIn("AND", q1)
        self.assertIn("*", q1)  # mantiene ricerca semplice con prefissi/stemming

        # Sintassi FTS5 esplicita resta intatta
        self.assertEqual(searcher.build_query("name:preventivo"), "name:preventivo")
        # Senza filtro colonna esplicito la sintassi FTS5 resta su nome e contenuto (mai `stem`)
        self.assertEqual(searcher.build_query("affitto OR locazione"), "{name content} : (affitto OR locazione)")
        self.assertEqual(searcher.build_query('"canone mensile"'), '{name content} : ("canone mensile")')

        # query_terms non mangia 10:30 ma toglie name:
        self.assertEqual(set(searcher.query_terms("10:30")), {"10", "30"})
        self.assertEqual(searcher.query_terms("name:preventivo"), ["preventivo"])

    # --- Sezione 4 / caso 1: la frase tra virgolette rispetta l'ordine delle parole
    def test_quoted_phrase_respects_word_order(self):
        (self.tmp / "a.txt").write_text("Riepilogo dell'area nord dello stabilimento.", encoding="utf-8")
        (self.tmp / "b.txt").write_text("Nord area: dati del reparto.", encoding="utf-8")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}])
        indexer.index_all(cfg)
        con = db.connect()
        try:
            names = lambda q: sorted(r["name"] for r in searcher.search(con, q))
            self.assertEqual(names('"area nord"'), ["a.txt"])
            self.assertEqual(names('"nord area"'), ["b.txt"])
            self.assertEqual(names("area nord"), ["a.txt", "b.txt"])  # senza virgolette l'ordine non conta
            # il filtro colonna esplicito resta rispettato
            self.assertEqual(names('name:"a"'), ["a.txt"])
        finally:
            con.close()

    # --- Sezione 4 / caso 2: i numeri puri si cercano esatti, il prefisso solo con 123*
    def test_pure_numbers_are_exact_unless_wildcard(self):
        (self.tmp / "uno.txt").write_text("Fattura numero 123 del mese.", encoding="utf-8")
        (self.tmp / "due.txt").write_text("Fattura numero 1234 del mese.", encoding="utf-8")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}])
        indexer.index_all(cfg)
        con = db.connect()
        try:
            names = lambda q: sorted(r["name"] for r in searcher.search(con, q))
            self.assertEqual(names("123"), ["uno.txt"])
            self.assertEqual(names("1234"), ["due.txt"])
            self.assertEqual(names("123*"), ["due.txt", "uno.txt"])
            # le parole normali mantengono il prefisso
            self.assertEqual(names("fattur"), ["due.txt", "uno.txt"])
        finally:
            con.close()

    # --- Sezione 4 / caso 3: codici con separatori interni cercati come frase
    def test_codes_with_separators_are_phrase_searches(self):
        self.assertEqual(searcher.build_query("PR-00124/2025"), '"PR 00124 2025"')
        self.assertEqual(searcher.build_query("10:30"), '"10 30"')
        self.assertEqual(searcher.build_query("12.500,00"), '"12 500 00"')
        # punteggiatura finale o isolata non è un separatore interno
        self.assertNotIn('" "', searcher.build_query("prot: 123"))
        self.assertEqual(searcher.build_query("ciao,"), '("ciao"* OR {stem nstem} : "cia"*)')
        (self.tmp / "ok.txt").write_text("Pratica PR-00124/2025 approvata.", encoding="utf-8")
        (self.tmp / "sparso.txt").write_text("Voce PR e poi 2025 ma anche 00124 altrove.", encoding="utf-8")
        (self.tmp / "zeri.txt").write_text("Pratica PR-124/2025 diversa.", encoding="utf-8")
        (self.tmp / "importo.txt").write_text("Totale 12.500,00 euro; ore 10:30.", encoding="utf-8")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}])
        indexer.index_all(cfg)
        con = db.connect()
        try:
            names = lambda q: sorted(r["name"] for r in searcher.search(con, q))
            self.assertEqual(names("PR-00124/2025"), ["ok.txt"])
            self.assertEqual(names("12.500,00"), ["importo.txt"])
            self.assertEqual(names("10:30"), ["importo.txt"])
        finally:
            con.close()

    # --- Sezione 4 / caso 4: PDF misto, OCR attivato dopo la prima indicizzazione
    def test_mixed_pdf_is_reindexed_when_ocr_is_enabled_later(self):
        pdf = self.tmp / "misto.pdf"
        pdf.write_bytes(b"%PDF-finto")
        pages = [mock.MagicMock(extract_text=mock.MagicMock(return_value=t)) for t in (
            "Copertina digitale con testo abbastanza lungo da superare la soglia di quaranta.", "", "  ")]
        reader = mock.MagicMock(is_encrypted=False, pages=pages)
        base = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}])
        with mock.patch("pypdf.PdfReader", return_value=reader), \
             mock.patch.object(ocr, "ocr_available", return_value=True):
            # 1) OCR spento: la copertina è indicizzata, restano 2 pagine senza testo
            self.assertEqual(indexer.index_all(dict(base, ocr=False))["added"], 1)
            con = db.connect()
            row = con.execute("SELECT notext, ocr, blank_pages FROM docs").fetchone()
            con.close()
            self.assertEqual(row, (0, 0, 2))
            # 2) nulla cambia: file invariato e OCR ancora spento -> non viene riletto
            self.assertEqual(indexer.index_all(dict(base, ocr=False))["updated"], 0)
            # 3) OCR attivato: il PDF viene riletto e le pagine scansionate entrano nell'indice
            with mock.patch.object(ocr, "ocr_pdf_pages",
                                   return_value={2: "Scansione clausola recesso", 3: "Scansione allegato"}) as m_ocr:
                self.assertEqual(indexer.index_all(dict(base, ocr=True))["updated"], 1)
            self.assertEqual(m_ocr.call_args[0][1], [2, 3])
            con = db.connect()
            try:
                self.assertEqual([r["name"] for r in searcher.search(con, "recesso")], ["misto.pdf"])
                self.assertEqual(con.execute("SELECT ocr FROM docs").fetchone()[0], 1)
            finally:
                con.close()
            # 4) OCR già fatto: non si ripete a ogni scansione
            self.assertEqual(indexer.index_all(dict(base, ocr=True))["updated"], 0)

    # --- Anteprima di un PDF grande: estratto subito, occorrenze complete in un altro thread
    def test_preview_shows_snippet_instantly_and_loads_full_text_in_thread(self):
        filler = "lorem ipsum dolor sit amet " * 40_000  # ~1 MB di testo
        f = self.tmp / "grande.txt"
        f.write_text("Premessa: il canone mensile è fissato.\n" + filler + "\nFine: canone di locazione.",
                     encoding="utf-8")
        indexer.index_all(dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}]))
        con = db.connect()
        try:
            r = searcher.search(con, "canone")[0]
        finally:
            con.close()

        # 1) l'estratto arriva già dalla ricerca, senza rileggere il testo del documento
        quick = preview.snippet_parts(r["snippet"])
        self.assertIn(("canone", "hit"), quick)
        joined = "".join(t for t, _ in preview.snippet_parts(f"…un {searcher.HIT_START}canone{searcher.HIT_END} qui…"))
        self.assertEqual(joined, "…un canone qui…\n")             # i puntini di FTS5 non si raddoppiano
        self.assertEqual("".join(t for t, _ in preview.snippet_parts(f"{searcher.HIT_START}canone{searcher.HIT_END} qui"))[:6],
                         "canone")                               # nessun puntino se il testo non è tagliato
        self.assertEqual(preview.snippet_parts(""), [])           # match solo sul nome: nessun estratto
        self.assertEqual(preview.snippet_parts("senza marcatori"), [])

        # 2) il testo completo si legge da un altro thread (connessione propria)
        out: dict = {}

        def work():
            out["content"] = preview.read_content(r["id"])
            out["parts"] = preview.preview_parts(out["content"], searcher.query_terms("canone"))

        t = threading.Thread(target=work)
        t.start()
        t.join(timeout=30)
        self.assertFalse(t.is_alive())
        hits = [text for text, tag in out["parts"] if tag == "hit"]
        self.assertGreaterEqual(len(hits), 2)                   # inizio e fine del documento
        self.assertTrue(all(h.lower() == "canone" for h in hits))
        self.assertEqual(preview.read_content(10**9), "")          # documento inesistente

        # 3) casi particolari dell'anteprima completa
        self.assertIn("Nessun testo estratto", preview.preview_parts("   ", ["x"])[0][0])
        self.assertIn("trovato nel nome del file", preview.preview_parts("testo qualunque", ["assente"])[0][0])

    def test_full_text_preview_highlights_every_occurrence_and_navigation_data(self):
        text = "Il Canone è dovuto. Perché il canone cresce, la città paga il CANONE.\r\nFine."
        spans = preview.hit_spans(text, ["canone", "perche"])
        self.assertEqual([text[a:b] for a, b in spans], ["Canone", "Perché", "canone", "CANONE"])  # accenti e maiuscole
        parts = preview.full_text_parts(text, ["canone"])
        self.assertEqual("".join(t for t, _ in parts), text.replace("\r\n", "\n"))  # nessun carattere perso
        self.assertEqual([t for t, tag in parts if tag == "hit"], ["Canone", "canone", "CANONE"])
        self.assertEqual(preview.hit_spans(text, []), [])
        self.assertIn("Nessun testo estratto", preview.full_text_parts("  ", ["x"])[0][0])

    def test_hit_spans_are_not_lost_or_doubled_across_block_boundaries(self):
        block = 100_000  # il testo si legge a blocchi di 100.000 caratteri
        for shift in (-3, 0, 3, 40, 63):
            text = "x" * (block - 64 + shift) + " canone " + "y" * 200_000 + " canone"
            spans = preview.hit_spans(text, ["canone"])
            self.assertEqual([text[a:b] for a, b in spans], ["canone", "canone"], shift)
            self.assertEqual(len(set(spans)), 2)

    def test_full_text_preview_is_cut_but_says_so(self):
        text = "canone " * 100
        parts = preview.full_text_parts(text, ["canone"], limit=70)
        self.assertEqual(sum(1 for _, tag in parts if tag == "hit"), 10)
        self.assertIn("primi 70 caratteri su 700", parts[-1][0])
        self.assertEqual(len(preview.hit_spans("canone " * 50, ["canone"], limit=7)), 7)

    def test_occurrence_view_can_show_more_excerpts(self):
        text = " ".join(f"riga {i} canone" + " riempimento" * 40 for i in range(80))
        few = [tag for _, tag in preview.preview_parts(text, ["canone"]) if tag == "hit"]
        many = [tag for _, tag in preview.preview_parts(text, ["canone"], max_n=50) if tag == "hit"]
        self.assertEqual((len(few), len(many)), (8, 50))

    # --- «Cerca in»: nome e contenuto, solo contenuto, solo nome
    def _where_names(self, con, q, where, **kw):
        return sorted(r["name"] for r in searcher.search(con, q, where=where, **kw))

    def test_search_in_content_only_or_name_only(self):
        (self.tmp / "contratti.txt").write_text("nulla di rilevante qui", encoding="utf-8")   # solo nel nome (plurale)
        (self.tmp / "note.txt").write_text("il contratto di affitto è scaduto", encoding="utf-8")  # solo nel contenuto
        (self.tmp / "contratto_nota.txt").write_text("contratti firmati", encoding="utf-8")   # in entrambi
        indexer.index_all(dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": False}]))
        con = db.connect()
        try:
            self.assertEqual(self._where_names(con, "contratto", "all"), ["contratti.txt", "contratto_nota.txt", "note.txt"])
            # nel contenuto non conta il nome, nemmeno con una forma diversa della parola (contratti ~ contratto)
            self.assertEqual(self._where_names(con, "contratto", "content"), ["contratto_nota.txt", "note.txt"])
            self.assertEqual(self._where_names(con, "contratti", "content"), ["contratto_nota.txt", "note.txt"])  # stemming nel contenuto
            self.assertEqual(self._where_names(con, "contratto", "name"), ["contratti.txt", "contratto_nota.txt"])  # stemming nel nome
            self.assertEqual(self._where_names(con, "affitto scaduto", "content"), ["note.txt"])  # più parole
            self.assertEqual(self._where_names(con, "affitto", "name"), [])
            self.assertEqual(self._where_names(con, '"contratto di affitto"', "content"), ["note.txt"])  # frase
            self.assertEqual(self._where_names(con, '"contratto di affitto"', "name"), [])
            self.assertEqual(self._where_names(con, "contratti NOT affitto", "content"), ["contratto_nota.txt"])  # con gli operatori: parole esatte
            self.assertEqual(self._where_names(con, "name:contratti", "content"), ["contratti.txt"])  # il filtro scritto vince
            self.assertEqual(self._where_names(con, "contratto ext:txt", "name", ), ["contratti.txt", "contratto_nota.txt"])
            by_name = searcher.search(con, "contratto", where="name")
            self.assertTrue(all(searcher.HIT_START not in r["snippet"] for r in by_name))  # nessuna parola evidenziata nel testo
            # un file rinominato mantiene la ricerca per nome
            (self.tmp / "note.txt").rename(self.tmp / "disdetta_locazione.txt")
            indexer.index_all(dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": False}]))
            self.assertEqual(self._where_names(con, "disdette", "name"), ["disdetta_locazione.txt"])
            self.assertEqual(self._where_names(con, "affitto", "content"), ["disdetta_locazione.txt"])
        finally:
            con.close()

    def test_search_in_a_date_range_with_before_and_after(self):
        from datetime import datetime
        stamps = {"gennaio.txt": datetime(2025, 1, 15), "marzo.txt": datetime(2025, 3, 10), "giugno.txt": datetime(2025, 6, 20)}
        for name, when in stamps.items():
            f = self.tmp / name
            f.write_text("relazione trimestrale", encoding="utf-8")
            os.utime(f, (when.timestamp(), when.timestamp()))
        indexer.index_all(dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": False}]))
        con = db.connect()
        try:
            a, b = ui_text.parse_date_range("01/02/2025", "31/03/2025")[:2]
            self.assertEqual(sorted(r["name"] for r in searcher.search(con, "relazione", after=a, before=b)), ["marzo.txt"])
            self.assertEqual(sorted(r["name"] for r in searcher.search(con, "relazione", before=b)), ["gennaio.txt", "marzo.txt"])
            self.assertEqual(len(searcher.search(con, "relazione", after=a)), 2)
            # insieme a un filtro scritto: vale il più stretto
            self.assertEqual([r["name"] for r in searcher.search(con, "relazione after:2025-03 before:2025-12", before=b)], ["marzo.txt"])
            # senza testo si elencano i documenti del periodo
            self.assertEqual([r["name"] for r in searcher.list_docs(con, None, "name", flt={"after": a, "before": b})], ["marzo.txt"])
        finally:
            con.close()

    def test_date_ranges_written_by_hand(self):
        from datetime import datetime
        day = lambda y, m, d: datetime(y, m, d).timestamp()  # noqa: E731
        self.assertEqual(ui_text.parse_date_range("12/03/2025", "20/04/2025")[:2], (day(2025, 3, 12), day(2025, 4, 21)))
        self.assertEqual(ui_text.parse_date_range("03/2025", "03/2025")[:2], (day(2025, 3, 1), day(2025, 4, 1)))   # tutto il mese
        self.assertEqual(ui_text.parse_date_range("2024", "2024")[:2], (day(2024, 1, 1), day(2025, 1, 1)))        # tutto l'anno
        self.assertEqual(ui_text.parse_date_range("", "12/2025")[:2], (None, day(2026, 1, 1)))                    # fino a dicembre compreso
        self.assertEqual(ui_text.parse_date_range("1.2.2025", "")[2], "dal 01/02/2025")
        self.assertEqual(ui_text.parse_date_range("2025-12-31", "31-12-2025")[:2], (day(2025, 12, 31), day(2026, 1, 1)))
        for bad in ("ieri", "31/02/2025", "13/2025", "12/03/25", "1/2/3/2025", "abc"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                ui_text.parse_date_range(bad, "")
            self.assertIn("31/12/2025", str(cm.exception))
        with self.assertRaises(ValueError):
            ui_text.parse_date_range("", "")
        with self.assertRaises(ValueError) as cm:
            ui_text.parse_date_range("05/2025", "03/2025")
        self.assertIn("prima", str(cm.exception))

    # --- Pagine dei PDF e occorrenze espandibili
    @staticmethod
    def _fake_pdf_reader(texts):
        pages = [mock.MagicMock(extract_text=mock.MagicMock(return_value=t)) for t in texts]
        return mock.MagicMock(is_encrypted=False, pages=pages, metadata=None)

    PAGES = ("Prima pagina: premessa generale del documento senza altro da dire qui dentro.",
             "Seconda pagina: clausole varie, durata e rinnovo del contratto di locazione abitativa.",
             "Terza pagina: il canone mensile è fissato in euro 850 da pagare entro il giorno cinque.")

    def test_extract_pdf_records_where_each_page_starts(self):
        info: dict = {}
        with mock.patch("pypdf.PdfReader", return_value=self._fake_pdf_reader(self.PAGES)):
            text, _ = pdf.extract_pdf("x.pdf", dict(config.DEFAULT_CONFIG, ocr=False), info=info)
        starts = info["pages"]
        self.assertEqual(len(starts), 3)
        for n, page_text in enumerate(self.PAGES):
            self.assertEqual(text[starts[n]:starts[n] + len(page_text)], page_text)  # ogni pagina comincia dove dichiarato

    def test_results_say_on_which_page_the_passage_is(self):
        (self.tmp / "contratto.pdf").write_bytes(b"%PDF-finto")
        (self.tmp / "note.txt").write_text("il canone dipende dal contratto", encoding="utf-8")
        cfg = dict(config.DEFAULT_CONFIG, ocr=False, folders=[{"path": str(self.tmp), "recursive": False}])
        with mock.patch("pypdf.PdfReader", return_value=self._fake_pdf_reader(self.PAGES)), \
             mock.patch("cercadoc.extractors.meta.read_metadata", return_value="Titolo: Contratto affitto"):
            indexer.index_all(cfg)  # con i metadati davanti al testo: le posizioni delle pagine si spostano di conseguenza
        con = db.connect()
        try:
            rows = searcher.search(con, "canone")
            searcher.add_pages(con, rows)
            by_name = {r["name"]: r["page"] for r in rows}
            self.assertEqual(by_name, {"contratto.pdf": 3, "note.txt": None})  # il testo semplice non ha pagine
            rows = searcher.search(con, "locazione")
            searcher.add_pages(con, rows)
            self.assertEqual(rows[0]["page"], 2)
            rows = searcher.search(con, "premessa")
            searcher.add_pages(con, rows)
            self.assertEqual(rows[0]["page"], 1)
            only_name = searcher.search(con, "contratto", where="name")
            searcher.add_pages(con, only_name)
            self.assertIsNone(only_name[0]["page"])                                  # trovato solo nel nome
            row = {"id": rows[0]["id"], "snippet": "", "offset": 0}
            searcher.add_pages(con, [row])
            self.assertEqual(row["page"], 1)                                          # trovato per significato: arriva `offset`
            self.assertEqual(searcher.page_at(con, rows[0]["id"], 10**6), 3)          # oltre la fine: l'ultima pagina
            self.assertIsNone(searcher.page_at(con, 10**9, 5))
            # un file cambiato o sparito perde le sue pagine
            self.assertEqual(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 3)
            (self.tmp / "contratto.pdf").write_bytes(b"%PDF-finto-modificato")
            with mock.patch("pypdf.PdfReader", return_value=self._fake_pdf_reader(self.PAGES[:2])):
                indexer.index_all(cfg)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 2)
            (self.tmp / "contratto.pdf").unlink()
            indexer.index_all(cfg)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 0)
        finally:
            con.close()

    def test_occurrence_list_has_page_context_and_position_in_the_full_text(self):
        pages = ["Prima pagina senza nulla di rilevante. " * 3, "Il canone mensile è dovuto e il canone cresce. " + "Testo vario. " * 20,
                 "Ancora il canone, e poi " + "altro " * 60 + "il canone annuale."]
        text, starts, pos = "", [], 0
        for t in pages:
            starts.append(len(text) if not text else len(text) + 1)
            text = t if not text else text + "\n" + t
        items, total = preview.occurrences(text, ["canone"], starts)
        self.assertEqual(total, 4)
        self.assertEqual([i["page"] for i in items], [2, 3, 3])      # due occorrenze vicine stanno nella stessa voce
        self.assertEqual([i["index"] for i in items], [0, 2, 3])   # la 1 è unita alla 0
        self.assertEqual([i["count"] for i in items], [2, 1, 1])   # quante occorrenze contiene ogni voce
        self.assertEqual(sum(i["count"] for i in items), total)
        # vicine ma su pagine diverse restano voci separate: il numero di pagina conta
        close, close_starts = "Il canone qui.\nIl canone là.", [0, 15]
        self.assertEqual([i["page"] for i in preview.occurrences(close, ["canone"], close_starts)[0]], [1, 2])
        self.assertEqual(len(preview.occurrences(close, ["canone"])[0]), 1)  # senza pagine: stessa voce
        hits = [t for t, tag in items[0]["parts"] if tag == "hit"]
        self.assertEqual(hits, ["canone", "canone"])                  # le due occorrenze vicine sono evidenziate insieme
        self.assertTrue(items[0]["parts"][0][0].startswith("…"))      # il contesto è un pezzo del testo, con i puntini
        # lo stesso ordine del testo completo: l'indice di una voce porta all'occorrenza giusta nell'anteprima
        full = [t for t, tag in preview.full_text_parts(text, ["canone"]) if tag == "hit"]
        self.assertEqual(len(full), total)
        limited, all_found = preview.occurrences(text, ["canone"], starts, max_items=1)
        self.assertEqual((len(limited), all_found, limited[0]["count"]), (1, 4, 2))  # il limite non cambia il totale
        self.assertEqual(preview.occurrences("niente", ["canone"]), ([], 0))
        self.assertIsNone(preview.occurrences(text, ["canone"])[0][0]["page"])  # senza pagine (testo semplice)

    # --- Più parole: prima i documenti con le parole nello stesso passaggio
    def test_multi_word_search_prefers_same_passage_but_keeps_distant_matches(self):
        fill = lambda n: " ".join(f"parola{i}" for i in range(n))  # noqa: E731
        (self.tmp / "a_lontane.txt").write_text(
            "contratto " + fill(60) + " canone " + fill(60), encoding="utf-8")
        (self.tmp / "b_vicine.txt").write_text(
            fill(800) + " il canone del contratto mensile " + fill(800), encoding="utf-8")
        (self.tmp / "c_solo_una.txt").write_text("contratto " + fill(5), encoding="utf-8")
        indexer.index_all(dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}]))
        con = db.connect()
        try:
            # senza la precedenza il documento corto con le parole lontane sarebbe il primo (bm25)
            plain = [r[0] for r in con.execute(
                "SELECT d.name FROM fts JOIN docs d ON d.id = fts.rowid WHERE fts MATCH ? "
                "ORDER BY bm25(fts, 8.0, 1.0, 0.3)", (searcher.build_query("contratto canone"),))]
            self.assertEqual(plain, ["a_lontane.txt", "b_vicine.txt"])
            # con la precedenza: prima le parole vicine, ma anche quelle lontane restano reperibili
            ranked = [r["name"] for r in searcher.search(con, "contratto canone")]
            self.assertEqual(ranked, ["b_vicine.txt", "a_lontane.txt"])
            self.assertEqual([r["name"] for r in searcher.search(con, "contratti canoni")], ranked)  # stemming
            # l'ordinamento scelto dall'utente non cambia
            self.assertEqual([r["name"] for r in searcher.search(con, "contratto canone", order="name")],
                             ["a_lontane.txt", "b_vicine.txt"])
        finally:
            con.close()
        self.assertEqual(searcher._passage_query("contratto"), "")                 # una parola sola
        self.assertEqual(searcher._passage_query('"area nord" canone'), "")        # sintassi esplicita
        self.assertIn('"PR 00124 2025"', searcher._passage_query("PR-00124/2025 canone"))

    def test_sheet_and_slide_positions_follow_the_document_order(self):
        _make_xlsx(self.tmp / "libro.xlsx")
        with zipfile.ZipFile(self.tmp / "relazione.pptx", "w") as z:
            z.writestr("ppt/presentation.xml", '<pres xmlns:r="urn:rel"><sldIdLst><sldId id="72" r:id="b"/>'
                       '<sldId id="64" r:id="a"/></sldIdLst></pres>')
            z.writestr("ppt/_rels/presentation.xml.rels", '<rels><rel Id="a" Target="slides/slide1.xml"/>'
                       '<rel Id="b" Target="slides/slide9.xml"/></rels>')
            z.writestr("ppt/slides/slide1.xml", "<slide><p><t>Seconda diapositiva</t></p></slide>")
            z.writestr("ppt/slides/slide9.xml", "<slide><p><t>Prima diapositiva</t></p></slide>")
            z.writestr("ppt/slides/_rels/slide1.xml.rels", '<rels><rel Id="n" Target="../notesSlides/notesSlide4.xml"/></rels>')
            z.writestr("ppt/notesSlides/notesSlide4.xml", "<notes><p><t>Riservato</t><br/><t>Note del relatore</t></p></notes>")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp)}])
        self.assertEqual(indexer.index_all(cfg)["added"], 2)
        with db.connect() as con:
            for term, location in (("Appunto", "Foglio Note"), ("mensile", "Foglio Fatture 2025"),
                                   ("Prima", "Slide 1"), ("Riservato", "Slide 2")):
                row, = searcher.search(con, term)
                searcher.add_pages(con, [row])
                self.assertEqual(row["location"], location)
                self.assertIsNone(row["page"])
                content, layout = preview.read_document(row["id"])
                parts = preview.preview_parts(content, [term], layout=layout)
                self.assertIn((location + "\n", "title"), parts)
                if term == "Riservato":
                    self.assertIn("\nNote del relatore", "".join(t for t, _ in parts))
                    self.assertNotIn("Prima diapositiva", "".join(t for t, _ in parts))
        con.close()
        self.assertEqual(indexer.index_all(cfg)["updated"], 0)

    def test_same_paragraph_beats_adjacent_terms_across_a_break(self):
        (self.tmp / "separati.txt").write_text("alfa\n\nbeta")
        (self.tmp / "insieme.txt").write_text("alfa " + "intervallo " * 80 + "beta")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp)}])
        indexer.index_all(cfg)
        with db.connect() as con:
            rows = searcher.search(con, "alfa beta")
            self.assertEqual([r["name"] for r in rows], ["insieme.txt", "separati.txt"])
            self.assertIsNone(rows[1]["passage_start"])
            content, layout = preview.read_document(rows[1]["id"])
            self.assertNotIn("beta", "".join(t for t, _ in preview.preview_parts(content, ["alfa"], layout=layout)))
            spans = semantic._document_spans(con, rows[1]["id"], content, 5)
            self.assertEqual([content[a:b] for a, b in spans], ["alfa", "beta"])
        con.close()

    def test_ocr_extension_only_reads_newly_eligible_pages(self):
        (self.tmp / "misto.pdf").write_bytes(b"%PDF-campione")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp)}], ocr=True, ocr_max_pages=2)
        reader = self._fake_pdf_reader(["Testo digitale " * 5, "", "", ""])
        calls = []
        def recognize(path, pages, cfg, **kwargs):
            calls.append(pages)
            return {p: ("Riconosciuto pagina " + str(p) if p == 2 else "") for p in pages}
        with mock.patch("pypdf.PdfReader", return_value=reader), \
             mock.patch.object(ocr, "ocr_available", return_value=True), \
             mock.patch.object(ocr, "ocr_pdf_pages", side_effect=recognize):
            indexer.index_all(cfg)
            cfg["ocr_max_pages"] = 3
            self.assertTrue(indexer.index_all(cfg, dry_run=True)["changes"])
            indexer.index_all(cfg)
            self.assertEqual(indexer.index_all(cfg)["updated"], 0)
        self.assertEqual(calls, [[2], [3]])
        with db.connect() as con:
            row, = searcher.search(con, "Riconosciuto")
            searcher.add_pages(con, [row])
            self.assertEqual(row["location"], "Pag. 2 · OCR")
            content, layout = preview.read_document(row["id"])
            self.assertEqual([s[-1] for s in layout.sections], ["native", "ocr", "ocr_empty", "pending"])
            parts = preview.preview_parts(content, ["Riconosciuto"], layout=layout)
            self.assertIn(("Pag. 2 · OCR\n", "title"), parts)
            full = "".join(t for t, _ in preview.full_text_parts(content, [], layout=layout))
            self.assertIn("Pag. 3 · OCR senza testo", full)
            self.assertIn("Pag. 4 · OCR da eseguire", full)
        con.close()

    def test_pdf_ocr_failure_is_local_to_one_page(self):
        (self.tmp / "pratica.pdf").write_bytes(b"%PDF-prova")
        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp)}], ocr=True)
        pages = ["Prospetto digitale con un paragrafo certamente leggibile", "", ""]
        from contextlib import contextmanager
        @contextmanager
        def renderer(path, tmp, stop):
            yield lambda n: str(n)
        def recognize(image, lang, stop):
            if image == "2":
                raise RuntimeError("pagina illeggibile")
            return "Ultima pagina riconosciuta"
        with mock.patch("pypdf.PdfReader", return_value=self._fake_pdf_reader(pages)), \
             mock.patch.object(ocr, "ocr_available", return_value=True), \
             mock.patch.object(ocr, "_render_pages", renderer), mock.patch.object(ocr, "_tesseract", recognize):
            self.assertEqual(indexer.index_all(cfg)["errors"], 1)
            self.assertEqual(indexer.index_all(cfg)["updated"], 0)
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT lettura FROM pages ORDER BY n").fetchall(),
                             [("native",), ("error",), ("ocr",)])
            self.assertTrue(searcher.search(con, "Prospetto digitale"))
            row, = searcher.search(con, "Ultima pagina")
            searcher.add_pages(con, [row])
            self.assertEqual(row["location"], "Pag. 3 · OCR")
        con.close()

    # --- PDF pagina-per-pagina OCR + PDF protetto da password
    def test_extract_pdf_mixed_pages_triggers_page_ocr(self):
        fake_pages = [
            mock.MagicMock(extract_text=mock.MagicMock(
                return_value="Intestazione digitale abbastanza lunga da superare la soglia di quaranta caratteri.")),
            mock.MagicMock(extract_text=mock.MagicMock(return_value="")),
            mock.MagicMock(extract_text=mock.MagicMock(return_value="   ")),
        ]
        fake_reader = mock.MagicMock(is_encrypted=False, pages=fake_pages)
        cfg = dict(config.DEFAULT_CONFIG, ocr=True, ocr_max_pages=50)
        with mock.patch("pypdf.PdfReader", return_value=fake_reader), \
             mock.patch.object(ocr, "ocr_available", return_value=True), \
             mock.patch.object(ocr, "ocr_pdf_pages", return_value={2: "Testo pagina due", 3: "Testo pagina tre"}) as m_ocr:
            txt, ocr_used = pdf.extract_pdf("dummy.pdf", cfg)
        self.assertTrue(ocr_used)
        m_ocr.assert_called_once()
        self.assertEqual(m_ocr.call_args[0][1], [2, 3])  # OCR solo sulle pagine 2 e 3!
        self.assertIn("Intestazione digitale", txt)
        self.assertIn("Testo pagina due", txt)
        self.assertIn("Testo pagina tre", txt)

    def test_extract_pdf_encrypted_raises_clear_error(self):
        fake_reader = mock.MagicMock(is_encrypted=True)
        fake_reader.decrypt.return_value = 0
        with mock.patch("pypdf.PdfReader", return_value=fake_reader):
            with self.assertRaisesRegex(RuntimeError, "password"):
                pdf.extract_pdf("secret.pdf", dict(config.DEFAULT_CONFIG))

    # --- Subprocess stop cancellation
    def test_run_subprocess_stops_promptly(self):
        stop = threading.Event()
        def trigger():
            time.sleep(0.2)
            stop.set()
        t = threading.Thread(target=trigger)
        t.start()
        t0 = time.monotonic()
        with self.assertRaises(errors.Stopped):
            ocr._run_subprocess([sys.executable, "-c", "import time; time.sleep(10)"], timeout=15, stop=stop)
        t.join()
        self.assertLess(time.monotonic() - t0, 3.0)

    # --- Indicizzazione end-to-end, diagnostica errori, retry, ricerca e move
    def test_index_search_diagnostics_and_move(self):
        f1 = self.tmp / "appunti_citta.txt"
        f1.write_text("Riunione alle 10:30 nella città di Torino (copia).", encoding="utf-8")
        f2 = self.tmp / "fattura.xlsx"
        _make_xlsx(f2)
        bad_pdf = self.tmp / "corrotto.pdf"
        bad_pdf.write_bytes(b"questo non e un pdf valido")

        cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.tmp), "recursive": True}])
        st = indexer.index_all(cfg)
        self.assertEqual(st["added"], 3)
        self.assertEqual(st["errors"], 1)
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["done"], 3)

        con = db.connect()
        try:
            # Ricerca con punteggiatura e accenti
            res = searcher.search(con, "10:30 citta (copia)")
            self.assertEqual([r["name"] for r in res], ["appunti_citta.txt"])

            # Ricerca numero dentro Excel
            res_xl = searcher.search(con, "20250042")
            self.assertEqual([r["name"] for r in res_xl], ["fattura.xlsx"])

            # File corrotto comunque cercabile per nome e visibile in diagnostica
            res_bad = searcher.search(con, "corrotto")
            self.assertEqual(len(res_bad), 1)
            self.assertTrue(res_bad[0]["error"])

            diag = db.diagnostics(con)
            self.assertEqual(diag["total"], 3)
            self.assertEqual(diag["errors"], 1)
            self.assertEqual(diag["error_docs"][0]["name"], "corrotto.pdf")

            # Rinomina file -> riconosciuto come move senza rilettura
            f1_new = self.tmp / "verbale_torino.txt"
            f1.rename(f1_new)
            st2 = indexer.index_all(cfg)
            self.assertEqual(st2["moved"], 1)
            self.assertEqual(st2["added"], 0)
            self.assertEqual(st2["removed"], 0)
        finally:
            con.close()



class _FakeEmbedder:
    """Modello finto, deterministico: ogni parola è un «concetto» e due parole dello stesso gruppo contano
    uguale (come farebbe un modello vero con «caldaia» e «riscaldamento»)."""
    name, dim = models.DEFAULT_MODEL, 256  # stesso nome del modello vero: i controlli di stato lo confrontano
    GROUPS = [{"riscaldamento", "caldaia", "termosifoni"}, {"condominio", "condomini", "condominiale"},
              {"affitto", "locazione", "canone"}]

    def embed(self, texts, kind="passage"):
        import re
        import zlib

        import numpy as np
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for w in re.findall(r"[^\W\d_]+", text.lower()):
                if len(w) < 4:
                    continue
                group = next((i for i, g in enumerate(self.GROUPS) if w in g), None)
                key = f"g{group}" if group is not None else w
                out[row, zlib.crc32(key.encode()) % self.dim] += 1
        norm = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norm == 0, 1, norm)


class SemanticSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cercadoc_sem_"))
        config.CONFIG_PATH.unlink(missing_ok=True)
        con = db.connect()
        db.reset_index(con)
        con.close()
        self.cfg = dict(config.load_config(), folders=[{"path": str(self.tmp), "recursive": False}],
                        semantic_search=True, cpu_throttle=0)
        self.emb = _FakeEmbedder()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index(self, files: dict[str, str]):
        for name, text in files.items():
            (self.tmp / name).write_text(text, encoding="utf-8")
        indexer.index_all(self.cfg)

    def _search(self, q, **kw):
        con = db.connect()
        try:
            return semantic.hybrid_search(con, q, self.emb, min_score=0.3, margin=kw.pop("margin", 1.0), **kw)
        finally:
            con.close()

    def test_passages_overlap_and_stop_at_word_boundaries(self):
        text = " ".join(f"p{i}" for i in range(300))
        spans = semantic.split_passages(text, words=100, overlap=20, max_passages=50)
        self.assertEqual(len(spans), 4)  # 0-99, 80-179, 160-259, 240-299
        self.assertEqual(text[spans[0][0]:spans[0][1]].split()[0:2], ["p0", "p1"])
        self.assertEqual(text[spans[1][0]:spans[1][1]].split()[0], "p80")       # sovrapposizione di 20 parole
        self.assertEqual(text[spans[-1][0]:spans[-1][1]].split()[-1], "p299")   # il testo finisce nell'ultimo
        self.assertTrue(all(text[a:b] == text[a:b].strip() for a, b in spans))
        self.assertEqual(semantic.split_passages("poche parole qui"), [(0, 16)])
        self.assertEqual(semantic.split_passages("   \n  "), [])
        self.assertEqual(len(semantic.split_passages(text, words=100, overlap=20, max_passages=2)), 2)
        self.assertEqual(len(semantic.split_passages(" ".join(["x"] * 100), words=100, overlap=20)), 1)
        self.assertEqual(len(semantic.split_passages(" ".join(["x"] * 101), words=100, overlap=20)), 2)  # una parola in più

    def test_vector_store_discards_vectors_left_by_an_interrupted_write(self):
        import numpy as np
        from cercadoc.vectors import VectorStore
        store = VectorStore(self.tmp / "v", 8)
        store.add([1, 2], np.eye(8)[:2])
        with open(store.vec_path, "ab") as f:      # scritti i vettori, ma l'interruzione è arrivata prima degli id
            f.write(b"\x07" * 16)
        store.add([3], np.eye(8)[2:3])
        self.assertEqual((len(store), store.vec_path.stat().st_size), (3, 24))
        self.assertEqual(store.search(np.eye(8)[2], 1)[0][0], 3)  # il vettore nuovo non è sfalsato

    def test_embedding_marks_documents_and_resumes_after_a_stop(self):
        self._index({"a.txt": "caldaia centralizzata del palazzo", "b.txt": "ricetta della torta di mele",
                     "c.txt": "contratto di locazione"})
        con = db.connect()
        self.assertEqual(semantic.pending_count(con), 3)
        con.close()
        stop = threading.Event()
        seen = []

        def progress(st):
            seen.append(st["done"])
            stop.set()  # si ferma dopo il primo documento
        st = semantic.embed_pending(self.cfg, self.emb, progress, stop)
        self.assertEqual((st["done"], st.get("stopped")), (1, True))
        con = db.connect()
        self.assertEqual(semantic.pending_count(con), 2)
        con.close()
        st = semantic.embed_pending(self.cfg, self.emb)  # riprende dagli altri due
        self.assertEqual((st["done"], st["total"]), (2, 2))
        con = db.connect()
        info = semantic.status(con)
        con.close()
        self.assertEqual((info["pending"], info["passages"]), (0, 3))
        self.assertEqual(semantic.embed_pending(self.cfg, self.emb)["total"], 0)  # niente da rifare

    def test_meaning_finds_documents_without_the_words_but_exact_words_stay_first(self):
        self._index({"caldaia.txt": "Ripartizione quote caldaia centralizzata tra i condomini",
                     "spese.txt": "Le spese di riscaldamento del condominio sono aumentate",
                     "torta.txt": "Ricetta della torta di mele con cannella"})
        semantic.embed_pending(self.cfg, self.emb)
        res = self._search("spese riscaldamento condominio")
        self.assertEqual([r["name"] for r in res], ["spese.txt", "caldaia.txt"])  # parole esatte prima, poi significato
        self.assertNotIn("semantic", res[0])
        self.assertTrue(res[1]["semantic"])
        self.assertIn("caldaia", res[1]["snippet"])            # si mostra il passaggio trovato
        self.assertNotIn("torta.txt", [r["name"] for r in res])  # sotto la soglia: non compare
        con = db.connect()
        self.assertEqual([r["name"] for r in searcher.search(con, "spese riscaldamento condominio")], ["spese.txt"])
        con.close()

    def test_only_documents_close_to_the_best_one_are_kept(self):
        self._index({"spese.txt": "spese riscaldamento condominio", "caldaia.txt": "caldaia centralizzata del palazzo"})
        semantic.embed_pending(self.cfg, self.emb)
        con = db.connect()
        both = semantic.semantic_docs(con, semantic.open_store(self.emb.dim), self.emb, "spese riscaldamento condominio",
                                      min_score=0.1, margin=1.0)
        only_best = semantic.semantic_docs(con, semantic.open_store(self.emb.dim), self.emb,
                                           "spese riscaldamento condominio", min_score=0.1, margin=0.04)
        con.close()
        self.assertEqual(len(both), 2)
        self.assertEqual(len(only_best), 1)
        self.assertGreater(both[0]["score"] - both[1]["score"], 0.04)

    def test_meaning_respects_filters_and_leaves_other_searches_untouched(self):
        self._index({"caldaia.txt": "Ripartizione quote caldaia centralizzata", "caldaia.md": "Manutenzione della caldaia"})
        semantic.embed_pending(self.cfg, self.emb)
        self.assertEqual([r["name"] for r in self._search("riscaldamento ext:md")], ["caldaia.md"])
        self.assertEqual({r["name"] for r in self._search("riscaldamento")}, {"caldaia.txt", "caldaia.md"})
        self.assertEqual([r["name"] for r in self._search("riscaldamento", ext=["txt"])], ["caldaia.txt"])
        self.assertEqual(self._search("riscaldamento", order="date"), [])  # per data/nome: solo parole
        self.assertEqual(self._search('"riscaldamento"'), [])  # sintassi FTS5: solo parole
        self.assertEqual(self._search("riscaldamento AND caldaia"), [r for r in self._search("riscaldamento AND caldaia")
                                                                     if not r.get("semantic")])

    def test_meaning_is_skipped_when_searching_only_in_names(self):
        self._index({"caldaia.txt": "Ripartizione quote caldaia centralizzata"})
        semantic.embed_pending(self.cfg, self.emb)
        self.assertEqual([r["name"] for r in self._search("riscaldamento")], ["caldaia.txt"])
        self.assertEqual(self._search("riscaldamento", where="name"), [])           # i vettori sono dei contenuti
        self.assertEqual([r["name"] for r in self._search("riscaldamento", where="content")], ["caldaia.txt"])
        self.assertEqual(self._search("riscaldamento", before=1.0), [])             # periodo senza documenti

    def test_vectors_follow_changed_and_deleted_documents(self):
        self._index({"a.txt": "caldaia del palazzo", "b.txt": "torta di mele"})
        semantic.embed_pending(self.cfg, self.emb)
        self.assertEqual([r["name"] for r in self._search("riscaldamento")], ["a.txt"])
        (self.tmp / "a.txt").write_text("tutt'altro argomento: ricetta del ragù bolognese", encoding="utf-8")
        time.sleep(0.01)
        os.utime(self.tmp / "a.txt", (time.time() + 5, time.time() + 5))
        indexer.index_all(self.cfg)
        self.assertEqual(self._search("riscaldamento"), [])      # il vecchio vettore non risponde più, anche prima del ricalcolo
        con = db.connect()
        self.assertEqual(semantic.pending_count(con), 1)
        con.close()
        semantic.embed_pending(self.cfg, self.emb)
        (self.tmp / "b.txt").unlink()
        indexer.index_all(self.cfg)
        st = semantic.embed_pending(self.cfg, self.emb)
        self.assertEqual(st["removed"], 1)                       # i passaggi di b.txt tolti con il documento
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0], 1)
        con.close()
        self.assertEqual(semantic.open_store(self.emb.dim).count(), 1)

    def test_an_interrupted_calculation_is_cleaned_up(self):
        self._index({"a.txt": "caldaia del palazzo"})
        con = db.connect()
        # righe dei passaggi senza `vec_done`, come dopo un'interruzione, e un vettore già scritto
        cid = con.execute("INSERT INTO vec_chunks(doc_id,start,end) VALUES(1,0,5)").lastrowid
        con.commit()
        store = semantic.open_store(self.emb.dim)
        store.add([cid], self.emb.embed(["caldaia"]))
        self.assertEqual(semantic.reconcile(con, store, self.emb.name), 1)
        self.assertEqual((store.count(), con.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]), (0, 0))
        con.close()

    def test_reset_removes_vector_files(self):
        self._index({"a.txt": "caldaia del palazzo"})
        semantic.embed_pending(self.cfg, self.emb)
        store = semantic.open_store(self.emb.dim)
        self.assertTrue(store.vec_path.exists())
        con = db.connect()
        db.reset_index(con)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0], 0)
        con.close()
        self.assertFalse(store.vec_path.exists() or store.ids_path.exists())

    def test_agent_computes_missing_vectors_and_backs_off_when_nothing_advances(self):
        self._index({"a.txt": "caldaia del palazzo"})
        config.save_config(self.cfg)
        started = []

        class Worker:
            def __init__(self, advance):
                self.advance = advance

            def wait(self):
                if self.advance:
                    semantic.embed_pending(config.load_config(), _FakeEmbedder())

        mode = [False]
        ag = agent.Agent(spawn=lambda: started.append(1) or Worker(mode[0]), log=lambda *_: None)
        with mock.patch.object(semantic, "available", return_value=True):
            self.assertTrue(ag.tick())     # niente file da leggere, ma vettori mancanti: parte il lavoratore
            self.assertTrue(ag.semantic_retry_at > time.monotonic())  # non è avanzato: pausa
            self.assertFalse(ag.tick())    # in pausa non riparte a ogni controllo
            ag.semantic_retry_at = 0.0
            mode[0] = True
            self.assertTrue(ag.tick())
            self.assertEqual(ag.semantic_retry_at, 0.0)
            self.assertFalse(ag.tick())    # vettori calcolati: nulla da fare
        self.assertEqual(len(started), 2)
        with mock.patch.object(semantic, "available", return_value=False):
            (self.tmp / "b.txt").write_text("nuovo documento", encoding="utf-8")
            self.assertTrue(ag.tick())     # un file nuovo fa partire comunque il lavoratore, senza calcolare vettori

    def test_semantic_is_off_by_default_and_unavailable_without_the_model(self):
        self.assertFalse(config.DEFAULT_CONFIG["semantic_search"])
        self.assertFalse(semantic.available(dict(self.cfg), models_dir=self.tmp))  # nessun modello in questa cartella
        self.assertFalse(semantic.available(dict(self.cfg, semantic_search=False)))

    def test_tokenizer_with_a_different_fingerprint_is_rejected(self):
        (self.tmp / "sentencepiece.bpe.model").write_bytes(b"non e' il tokenizer atteso")
        with self.assertRaises(modelcheck.ModelRejected):
            models.load_tokenizer_checked(models.DEFAULT_MODEL, self.tmp)
        with self.assertRaises(modelcheck.ModelRejected):
            models.load_tokenizer_checked(models.DEFAULT_MODEL, self.tmp / "inesistente")

    @unittest.skipUnless(models.is_installed() and semantic.libraries_available(),
                         "modello e librerie per la ricerca per significato non installati")
    def test_real_model_matches_the_reference_tokenizer_and_ranks_by_meaning(self):
        from cercadoc.embedder import Embedder
        emb = Embedder()
        # numeri di riferimento calcolati con il tokenizer ufficiale (tokenizer.json di Xenova/multilingual-e5-small)
        self.assertEqual(emb.tokens("query: spese riscaldamento condominio"),
                         [0, 41, 1294, 12, 104995, 201717, 167557, 31, 2])
        self.assertEqual(len(emb.tokens("parola " * 1000)), 256)
        docs = ["Ripartizione delle quote della caldaia centralizzata tra i condomini per la stagione invernale.",
                "Contratto di locazione: il canone mensile è di euro 850, da versare entro il giorno cinque.",
                "Ricetta della torta di mele con cannella: sbucciare le mele e mescolare farina, uova e zucchero."]
        d = emb.embed(docs)
        q = emb.embed(["spese riscaldamento condominio", "quanto pago di affitto"], "query")
        self.assertEqual(list((d @ q[0]).argmax(axis=0) for _ in [0]), [0])
        self.assertEqual(int((d @ q[1]).argmax()), 1)
        self.assertGreater(float((d @ q[0]).max()), semantic.MIN_SCORE)
        self.assertLess(float((d @ emb.embed(["xyzzy qwerty"], "query")[0]).max()), semantic.MIN_SCORE)
        self.assertAlmostEqual(float((d * d).sum(axis=1)[0]), 1.0, places=4)  # vettori normalizzati


class DocumentRelationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.root)}], cpu_throttle=0)
        self.con = db.connect()
        self.addCleanup(self.con.close)
        db.reset_index(self.con)

    def put(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def names(self, query, **kwargs):
        return [r["name"] for r in searcher.search(self.con, query, **kwargs)]

    def test_valid_identifiers_mentions_filter_and_disconnected_folder_privacy(self):
        from cercadoc import relations
        text = ("Contabilità PR-00012/2025, pratica FASC-002/2024.\n"
                "RSSMRA80A01H501U; P. IVA IT12345678903; IBAN IT60 X054 2811 1010 0000 0123 456."
                "\nScrivere a Ufficio@Example.it.\n\nCodici errati RSSMRA80A01H501A, 12345678904, "
                "IT61X0542811101000000123456.")
        self.put("scheda.txt", text)
        self.put("altra.txt", "PR-00012/2025")
        self.cfg["protocol_prefixes"] = ("PR", "FASC")
        indexer.index_all(self.cfg)
        for q in ("cf:rssmra80a01h501u", "piva:IT12345678903", "protocollo:FASC-002/2024",
                  'iban:"IT60 X054 2811 1010 0000 0123 456"', "email:ufficio@example.it", "contabilità cf:RSSMRA80A01H501U"):
            self.assertEqual(self.names(q), ["scheda.txt"], q)
        for q in ("cf:RSSMRA80A01H501A", "piva:12345678904", "iban:IT61X0542811101000000123456"):
            self.assertEqual(self.names(q), [])
        row = searcher.search(self.con, "piva:12345678903")[0]
        self.assertIn("12345678903", text[row["offset"]:row["end"]])
        self.assertEqual([r[1] for r in relations.related_documents(self.con, row["id"])], ["altra.txt"])
        moved = self.root / "disco"
        moved.mkdir()
        original = self.root / "archivio"
        original.mkdir()
        (self.root / "scheda.txt").rename(original / "scheda.txt")
        self.cfg["folders"] = [{"path": str(original)}]
        indexer.index_all(self.cfg)
        original.rename(moved / "scollegato")
        self.cfg["entity_types"] = ["cf"]
        indexer.index_all(self.cfg)
        self.assertEqual(self.names("piva:12345678903"), [])
        self.assertEqual(self.con.execute("SELECT DISTINCT tipo FROM entita").fetchall(), [("cf",)])
        self.cfg["entity_types"] = ["piva"]
        indexer.index_all(self.cfg)
        self.assertEqual(self.names("piva:12345678903"), ["scheda.txt"])

    def test_amount_ranges_cents_filters_and_currencies(self):
        self.put("conto.txt", "Anticipo € 1.250,00.\nSaldo 472,50 EUR.")
        self.put("spese.md", "Costo apparato 1.400. Saldo: € 427,50.")
        self.put("dollari.txt", "Doc del 12.04.2025, offerta 1,350.75 USD.")
        self.put("altro.txt", "€ 472,51. Un anno 2025; aliquota 20%.")
        indexer.index_all(self.cfg)
        self.assertEqual(set(self.names("1200..1500")), {"conto.txt", "spese.md"})
        self.assertEqual(self.names("importo:1200..1500 ext:txt"), ["conto.txt"])
        self.assertEqual(self.names("saldo importo:~450 ext:md"), ["spese.md"])
        self.assertEqual(set(self.names("~450")), {"conto.txt", "spese.md"})
        self.assertEqual(self.names("importo:2025"), [])
        self.assertIn((135075, "USD"), list(self.con.execute("SELECT centesimi,valuta FROM importi")))
        (self.root / "conto.txt").unlink()
        self.put("spese.md", "Saldo aggiornato a € 230.")
        indexer.index_all(self.cfg)
        self.assertEqual(self.names("~450"), [])

    def test_signed_invoice_parties_dates_payments_and_contents(self):
        from cercadoc import relations
        xml = """<FatturaElettronica><FatturaElettronicaHeader><CedentePrestatore>
        <DatiAnagrafici><Anagrafica><Denominazione>Società Alfa</Denominazione></Anagrafica>
        <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>12345678903</IdCodice></IdFiscaleIVA></DatiAnagrafici>
        </CedentePrestatore></FatturaElettronicaHeader><FatturaElettronicaBody><DatiGenerali><DatiGeneraliDocumento>
        <Data>2025-07-05</Data><Divisa>EUR</Divisa><ImportoTotaleDocumento>1250.00</ImportoTotaleDocumento>
        </DatiGeneraliDocumento></DatiGenerali><DatiPagamento><DettaglioPagamento><ImportoPagamento>450.00000</ImportoPagamento>
        <DataScadenzaPagamento>2025-08-05</DataScadenzaPagamento></DettaglioPagamento></DatiPagamento>
        </FatturaElettronicaBody></FatturaElettronica>"""
        (self.root / "firma.xml.p7m").write_bytes(_make_p7m(xml.encode()))
        indexer.index_all(self.cfg)
        self.put("originale.xml", xml)
        indexer.index_all(self.cfg)
        self.assertEqual(set(self.names("piva:12345678903 ~450")), {"originale.xml", "firma.xml.p7m"})
        rel = self.con.execute("SELECT s.name,o.name FROM collegamenti c JOIN docs s ON s.id=c.da_doc "
                               "JOIN docs o ON o.id=c.a_doc WHERE c.tipo='contiene'").fetchall()
        self.assertEqual(rel, [("firma.xml.p7m", "originale.xml")])
        self.assertEqual([r[0] for r in relations.deadlines(self.con)], ["2025-08-05"] * 2)
        self.assertEqual(self.con.execute("SELECT DISTINCT data_iso,fonte FROM analisi").fetchall(), [("2025-07-05", "FatturaPA")])
        self.assertIn(("soggetto", "cedente"), list(self.con.execute("SELECT e.tipo,m.ruolo FROM entita e JOIN menzioni m ON m.entita_id=e.id")))

    def test_conversation_arrives_in_reverse_and_timeline_uses_internal_dates(self):
        from cercadoc import relations
        self.put("risposta.eml", "Date: Tue, 4 Mar 2025 10:00:00 +0100\nMessage-ID: <b@example.it>\n"
                 "In-Reply-To: <a@example.it>\nReferences: <a@example.it>\n\nPR-123/2025. Risposta.")
        indexer.index_all(self.cfg)
        self.put("domanda.eml", "Date: Mon, 3 Mar 2025 12:00:00 +0100\nMessage-ID: <a@example.it>\n"
                 "From: Bot <pec@example.it>\n\nPR-123/2025. Domanda.")
        indexer.index_all(self.cfg)
        self.assertEqual(self.con.execute("SELECT count(*) FROM collegamenti WHERE tipo='risposta'").fetchone()[0], 1)
        self.assertEqual(self.names("email:a@example.it"), [])
        eid = self.con.execute("SELECT id FROM entita WHERE tipo='protocollo'").fetchone()[0]
        rows = relations.entity_documents(self.con, eid)
        self.assertEqual([(r[1],r[2],r[3]) for r in rows], [
            ("domanda.eml", "2025-03-03", "email"), ("risposta.eml", "2025-03-04", "email")])
        self.assertEqual(self.names("protocollo:PR-123/2025", order="document_date"), ["risposta.eml", "domanda.eml"])
        for doc, _, _, _, a, b in rows:
            self.assertEqual(preview.read_content(doc)[a:b], "PR-123/2025")
        self.cfg["entity_types"] = ["email", "protocollo"]
        indexer.index_all(self.cfg)
        self.assertEqual(self.con.execute("SELECT count(*) FROM collegamenti WHERE tipo='risposta'").fetchone()[0], 0)

    def test_deadlines_context_dates_and_optional_summary(self):
        from datetime import date
        from cercadoc import relations
        self.put("atto.txt", "Roma, 2 novembre 2026\nPremessa del 3 marzo 2024.\n\nScade il 24/11/2026. "
                 "Disdetta entro il 24 dicembre 2026. Data di nascita 01/01/1990. Scade il 31/02/2027.")
        self.put("avviso.txt", "Validità fino al 25.12.2026.\n\nSenza data di emissione.")
        indexer.index_all(self.cfg)
        self.assertEqual([r[0] for r in relations.deadlines(self.con)], ["2026-11-24", "2026-12-24", "2026-12-25"])
        self.assertEqual(relations.upcoming_count(self.con, date(2026, 11, 24)), 1)
        self.assertEqual(set(self.con.execute("SELECT data_iso FROM analisi")), {(None,), ("2026-11-02",)})
        for iso, doc, name, context, a, b in relations.deadlines(self.con):
            self.assertIn(preview.read_content(doc)[a:b], context)
        (self.root / "atto.txt").unlink()
        indexer.index_all(self.cfg)
        self.assertEqual(relations.upcoming_count(self.con, date(2026, 11, 24)), 0)

    def test_cross_format_versions_and_diff_ignore_line_wraps(self):
        from cercadoc import relations, versions
        common = "Il conduttore si impegna a versare il canone mensile entro il quinto giorno di ogni mese."
        old = common + " Il deposito dovuto alla stipula ammonta a 1250 euro."
        new = common + " Il deposito dovuto alla stipula ammonta a 1450 euro."
        with zipfile.ZipFile(self.root / "bozza.docx", "w") as out:
            out.writestr("word/document.xml", "<d><p><t>" + old + "</t></p></d>")
        (self.root / "scansione.pdf.p7m").write_bytes(_make_p7m(b"%PDF-scan"))
        (self.root / "scansione.pdf").write_bytes(b"%PDF-scan")
        self.put("estraneo.txt", "Le mele si raccolgono in autunno e si conservano per tutto l'inverno.")
        self.cfg["ocr"] = True
        with mock.patch("pypdf.PdfReader", return_value=CercaDocTests._fake_pdf_reader([""])), \
             mock.patch.object(ocr, "ocr_available", return_value=True), \
             mock.patch.object(ocr, "ocr_pdf_pages", return_value={1:new.replace("canone mensile","canone\nmensile")}):
            indexer.index_all(self.cfg)
        doc = self.con.execute("SELECT id FROM docs WHERE ext='docx'").fetchone()[0]
        self.assertEqual({r[1] for r in relations.document_versions(self.con, doc)}, {"scansione.pdf", "scansione.pdf.p7m"})
        hunks = list(versions.differences(old, new.replace("canone mensile","canone\nmensile")))
        self.assertEqual(len(hunks), 1)
        (a, spans_a), (b, spans_b) = hunks[0]
        self.assertEqual([a[x:y] for x,y in spans_a], ["1250"])
        self.assertEqual([b[x:y] for x,y in spans_b], ["1450"])
        self.assertEqual(list(versions.differences(old, old.replace("canone mensile", "canone\nmensile"))), [])

    def test_office_creation_date_and_precedence_of_dated_text(self):
        for name, body in (("appunto", "Riferimento PR-321/2025"), ("contratto", "Data: 12 febbraio 2024")):
            with zipfile.ZipFile(self.root / (name + ".docx"), "w") as out:
                out.writestr("word/document.xml", "<d><p><t>" + body + "</t></p></d>")
                out.writestr("docProps/core.xml", "<c><created>2025-01-01T18:00:00Z</created></c>")
        indexer.index_all(self.cfg)
        rows = searcher.list_docs(self.con, order="document_date")
        self.assertEqual([(r["name"], r["document_date"], r["date_source"]) for r in rows], [
            ("appunto.docx", "2025-01-01", "metadati"), ("contratto.docx", "2024-02-12", "testo")])

    def test_semantic_anchor_beyond_full_preview_limit_and_similar_documents(self):
        self.put("uno.txt", "Contratto per la manutenzione della caldaia centralizzata")
        self.put("due.txt", "Quote per la caldaia")
        self.put("altro.txt", "Ricetta della torta di mele")
        indexer.index_all(self.cfg)
        from cercadoc import semantic
        emb = _FakeEmbedder()
        semantic.embed_pending(self.cfg, emb)
        store = semantic.open_store(emb.dim)
        source = searcher.search(self.con, "name:uno")[0]["id"]
        hits = semantic.similar_documents(self.con, source, store=store, model=emb.name, min_score=.2)
        self.assertEqual([r["name"] for r in hits], ["due.txt"])
        r = hits[0]
        self.assertEqual(preview.read_content(r["id"])[r["offset"]:r["end"]], "Quote per la caldaia")
        prefix = "caffè\r\n" * 65000
        for full in (True, False):
            parts = preview.anchored_parts(prefix + "Quota 450 euro", len(prefix), len(prefix)+5, full=full)
            self.assertEqual([t for t,tag in parts if tag=="hit"], ["Quota"])
            self.assertLessEqual(sum(len(t) for t,tag in parts), preview.FULL_TEXT_LIMIT + 8)

    def test_snapshot_survives_replacement_of_original_model(self):
        from hashlib import sha256
        model = self.root / "model.onnx"
        data = _onnx_model([_onnx_node("Add")])
        model.write_bytes(data)
        with modelcheck.verified_runtime_source(model, sha256(data).hexdigest()) as source:
            model.write_bytes(b"modello diverso")
            self.assertEqual(source if isinstance(source,bytes) else Path(source).read_bytes(), data)
        with self.assertRaises(modelcheck.ModelRejected), modelcheck.verified_runtime_source(model, sha256(data).hexdigest()):
            self.fail("impronta diversa accettata")

    def test_v7_migration_preserves_disconnected_text_ids_and_ocr_cache(self):
        import sqlite3
        archive = self.root / "vecchio.db"
        old = sqlite3.connect(archive)
        old.executescript("""
            CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE,name TEXT,folder TEXT,ext TEXT,
                size INTEGER,mtime REAL,notext INTEGER,ocr INTEGER,blank_pages INTEGER,dev INTEGER,ino INTEGER,fp TEXT,error TEXT);
            CREATE VIRTUAL TABLE fts USING fts5(name,content,stem);
            CREATE TABLE ocr_cache(key TEXT,page INTEGER,text TEXT,PRIMARY KEY(key,page));
            INSERT INTO ocr_cache VALUES('abc',2,'pagina in corso');
            INSERT INTO docs VALUES(56,'/disco/atto.txt','atto.txt','/disco','txt',123,1,0,1,0,0,0,'a','');
            INSERT INTO fts(rowid,name,content,stem) VALUES(56,'atto.txt','Saldo 1250 EUR. PR-123/2025','');
            PRAGMA user_version=7;
        """)
        old.close()
        with mock.patch.object(db, "DB_PATH", archive):
            indexer.index_all(dict(self.cfg, folders=[{"path": "/disco"}]))
            with db.connect() as migrated:
                self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
                self.assertEqual(migrated.execute("SELECT text FROM ocr_cache").fetchone()[0], "pagina in corso")
                self.assertEqual(searcher.search(migrated, "importo:1250")[0]["id"], 56)
                self.assertEqual(searcher.search(migrated, "atti", where="content"), [])
                self.assertEqual(migrated.execute("PRAGMA foreign_key_check").fetchall(), [])
            migrated.close()

    def test_unpublished_compaction_does_not_change_reader_results(self):
        import numpy as np
        from cercadoc.vectors import VectorStore
        store = VectorStore(self.root / "atomic", 3)
        store.add([10, 20, 30], np.eye(3))
        store.remove([20])
        with mock.patch("cercadoc.vectors.os.replace", side_effect=OSError("interruzione")), self.assertRaises(OSError):
            store.compact()
        self.assertEqual(len(store), 3)
        self.assertEqual(store.search([0, 0, 1], 1)[0][0], 30)
        store.clean_retired()
        self.assertEqual(store.compact(), 1)
        self.assertEqual((len(store), store.search([0, 0, 1], 1)[0][0]), (2, 30))

    def test_filtered_semantic_ranking_starts_inside_the_selected_documents(self):
        import numpy as np
        self.put("altro.txt", "primo testo")
        self.put("utile.md", "testo selezionabile")
        indexer.index_all(self.cfg)
        class Model:
            name, dim = "regressione", 2
            def embed(self, texts, kind="passage"):
                return np.array([[1., 0.] if t in ("domanda", "primo testo") else [.85, .5268] for t in texts])
        model = Model()
        semantic.embed_pending(self.cfg, model)
        rows = semantic.hybrid_search(self.con, "domanda ext:md", model, min_score=.80, margin=.015)
        self.assertEqual([r["name"] for r in rows], ["utile.md"])

    @unittest.skipUnless(sys.platform == "win32", "prova nativa della compattazione su Windows")
    def test_windows_open_reader_survives_compaction_and_reclaims_files_after_close(self):
        import numpy as np
        from cercadoc.vectors import VectorStore
        store = VectorStore(self.root / "blocchi", 3)
        store.add([1,2,3], np.eye(3))
        store.remove([2])
        old_vec, old_ids = store.vec_path, store.ids_path
        vec, ids = store._maps()
        self.assertEqual(store.compact(), 1)
        self.assertEqual(list(ids), [1,-1,3])
        self.assertEqual(int(vec[2,2]), 127)
        self.assertEqual(store.search([0,0,1],1)[0][0], 3)
        del ids, vec
        store.clean_retired()
        self.assertFalse(old_vec.exists() or old_ids.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
