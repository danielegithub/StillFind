# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressioni su indici e documenti sintetici, senza usare l'archivio dell'account."""
import base64
from contextlib import closing, ExitStack
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import tempfile
import unittest
from unittest import mock
import zipfile

from cercadoc import agent, config, db, indexer, library, protection, redaction, search, semantic
from cercadoc.extractors import extract, ocr


def zip_bytes(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


def pdf_fixture(dest, scanned=True):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(500, 140)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(
        {NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 25 Tf 30 70 Td (SECRETWORD Visible) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    if scanned:
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (1050, 240), "white")
        # Il font integrato in Pillow funziona anche su una macchina senza font aggiuntivi.
        ImageDraw.Draw(image).text((40, 65), "CUSTODE Visible alice@example.it", fill="black",
                                   font=ImageFont.load_default(size=52))
        data = io.BytesIO()
        image.save(data, "PDF", resolution=144)
        data.seek(0)
        writer.append(data)
    writer.add_metadata({"/Author": "segreto-nei-metadati"})
    writer.add_attachment("allegato-privato.txt", b"allegato-nascosto")
    writer.write(str(dest))


class PrivateIndex(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.docs = root / "files"
        self.docs.mkdir()
        self.home = root / "index"
        self.home.mkdir()
        stack.enter_context(mock.patch.dict(os.environ, CERCADOC_HOME=str(self.home)))
        self.path = self.home / "index.db"
        for module in (db, config, agent, semantic):
            for name, value in (("DB_PATH", self.path), ("APP_DIR", self.home), ("CONFIG_PATH", self.home/"config.json")):
                if hasattr(module, name):
                    stack.enter_context(mock.patch.object(module, name, value))
        self.cfg = dict(config.DEFAULT_CONFIG, folders=[{"path": str(self.docs), "recursive": True}], cpu_throttle=0,
                        ocr_lang="eng", pause_on_battery=False)
        protection.forget()
        self.addCleanup(protection.forget)

    def test_history_order_retention_and_named_criteria(self):
        with closing(db.connect()) as con:
            saved = dict(query="fattura", folder="/archivio", custom=["2025-01-01", "2026-01-01"])
            library.save_search(con, saved, "Fatture 2025")
            for i in range(63):
                library.save_search(con, {"query": str(i)})
            library.save_search(con, {"query": "61"})
            history = library.searches(con, False)
            self.assertEqual(len(history), 60)
            self.assertEqual(history[0][2], {"query": "61"})
            self.assertEqual(library.searches(con, True)[0][2], saved)
            library.save_search(con, {"query": "contratti"}, "Fatture 2025")
            self.assertEqual(len(library.searches(con, True)), 1)

    def test_zip_budget_paths_nested_and_epub_spine(self):
        target = self.docs / "pratiche.zip"
        target.write_bytes(zip_bytes({"atti/verbale.txt": "atto riservato", "../fuori.txt": "ESCAPE",
                                      "inner.zip": zip_bytes({"uno.txt": "NESTED"})}))
        body, _ = extract(target, "zip", self.cfg)
        self.assertIn("atto riservato", body)
        self.assertNotIn("ESCAPE", body)
        self.assertNotIn("NESTED", body)
        target.write_bytes(zip_bytes({"uno.txt": "z"*700000, "due.txt": "q"*700000}))
        with self.assertRaisesRegex(ValueError, "limite"):
            extract(target, "zip", dict(self.cfg, max_file_mb=1))
        target = self.docs / "libro.epub"
        target.write_bytes(zip_bytes({"META-INF/container.xml": '<container><rootfile full-path="o/b.opf"/></container>',
                      "o/b.opf": '<package><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                      '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest>'
                      '<spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
                      "o/a.xhtml": "<p>ULTIMO</p>", "o/b.xhtml": "<p>PRIMO</p>"}))
        text, _ = extract(target, "epub", self.cfg)
        self.assertLess(text.index("PRIMO"), text.index("ULTIMO"))

    def test_target_and_audit_xlsx_attachment_and_exposure(self):
        email = "alice@example.it"
        folder = self.docs / "Download_100%"
        folder.mkdir()
        sibling = self.docs / "DownloadX100A"
        sibling.mkdir()
        xlsx = zip_bytes({"xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/'
              'spreadsheetml/2006/main"><sheetData><row><c t="inlineStr"><is><t>'+email+
              '</t></is></c></row></sheetData></worksheet>'})
        (sibling/"elenco.xlsx").write_bytes(xlsx)
        mail = folder/"pratica.eml"
        mail.write_bytes(b'Subject: Prova\nContent-Type: multipart/mixed; boundary="X"\n\n--X\n'
                         b'Content-Type: text/plain\n\nverbale\n--X\nContent-Type: application/octet-stream\n'
                         b'Content-Disposition: attachment; filename="indirizzi.xlsx"\n'
                         b'Content-Transfer-Encoding: base64\n\n' + base64.b64encode(xlsx) + b'\n--X--\n')
        other = self.docs/"altro.txt"
        other.write_text("anteprima originale")
        self.assertEqual(indexer.index_all(self.cfg)["errors"], 0)
        other.unlink()
        with mock.patch.object(os, "walk", side_effect=AssertionError("scansione globale")):
            result = indexer.index_all(self.cfg, target=str(mail))
        self.assertEqual((result["updated"], result["removed"]), (1, 0))
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM docs").fetchone()[0], 3)
            all_hits = library.audit(con, email.upper())
            self.assertEqual({r[1] for r in all_hits}, {"pratica.eml", "elenco.xlsx"})
            self.assertEqual([r[1] for r in library.audit(con, email, [folder])], ["pratica.eml"])
            content = con.execute("SELECT content FROM fts WHERE rowid=?", (all_hits[0][0],)).fetchone()[0]
            self.assertEqual(content[all_hits[0][5]:][:len(email)], email)
            self.assertEqual(library.audit(con, "anteprima originale")[0][3], "testo")

    def test_target_failure_preserves_previous_text(self):
        path = self.docs / "prova.txt"
        path.write_text("preesistente")
        indexer.index_all(self.cfg)
        with mock.patch.object(indexer, "extract", side_effect=OSError("lettura fallita")):
            with self.assertRaisesRegex(RuntimeError, "lettura fallita"):
                indexer.index_all(self.cfg, target=str(path))
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT content FROM fts").fetchone(), ("preesistente",))
            self.assertEqual(con.execute("SELECT error FROM docs").fetchone(), ("lettura fallita",))

    @unittest.skipUnless(importlib.util.find_spec("sqlcipher3") and semantic.libraries_available()
                         and semantic.models.is_installed(), "Modello locale e SQLCipher richiesti")
    def test_vault_background_embedding_uses_only_encrypted_vectors(self):
        cfg = dict(self.cfg, semantic_search=True)
        config.save_config(cfg)
        (self.docs/"pratica.txt").write_text("Il contratto di affitto prevede il pagamento mensile del canone.")
        indexer.index_all(cfg)
        protection.migrate(self.path, "vault", "passphrase di collaudo")
        proc = agent.spawn_worker(sandbox=True, mode="--embed")
        try:
            self.assertNotIn("passphrase di collaudo", str(proc.args))
            self.assertEqual(proc.wait(timeout=60), 0)
        finally:
            proc.stdin.close()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        with closing(db.connect()) as con:
            self.assertEqual(semantic.pending_count(con), 0)
            self.assertGreater(semantic.open_store(con=con).count(), 0)
            self.assertFalse(db.vector_files(self.path))

    def test_idle_lock_before_open_signal(self):
        from types import SimpleNamespace
        from cercadoc import gui
        session = SimpleNamespace(protection={"mode":"vault","minutes":2}, _last_input=time.monotonic()-121)
        with mock.patch.object(gui.security_ui, "restart") as reset:
            gui.App._session_tick(session)
        reset.assert_called_once_with(session)

    @unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher richiesto")
    def test_vault_round_trip_vectors_wal_history_and_lock(self):
        import numpy as np
        from cercadoc.vectors import VectorStore, DatabaseVectors
        token = "segretoindipendente"
        (self.docs/"atto.txt").write_text(token)
        indexer.index_all(self.cfg)
        vec = np.arange(384, dtype=np.float32)
        VectorStore(self.path, 384).add([91], [vec])
        with closing(db.connect()) as con:
            library.save_search(con, {"query": token}, "Pratiche")
            with self.assertRaisesRegex(RuntimeError, "occupato"):
                protection.migrate(self.path, "vault", "frase di prova", 3)
        protection.migrate(self.path, "vault", "frase di prova", 3)
        self.assertEqual(protection.settings(self.path)["minutes"], 3)
        protection.forget()
        with self.assertRaises(protection.Locked):
            protection.connection(self.path)
        with self.assertRaises(protection.Locked):
            protection.unlock(self.path, "frase diversa")
        protection.unlock(self.path, "frase di prova")
        self.assertFalse(agent.Agent(log=lambda _: None).tick())
        with closing(db.connect()) as con:
            self.assertEqual(len(search.search(con, token)), 1)
            self.assertEqual(library.searches(con, True)[0][1], "Pratiche")
            store = semantic.open_store(con=con)
            self.assertIsInstance(store, DatabaseVectors)
            self.assertEqual(store.search(vec)[0][0], 91)
            store.add([92], [-vec])
            library.save_search(con, {"query": token+"wal"})
            self.assertFalse(db.vector_files(self.path))
            for item in self.home.iterdir():
                self.assertNotIn(token.encode(), item.read_bytes())
                self.assertNotIn(b"frase di prova", item.read_bytes())
        with closing(sqlite3.connect(self.path)) as con, self.assertRaises(sqlite3.DatabaseError):
            con.execute("SELECT name FROM sqlite_master").fetchall()
        protection.migrate(self.path, "plain")
        with closing(db.connect()) as con:
            self.assertEqual(len(search.search(con, token)), 1)
            self.assertEqual(semantic.open_store(con=con).count(), 2)
        self.assertFalse(list(self.home.glob("*.key-*")))

    @unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher richiesto")
    def test_auto_failure_keeps_source_and_accounts_are_cleaned(self):
        secrets = {}
        def keyring(action, account, directory, key=None):
            if action == "put":
                secrets[account] = bytes(key)
            elif action == "get":
                return bytearray(secrets[account])
            else:
                secrets.pop(account, None)
        with closing(db.connect()) as con:
            library.save_search(con, {"query":"persistente"}, "Contratti")
        with mock.patch.object(protection, "credential", side_effect=keyring):
            with mock.patch.object(protection.os, "replace", side_effect=OSError("disco occupato")):
                with self.assertRaises(OSError):
                    protection.migrate(self.path, "auto")
            self.assertEqual(secrets, {})
            self.assertEqual(protection.settings(self.path)["mode"], "plain")
            protection.migrate(self.path, "auto")
            protection.forget()
            with closing(db.connect()) as con:
                self.assertEqual(library.searches(con, True)[0][1], "Contratti")
            self.assertEqual(len(secrets), 1)
            protection.migrate(self.path, "plain")
            self.assertEqual(secrets, {})

    def test_plaintext_redaction_never_overwrites(self):
        source, dest = self.docs/"originale.txt", self.docs/"copia.txt"
        source.write_text("IBAN IT60X0542811101000000123456\nMario Rossi e mario rossi")
        original = source.read_bytes()
        self.assertEqual(redaction.redact_text(source, dest, ["iban"], ["Mario Rossi"]), 3)
        self.assertEqual(dest.read_text(), "IBAN [OMISSIS]\n[OMISSIS] e [OMISSIS]")
        with self.assertRaises(FileExistsError):
            redaction.redact_text(source, source, ["iban"], [])
        self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "PDFium richiesto")
    def test_rotated_pdf_native_text_and_manual_box(self):
        import pypdfium2 as pdf
        from pypdf import PdfWriter
        native = self.docs / "originale.pdf"
        rotated = self.docs / "ruotato.pdf"
        dest = self.docs / "oscurato.pdf"
        pdf_fixture(native, scanned=False)
        writer = PdfWriter(clone_from=native)
        writer.pages[0].rotate(90)
        writer.write(rotated)
        with closing(redaction.PdfReview(rotated)) as review:
            image, size, count = review.prepare([], ["SECRETWORD"], None)
            self.assertEqual(count, 1)
            self.assertGreater(image.height, image.width)
            # Il centro del termine sul foglio originale viene ruotato con il foglio.
            self.assertEqual(image.getpixel((235, 250)), (0, 0, 0))
            review.approve(image, size, [(10, 10, 30, 30)], count)
            review.save(dest)
        with closing(pdf.PdfDocument(dest)) as result, closing(result[0]) as page, closing(page.render(scale=3)) as bitmap:
            image = bitmap.to_pil().convert("L")
            self.assertLess(image.getpixel((20, 20)), 5)
            self.assertLess(image.getpixel((235, 250)), 5)

    @unittest.skipUnless(ocr.which("tesseract") and importlib.util.find_spec("pypdfium2"), "PDFium e Tesseract richiesti")
    def test_scanned_attachment_is_read_once_after_enabling_ocr(self):
        source = self.home / "allegato.pdf"
        pdf_fixture(source)
        mail = self.docs / "allegato.eml"
        mail.write_bytes(b"Subject: pratica\nContent-Type: application/pdf\n"
                         b"Content-Disposition: attachment; filename*=UTF-8''scansione%20marzo.pdf\n"
                         b"Content-Transfer-Encoding: base64\n\n" + base64.b64encode(source.read_bytes()))
        indexer.index_all(self.cfg)
        cfg = dict(self.cfg, ocr=True)
        installed = ocr.which
        with mock.patch.object(ocr, "which", side_effect=lambda n: None if n == "pdftoppm" else installed(n)):
            self.assertEqual(indexer.index_all(cfg)["updated"], 1)
            self.assertEqual(indexer.index_all(cfg)["updated"], 0)
        with closing(db.connect()) as con:
            self.assertEqual([r[1] for r in library.audit(con, "alice@example.it")], ["allegato.eml"])

    @unittest.skipUnless(ocr.which("tesseract") and importlib.util.find_spec("pypdfium2"), "PDFium e Tesseract richiesti")
    def test_ocr_scans_force_target_and_pdf_image_redaction(self):
        from pypdf import PdfReader
        import pypdfium2 as pdf
        source, dest = self.docs/"verbale.pdf", self.docs/"copia.pdf"
        pdf_fixture(source)
        original = source.read_bytes()
        indexer.index_all(self.cfg)
        self.assertEqual(indexer.index_all(self.cfg, target=str(source), force_ocr=True)["updated"], 1)
        with closing(db.connect()) as con:
            self.assertEqual([r[1] for r in library.audit(con,"alice@example.it")], ["verbale.pdf"])
        review = redaction.PdfReview(source)
        try:
            with self.assertRaises(ValueError):
                review.save(dest)
            for term in ("SECRETWORD", "CUSTODE"):
                image, size, boxes = review.prepare(["email"], [term], "eng")
                self.assertGreater(boxes, 0)
                review.approve(image, size, [], boxes)
            review.save(dest)
            with self.assertRaises(FileExistsError):
                review.save(source)
        finally:
            review.close()
        self.assertEqual(source.read_bytes(), original)
        reader = PdfReader(dest)
        self.assertEqual(len(reader.pages), 2)
        self.assertFalse(reader.attachments)
        self.assertNotIn("segreto-nei-metadati", str(reader.metadata))
        with closing(pdf.PdfDocument(dest)) as output:
            for i, page in enumerate(reader.pages):
                self.assertFalse(page.extract_text())
                with closing(output[i]) as p, closing(p.render(scale=3)) as bitmap:
                    data = io.BytesIO()
                    rendered = bitmap.to_pil().convert("L")
                    rendered.save(data, "PNG")
                    # Anche lo spazio bianco tra i caratteri del dato coperto è diventato nero.
                    self.assertLess(rendered.getpixel((200, 175) if i == 0 else (155, 150)), 12)
                    # La parola vicina resta leggibile anche quando Tesseract cambia segmentazione.
                    from PIL import ImageChops, ImageStat
                    with closing(pdf.PdfDocument(source)) as original_pdf, closing(original_pdf[i]) as before:
                        with closing(before.render(scale=3)) as before_bitmap:
                            box = (650, 145, 880, 218) if i == 0 else (438, 107, 665, 185)
                            reference = before_bitmap.to_pil().convert("L").crop(box)
                            self.assertLess(reference.getextrema()[0], 30)
                            diff = ImageChops.difference(rendered.crop(box), reference)
                            self.assertLess(ImageStat.Stat(diff).mean[0], 5)
                run = subprocess.run([ocr.which("tesseract"),"stdin","stdout","-l","eng","--psm","6"],
                                     input=data.getvalue(),capture_output=True,check=True)
                seen = run.stdout.decode().lower()
                self.assertNotRegex(seen, r"custode|secretword|alice|example")


if __name__ == "__main__":
    unittest.main()
