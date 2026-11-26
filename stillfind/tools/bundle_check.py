# SPDX-License-Identifier: GPL-3.0-or-later
"""Prova funzionale del pacchetto completo, senza documenti dell'utente."""
import hashlib
import json
from pathlib import Path
import sys
import traceback


def check(report_path=None):
    report = {"ok": False, "checks": []}
    try:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Questa verifica richiede l'eseguibile compilato.")
        from stillfind.core.ocr_languages import OcrLanguages
        from stillfind.core.resources import guide_assets_dir, resource_root
        from stillfind.core.offline import self_test_offline
        from stillfind.core.private_tmp import PrivateWorkspace
        from stillfind.ui.dialogs.help import GuideFigure, HelpDialog
        root = resource_root()
        manifest = json.loads((root / "assets.json").read_text())
        packaged = {Path(item["path"]).stem for item in manifest if item["path"].endswith(".traineddata")}
        missing = ({code for code, _ in OcrLanguages.LANGUAGES} | {"osd"}) - packaged
        if missing:
            raise RuntimeError(f"Lingue OCR mancanti dal manifest: {sorted(missing)}")
        for item in manifest:
            relative = item["path"].replace(".build-assets/tessdata/", "ocr/tessdata/")
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError(f"Risorsa assente o corrotta: {relative}")
        report["checks"].append("SHA-256 modelli, tokenizer e lingue OCR")
        from stillfind.core import i18n
        from stillfind.core.resources import locales_dir
        catalogs = i18n.Translator.load_catalogs(locales_dir())
        if not locales_dir().is_dir() or any(not catalogs[code] for code in catalogs):
            raise RuntimeError(f"Cataloghi di traduzione mancanti nel pacchetto: {locales_dir()}")
        report["checks"].append("cataloghi di traduzione (" + ", ".join(sorted(catalogs)) + ")")
        assets_dir = guide_assets_dir()
        figure_assets = [b["asset"] for c in HelpDialog.CHAPTERS for b in c["blocks"] if b.get("kind") == "figure"]
        if not assets_dir.is_dir() or not figure_assets:
            raise RuntimeError(f"Cartella illustrazioni guida mancante: {assets_dir}")
        for asset in figure_assets:
            light_img, dark_img = GuideFigure._load_sources(asset)
            if light_img is None or dark_img is None:
                raise RuntimeError(f"Illustrazione guida mancante nel pacchetto: {asset}")
        report["checks"].append(f"illustrazioni guida incorporate ({len(figure_assets)} figure chiaro/scuro)")
        if not self_test_offline():
            raise RuntimeError("Blocco rete Python non attivo")
        report["checks"].append("blocco rete Python")
        import customtkinter
        window = customtkinter.CTk()
        try:
            window.withdraw()
            customtkinter.CTkLabel(window, text="StillFind").pack()
            window.update_idletasks()
        finally:
            window.destroy()
        report["checks"].append("interfaccia e runtime Tcl/Tk")
        import sqlcipher3
        with sqlcipher3.connect(":memory:") as con:
            if not con.execute("PRAGMA cipher_version").fetchone():
                raise RuntimeError("SQLCipher non disponibile")
        report["checks"].append("SQLCipher")
        from PIL import Image, ImageDraw, ImageFont
        from stillfind.extractors.ocr import Tesseract
        with PrivateWorkspace.workspace() as tmp:
            page = Image.new("RGB", (1000, 180), "white")
            ImageDraw.Draw(page).text((30, 40), "StillFind documenti 12345", fill="black",
                                      font=ImageFont.load_default(size=48))
            png, pdf = tmp / "prova.png", tmp / "prova.pdf"
            page.save(png)
            page.save(pdf, "PDF")
            for lang in [code for code, _ in OcrLanguages.LANGUAGES] + ["ita+eng", "+".join(c for c, _ in OcrLanguages.LANGUAGES)]:
                text = Tesseract(lang).recognize(str(png))
                # Alcune lingue separano le parole in modo diverso («Still Find»): conta il contenuto.
                if "12345" not in text or "stillfind" not in "".join(text.lower().split()):
                    raise RuntimeError(f"OCR {lang} fallito: {text!r}")
            report["checks"].append("OCR immagine nelle sei lingue, in combinazione e con orientamento osd")
            from pypdf import PdfReader
            if len(PdfReader(pdf).pages) != 1:
                raise RuntimeError("Lettura PDF fallita")
            text = Tesseract("ita+eng").recognize_pdf_pages(str(pdf), [1])
            if "12345" not in text[1]:
                raise RuntimeError("OCR PDF fallito")
            report["checks"].append("lettura PDF, rendering PDFium e OCR PDF")
        from stillfind.semantic.embedder import Embedder
        import numpy as np
        vectors = Embedder(threads=1).embed(["Una fattura da cercare", "Documento di prova"])
        if vectors.shape != (2, 384) or not np.isfinite(vectors).all():
            raise RuntimeError("Inferenza del modello fallita")
        if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=0.001):
            raise RuntimeError("Vettori del modello non validi")
        report["checks"].append("SentencePiece e inferenza ONNX (vettori 384 dimensioni)")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if sys.stdout:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1
