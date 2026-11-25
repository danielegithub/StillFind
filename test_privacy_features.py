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
import uuid
import unittest
from unittest import mock
import zipfile

from cercadoc import agent, archive, config, db, hotkey, indexer, library, private_tmp, protection, redaction, search, semantic, sources
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

    def test_private_ocr_workspaces_survive_other_launch_and_clean_crashed_jobs(self):
        with private_tmp.workspace() as work:
            self.assertEqual(work.parent, self.home / ".ocr-tmp")
            if os.name != "nt":
                self.assertEqual(work.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(work.stat().st_mode & 0o777, 0o700)
            (work / "pagina.png").write_bytes(b"privato")
            crashed = subprocess.run([sys.executable, "-c",
                "from cercadoc import private_tmp; import os; "
                "job=private_tmp.workspace(); p=job.__enter__(); (p/'pagina.png').write_bytes(b'privato'); os._exit(9)"],
                timeout=10)
            self.assertEqual(crashed.returncode, 9)
            self.assertEqual(len(list(work.parent.glob("lavoro-*"))), 2)
            subprocess.run([sys.executable, "-m", "cercadoc", "--list"], stdout=subprocess.DEVNULL,
                           check=True, timeout=10)
            self.assertEqual(list(work.parent.glob("lavoro-*")), [work])
            self.assertEqual((work / "pagina.png").read_bytes(), b"privato")
        self.assertFalse(work.exists())
        rc, output, _ = ocr._run_subprocess([sys.executable, "-c", "import tempfile;print(tempfile.gettempdir())"],
                                            timeout=10)
        self.assertEqual(rc, 0)
        self.assertEqual(Path(output.decode().strip()).parent, private_tmp.root_path())
        self.assertFalse(list(private_tmp.root_path().glob("lavoro-*")))

    def test_offline_timer_survives_restarts_resets_and_purges_only_expired_sources(self):
        disk = self.docs / "disco"
        disk.mkdir()
        (disk / "atto.txt").write_text("documento esterno")
        (self.docs / "conserva.txt").write_text("documento interno")
        cfg = dict(self.cfg, folders=[{"path": str(self.docs), "recursive": False}, {"path": str(disk)}])
        unplugged = self.home.parent / "scollegato"
        with mock.patch.object(sources.time, "time", return_value=1000) as clock:
            indexer.index_all(cfg)
            disk.rename(unplugged)
            self.assertTrue(indexer.index_all(cfg, dry_run=True)["changes"])
            indexer.index_all(cfg)
            clock.return_value += 50 * 86400
            self.assertEqual(indexer.index_all(cfg)["removed"], 0)  # conservazione illimitata
            disk_back = unplugged.rename(disk)
            cfg["offline_retention_days"] = 3
            self.assertTrue(indexer.index_all(cfg, dry_run=True)["changes"])  # va azzerata la data
            indexer.index_all(cfg)
            disk_back.rename(unplugged)
            indexer.index_all(cfg)
            clock.return_value += 3 * 86400 - 1
            self.assertEqual(indexer.index_all(cfg)["removed"], 0)
            clock.return_value += 1
            self.assertTrue(indexer.index_all(cfg, dry_run=True)["changes"])
            self.assertEqual(indexer.index_all(cfg)["removed"], 1)
            with closing(db.connect()) as con:
                self.assertEqual(con.execute("SELECT name FROM docs").fetchall(), [("conserva.txt",)])
                self.assertFalse(con.execute("SELECT 1 FROM dirs WHERE path=?", (str(disk),)).fetchone())
            unplugged.rename(disk)
            self.assertEqual(indexer.index_all(cfg)["added"], 1)

    def test_unmounted_volume_with_an_empty_mountpoint_keeps_its_documents(self):
        mount = self.docs / "supporto"
        mount.mkdir()
        (mount / "esterno.txt").write_text("dati esterni")
        attached = True
        ismount = os.path.ismount
        def mounted(path):
            return attached if Path(path) == mount else ismount(path)
        with mock.patch.object(sources.os.path, "ismount", side_effect=mounted):
            indexer.index_all(self.cfg)
            mount.rename(self.home / "volume-staccato")
            mount.mkdir()  # directory ancora accessibile sul disco interno
            attached = False
            self.assertEqual(indexer.index_all(self.cfg)["removed"], 0)
            with closing(db.connect()) as con:
                self.assertIsNotNone(con.execute("SELECT assente_da FROM sorgenti WHERE path=?", (str(mount),)).fetchone()[0])
                self.assertEqual(con.execute("SELECT count(*) FROM docs").fetchone(), (1,))

    def test_unplug_during_extraction_preserves_last_searchable_content(self):
        path = self.docs / "verbale.txt"
        path.write_text("versione conservata")
        indexer.index_all(self.cfg)
        path.write_text("versione successiva più lunga")
        def unplug(*args, **kwargs):
            self.docs.rename(self.home.parent / "scollegato")
            raise FileNotFoundError(str(path))
        with mock.patch.object(indexer, "extract", side_effect=unplug):
            indexer.index_all(dict(self.cfg, offline_retention_days=1))
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT content FROM fts").fetchone(), ("versione conservata",))
            self.assertIsNotNone(con.execute("SELECT assente_da FROM sorgenti").fetchone()[0])

    def test_forget_folder_cleans_graph_vectors_and_cache_without_affecting_siblings(self):
        import numpy as np
        scope = self.docs / "atti_%"
        other = self.docs / "atti_%2"
        scope.mkdir()
        other.mkdir()
        a = scope / "pratica.txt"
        b = other / "pratica.txt"
        a.write_text("segreto alpha@example.it comune@example.it")
        b.write_text("conservare comune@example.it")
        indexer.index_all(self.cfg)
        with closing(db.connect()) as con:
            docs = dict(con.execute("SELECT path,id FROM docs"))
            for doc_id in docs.values():
                con.execute("INSERT INTO pages(doc_id,n,start) VALUES(?,1,0)", (doc_id,))
                con.execute("INSERT INTO vec_chunks(id,doc_id,start,end) VALUES(?,?,0,5)", (doc_id, doc_id))
                con.execute("INSERT INTO vec_done VALUES(?,1,1,'prova')", (doc_id,))
            store = semantic.open_store(con=con)
            store.add([docs[str(a)], docs[str(b)]], np.eye(store.dim)[:2])
            ocr._OcrCache(con, "parziale", "ita", str(a)).put(1, "cache sensibile")
            con.commit()
            db.set_paused(con, True)
            config.save_config(self.cfg)
            with agent.spawn_worker(from_window=True, target=str(scope), forget="folder") as worker:
                self.assertNotIn(str(scope), worker.args)
                self.assertEqual(worker.wait(timeout=15), 0)
            self.assertTrue(db.worker_status(con)["manual_pause"])
            self.assertEqual(db.worker_status(con)["removed"], 1)
            self.assertEqual(con.execute("SELECT path FROM docs").fetchall(), [(str(b),)])
            self.assertFalse(search.search(con, "segreto"))
            for table in ("passaggi", "vec_chunks", "pages", "vec_done", "analisi"):
                self.assertFalse(con.execute(f"SELECT 1 FROM {table} WHERE doc_id=?", (docs[str(a)],)).fetchone())
            self.assertFalse(con.execute("SELECT 1 FROM entita WHERE valore_normalizzato='alpha@example.it'").fetchone())
            self.assertTrue(con.execute("SELECT 1 FROM entita WHERE valore_normalizzato='comune@example.it'").fetchone())
            self.assertFalse(con.execute("SELECT 1 FROM ocr_cache").fetchone())
            self.assertEqual(store.count(), 1)
            self.assertIsNone(store.mean([docs[str(a)]]))
            self.assertTrue(a.exists())
            (scope / "nuovo.txt").write_text("da saltare")
            indexer.index_all(self.cfg)
            self.assertEqual(con.execute("SELECT count(*) FROM docs").fetchone(), (1,))
            self.assertEqual(archive.forget(con, str(b), False), 1)
            self.assertTrue(b.exists())
            db.reset_index(con)
            self.assertEqual(indexer.index_all(self.cfg)["added"], 0)
            with con:
                con.execute("DELETE FROM esclusioni WHERE path=?", (str(b),))
            self.assertEqual(indexer.index_all(self.cfg)["added"], 1)

    def test_interrupted_vector_removal_retries_without_optional_libraries(self):
        import numpy as np
        target = self.docs / "da togliere.txt"
        target.write_text("contenuto privato")
        config.save_config(self.cfg)
        indexer.index_all(self.cfg)
        with closing(db.connect()) as con:
            doc_id, = con.execute("SELECT id FROM docs").fetchone()
            con.execute("INSERT INTO vec_chunks VALUES(7,?,0,5)", (doc_id,))
            store = semantic.open_store(con=con)
            store.add([7, 8], np.eye(store.dim)[:2])
            con.commit()
            with mock.patch("cercadoc.vector_files.os.replace", side_effect=OSError("pubblicazione interrotta")), \
                 self.assertRaisesRegex(OSError, "interrotta"):
                archive.forget(con, target, False)
            self.assertFalse(con.execute("SELECT 1 FROM docs").fetchone())
            self.assertEqual(con.execute("SELECT id FROM vettori_da_togliere").fetchall(), [(7,)])
            self.assertTrue(indexer.index_all(self.cfg, dry_run=True)["changes"])
            subprocess.run([sys.executable, "-S", "-m", "cercadoc", "--index"],
                           check=True, capture_output=True, timeout=15)
            self.assertFalse(con.execute("SELECT 1 FROM vettori_da_togliere").fetchone())
            self.assertEqual(store.count(), 1)
            self.assertIsNone(store.mean([7]))
            self.assertIsNotNone(store.mean([8]))
            self.assertEqual(set(db.vector_files(self.path)), {store.vec_path, store.ids_path, store.manifest})

    @unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher richiesto")
    def test_forget_inside_vault_removes_encrypted_vectors_and_keeps_exclusion_private(self):
        target = self.docs / "nome-riservato.txt"
        target.write_text("email riservata soloqui@example.it")
        indexer.index_all(self.cfg)
        protection.migrate(self.path, "vault", "frase di collaudo")
        with closing(db.connect()) as con:
            doc_id, = con.execute("SELECT id FROM docs").fetchone()
            with con:
                con.execute("INSERT INTO vec_chunks VALUES(17,?,0,5)", (doc_id,))
                con.execute("INSERT INTO vector_values VALUES(17,?)", (bytes(384),))
            with agent.spawn_worker(from_window=True, target=str(target), forget="file") as proc:
                self.assertEqual(proc.wait(timeout=15), 0)
            for table in ("docs", "passaggi", "brani_fts", "entita", "fts", "vec_chunks", "vector_values", "vettori_da_togliere"):
                self.assertFalse(con.execute(f"SELECT 1 FROM {table}").fetchone(), table)
            self.assertEqual(con.execute("SELECT path FROM esclusioni").fetchall(), [(archive.path_key(target),)])
            self.assertFalse(db.vector_files(self.path))
        for item in self.home.iterdir():
            if item.is_file():
                self.assertNotIn(target.name.encode(), item.read_bytes())

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

    def test_worker_pause_resume_contention_and_recovery(self):
        config.save_config(self.cfg)
        (self.docs / "prova.txt").write_text("firmato")
        with closing(db.connect()) as con:
            db.set_paused(con, True)
            run = uuid.uuid4().hex
            with agent.spawn_worker(sandbox=False, run=run) as proc:
                try:
                    # La prima riga viene emessa dopo il commit, da un altro processo.
                    self.assertIn('"phase": "scan"', proc.stdout.readline())
                    state = agent.read_status(con)
                    self.assertEqual((state["run"], state["indexed"], state["manual_pause"]), (run, 0, True))
                    with agent.spawn_worker(sandbox=False) as other:
                        self.assertEqual(other.wait(timeout=10), agent.BUSY)
                    self.assertEqual(agent.read_status(con)["run"], run)
                    db.set_paused(con, False)
                    proc.communicate(timeout=15)
                    self.assertEqual(proc.returncode, 0)
                finally:
                    if proc.poll() is None:
                        proc.kill()
            self.assertEqual((agent.read_status(con)["phase"], agent.read_status(con)["indexed"]), ("done", 1))
            db.set_paused(con, True)
            with agent.spawn_worker(sandbox=False) as interrupted:
                self.assertIn('"phase": "scan"', interrupted.stdout.readline())
                interrupted.kill()
                interrupted.wait(timeout=10)
            self.assertEqual(agent.read_status(con)["phase"], "error")
            self.assertTrue(db.worker_status(con)["manual_pause"])

    @unittest.skipUnless(sys.platform == "darwin", "recinto nativo macOS")
    def test_window_worker_cancel_target_retry_and_rebuild(self):
        config.save_config(dict(self.cfg, network_sandbox=False))
        doc = self.docs / "prova.txt"
        doc.write_text("prima")
        indexer.index_all(self.cfg)
        doc.write_text("seconda versione")
        new = self.docs / "nuovo.txt"
        new.write_text("altra pratica")
        with closing(db.connect()) as con:
            db.set_paused(con, True)
            run = uuid.uuid4().hex
            with agent.spawn_worker(from_window=True, run=run, rebuild=True) as proc:
                self.assertEqual(proc.args[0], "/usr/bin/sandbox-exec")
                # Ferma annulla anche una ricostruzione in attesa prima di azzerare l'indice.
                agent.stop_file(run).touch()
                self.assertEqual(proc.wait(timeout=15), 0)
            self.assertTrue(db.worker_status(con)["stopped"])
            self.assertEqual(con.execute("SELECT content FROM fts").fetchone(), ("prima",))
            db.set_paused(con, False)
            with agent.spawn_worker(from_window=True, target=str(doc), retry_errors=True) as proc:
                self.assertNotIn(str(doc), proc.args)
                self.assertEqual(proc.wait(timeout=15), 0)
            self.assertEqual(con.execute("SELECT content FROM fts").fetchall(), [("seconda versione",)])
            with agent.spawn_worker(from_window=True, target=str(doc), force_ocr=True) as proc:
                self.assertNotEqual(proc.wait(timeout=15), 0)
            self.assertIn("OCR", db.worker_status(con)["error"])
            with agent.spawn_worker(from_window=True, rebuild=True) as proc:
                self.assertEqual(proc.wait(timeout=15), 0)
            self.assertEqual(db.worker_status(con)["indexed"], 2)

    @unittest.skipUnless(sys.platform == "darwin" and importlib.util.find_spec("sqlcipher3"),
                         "recinto macOS e SQLCipher")
    def test_window_worker_reads_target_inside_unlocked_vault(self):
        config.save_config(self.cfg)
        target = self.docs / "riservato.txt"
        target.write_text("testo originale")
        indexer.index_all(self.cfg)
        protection.migrate(self.path, "vault", "frase di collaudo")
        target.write_text("testo aggiornato")
        with closing(db.connect()) as con, agent.spawn_worker(from_window=True, target=str(target)) as proc:
            self.assertNotIn(str(target), proc.args)
            self.assertNotIn("frase di collaudo", proc.args)
            self.assertEqual(proc.wait(timeout=15), 0)
            self.assertEqual(con.execute("SELECT content FROM fts").fetchone(), ("testo aggiornato",))
            self.assertEqual(db.worker_status(con)["updated"], 1)
            self.assertEqual(db.worker_status(con)["indexed"], 1)

    def test_ocr_queue_counts_documents_and_tray_uses_the_same_snapshot(self):
        from cercadoc import tray
        for name in ("uno.png", "due.png", "testo.txt"):
            (self.docs / name).write_text("test")
        progress = []
        with mock.patch.object(ocr, "ocr_available", return_value=True), \
             mock.patch.object(ocr, "_tesseract", return_value="riconosciuto"):
            indexer.index_all(dict(self.cfg, ocr=True), progress.append)
        self.assertEqual(max(st["ocr_pending"] for st in progress), 2)
        self.assertIn(1, [st["ocr_pending"] for st in progress if st["phase"] == "index"])
        with mock.patch.object(tray, "status", [3, 1, True]):
            self.assertEqual(tray.entries(), [("open", "Cerca…"), (None, "3 file indicizzati · 1 OCR in coda"),
                             ("resume", "Riprendi"), ("settings", "Impostazioni"), ("about", "Informazioni"),
                             ("quit", "Esci")])

    def test_second_window_forwards_dialog_without_opening_tk(self):
        from cercadoc import gui
        with mock.patch.object(hotkey, "APP_DIR", self.home), \
             mock.patch.object(hotkey, "SIGNAL", self.home / "apri-ricerca"), \
             agent.acquire_lock(self.home / "window.lock"), \
             mock.patch.object(gui.security_ui, "startup", side_effect=AssertionError("seconda GUI")):
            gui.run(action="settings")
            self.assertTrue(hotkey.consume("open"))
            self.assertTrue(hotkey.consume("settings"))

    @unittest.skipIf(sys.platform == "win32", "Windows usa il firewall")
    def test_window_requires_a_sandbox_before_launch(self):
        with mock.patch.object(agent.netlock, "wrapper", return_value=None), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("avvio fuori recinto")):
            with self.assertRaisesRegex(RuntimeError, "recinto"):
                agent.spawn_worker(sandbox=False, from_window=True)

    def test_idle_lock_before_open_signal(self):
        from types import SimpleNamespace
        from cercadoc import gui
        session = SimpleNamespace(protection={"mode":"vault","minutes":2}, _last_input=time.monotonic()-121,
                                  _quit_token=hotkey.quit_token())
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
