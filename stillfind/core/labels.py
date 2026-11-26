# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Testi e scelte dell'interfaccia (senza Tk, quindi si prova con i test).

Ogni scelta ha un identificatore interno stabile («pdf», «week»…): il comportamento dell'app dipende solo da quello,
mai dal testo tradotto che l'utente vede."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta

from stillfind.core import i18n
from stillfind.core.i18n import mark, tr, trn


class Choices:
    """Scelte di un menu: identificatore interno e testo (italiano nel codice) tradotto al momento di mostrarlo."""

    def __init__(self, items):
        self.items = tuple(items)

    @property
    def ids(self) -> list[str]:
        return [key for key, _ in self.items]

    def labels(self) -> list[str]:
        return [tr(text) for _, text in self.items]

    def label(self, key: str) -> str:
        return next((tr(text) for k, text in self.items if k == key), key)

    def id(self, label: str, default: str | None = None) -> str | None:
        """Identificatore della scelta mostrata con quel testo (nella lingua corrente)."""
        return next((k for k, text in self.items if tr(text) == label), default)


# categorie comprensibili al posto delle estensioni (DOCX, ODT, P7M…): (identificatore, estensioni)
CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("text", ("txt", "md", "rtf", "odt", "docx", "tex", "log")),
    ("pdf", ("pdf",)),
    ("sheets", ("xlsx", "ods", "csv")),
    ("slides", ("pptx", "odp")),
    ("email", ("eml",)),
    ("archives", ("zip", "epub")),
    ("web", ("html", "htm", "xml", "json")),
    ("signed", ("p7m",)),
    ("images", ("png", "jpg", "jpeg", "tif", "tiff", "bmp")),
)
CATEGORY_NAMES = Choices((("text", mark("Documenti di testo")), ("pdf", mark("PDF")),
                          ("sheets", mark("Fogli di calcolo")), ("slides", mark("Presentazioni")),
                          ("email", mark("Email")), ("archives", mark("Archivi e libri")),
                          ("web", mark("Pagine web e dati")), ("signed", mark("File firmati")),
                          ("images", mark("Immagini e foto"))))
ALL = "all"          # identificatore di «Tutti i tipi», «Tutte le cartelle», «Qualsiasi data»
CUSTOM_DATE = "custom"
DATE_CHOICES = Choices((("any", mark("Qualsiasi data")), ("week", mark("Ultima settimana")),
                        ("month", mark("Ultimo mese")), ("year", mark("Quest'anno")),
                        (CUSTOM_DATE, mark("Scegli le date…"))))
# «Cerca in»: identificatore = valore per `search.search(where=...)`
WHERE_CHOICES = Choices((("all", mark("Nome e contenuto")), ("content", mark("Solo nel contenuto")),
                         ("name", mark("Solo nel nome"))))
ORDER_CHOICES = Choices((("rank", mark("Più pertinenti")), ("date", mark("Più recenti")),
                         ("document_date", mark("Data del documento")), ("name", mark("Nome (A-Z)"))))
_DAY = 86400.0
_ICONS = {"pdf": "📕", "xlsx": "📊", "ods": "📊", "csv": "📊", "pptx": "📽", "odp": "📽", "eml": "✉️",
          "p7m": "🔏", "png": "🖼", "jpg": "🖼", "jpeg": "🖼", "tif": "🖼", "tiff": "🖼", "bmp": "🖼"}


def category_exts(category: str) -> tuple[str, ...]:
    return next((exts for name, exts in CATEGORIES if name == category), ())


def present_categories(exts) -> list[str]:
    """Gli identificatori delle categorie che hanno almeno un documento (per il menu «Tipo»)."""
    have = set(exts)
    return [name for name, group in CATEGORIES if have & set(group)]


def icon(ext: str) -> str:
    return _ICONS.get(ext.lower(), "📄")


def relative_date(ts: float, now: float | None = None) -> str:
    """«oggi», «ieri», «3 giorni fa», «12 gen», «12 gen 2024» (nella lingua corrente)."""
    now = time.time() if now is None else now
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    days = (n.date() - d.date()).days
    if days <= 0:
        return tr("oggi")
    if days == 1:
        return tr("ieri")
    if days < 7:
        return tr("{n} giorni fa", n=days)
    return i18n.Formats.date(d, with_year=d.year != n.year)


def date_after(choice: str, now: float | None = None) -> float | None:
    """Inizio dell'intervallo scelto nel menu «Data» (identificatore); None per «Qualsiasi data»."""
    now = time.time() if now is None else now
    if choice == "week":
        return now - 7 * _DAY
    if choice == "month":
        return now - 30 * _DAY
    if choice == "year":
        return datetime(datetime.fromtimestamp(now).year, 1, 1).timestamp()
    return None


_DATE_SPLIT = re.compile(r"[/.\-]")


def parse_period(text: str) -> tuple[float, float, str]:
    """Una data scritta a mano → (inizio, fine esclusa, testo normalizzato). Si capisce «31/12/2025»,
    «31.12.2025», «31-12-2025», «2025-12-31», ma anche solo il mese («12/2025», «2025-12») o l'anno («2025»):
    in quel caso il periodo è tutto il mese o tutto l'anno. ValueError con un messaggio comprensibile se non è una data."""
    t = text.strip()
    bad = ValueError(tr("Non capisco la data «{text}». Scrivila così: {full} (oppure {month}, oppure {year}).",
                        text=t, full=i18n.Formats.numeric_date(2025, 12, 31), month=i18n.Formats.numeric_date(2025, 12), year=2025))
    parts = _DATE_SPLIT.split(t)
    if not t or not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise bad
    nums = [int(p) for p in parts]
    if len(parts[0]) == 4:      # 2025, 2025-12, 2025-12-31
        y, m, d = nums[0], (nums[1] if len(nums) > 1 else 0), (nums[2] if len(nums) > 2 else 0)
    elif len(parts[-1]) == 4:   # 12/2025, 31/12/2025 (in inglese 12/31/2025)
        y = nums[-1]
        m, d = (nums[-2] if len(nums) > 1 else 0), (nums[0] if len(nums) == 3 else 0)
        if len(nums) == 3 and i18n.Formats.month_first():
            m, d = d, m
    else:
        raise bad
    try:
        if d:
            start = datetime(y, m, d)
            end, label = start + timedelta(days=1), i18n.Formats.numeric_date(y, m, d)
        elif m:
            start = datetime(y, m, 1)
            end, label = datetime(y + (m == 12), m % 12 + 1, 1), i18n.Formats.numeric_date(y, m)
        else:
            start, end, label = datetime(y, 1, 1), datetime(y + 1, 1, 1), str(y)
    except ValueError:
        raise bad from None
    return start.timestamp(), end.timestamp(), label


def parse_date_range(start_text: str, end_text: str) -> tuple[float | None, float | None, str]:
    """Le due date del dialogo «Scegli le date» (anche una sola) → (dopo, prima, etichetta per il menu).
    «Prima» è escluso e comprende tutto il periodo scritto: «al 12/2025» include tutto dicembre."""
    after = before = None
    first = last = ""
    if start_text.strip():
        after, _, first = parse_period(start_text)
    if end_text.strip():
        _, before, last = parse_period(end_text)
    if after is None and before is None:
        raise ValueError(tr("Scrivi almeno una data."))
    if after is not None and before is not None and after >= before:
        raise ValueError(tr("La data finale è prima di quella iniziale."))
    label = (f"{first} – {last}" if first and last else tr("dal {date}", date=first) if first
             else tr("fino al {date}", date=last))
    return after, before, label


def friendly_folder(folder: str, roots: list[str], sep: str = os.sep) -> str:
    """Cartella breve e leggibile: «Documenti › Contratti» invece del percorso completo."""
    folder = folder.rstrip("/\\")
    best = max((r.rstrip("/\\") for r in roots if folder == r.rstrip("/\\") or folder.startswith(r.rstrip("/\\") + sep)),
               key=len, default=None)
    if best:
        parts = [best.rsplit(sep, 1)[-1] or best] + [p for p in folder[len(best):].split(sep) if p]
    else:
        parts = [p for p in folder.split(sep) if p][-2:] or [folder]
    if len(parts) > 3:
        parts = [parts[0], "…"] + parts[-2:]
    return " › ".join(parts)


def thousands(n: int) -> str:
    return i18n.Formats.number(n)


# ---- stato dei vettori («significato») di un documento
EMBEDDING_NAMES = {"ready": mark("attivo"), "pending": mark("in coda"), "no_text": mark("non disponibile"),
                   "disabled": mark("disattivato")}


def embedding_tip(state: dict | None) -> str:
    """Spiega a parole semplici lo stato dei vettori di **quel** file (vedi `SemanticIndex.document_states`)."""
    if not state:
        return ""
    kind = state.get("state")
    if kind == "ready":
        n = state.get("passages", 0)
        text = trn("Embedding attivo · {n} passaggio calcolato con {model}",
                   "Embedding attivo · {n} passaggi calcolati con {model}", n, model=state.get("model", ""))
        if state.get("at_limit"):
            text += "\n" + tr("Il documento ha raggiunto il tetto di {n} passaggi: il resto non è cercabile per significato.",
                              n=thousands(state.get("max_passages", n)))
        return text
    if kind == "pending":
        if state.get("modified"):
            return tr("Embedding da aggiornare · il file è cambiato dopo l'ultimo calcolo: "
                      "il testo è già cercabile per parole, i vettori verranno ricalcolati in background")
        return tr("Embedding in coda · il testo è già cercabile per parole, "
                  "i vettori verranno calcolati in background")
    if kind == "no_text":
        return tr("Embedding non disponibile · documento privo di testo leggibile")
    if kind == "disabled":
        if state.get("reason") == "no_model":
            return tr("Embedding non disponibile · il modello non è presente nella cartella dell'app")
        if state.get("reason") == "no_libraries":
            return tr("Embedding non disponibile · mancano i componenti che lo eseguono")
        return tr("Embedding disattivato · attiva «Cerca anche per significato» nelle Impostazioni")
    return ""


# ---- riquadro dei modelli nel piè di pagina
def footer_models(model: dict, ocr: dict) -> dict:
    """Testi, tono del colore («text» attivo, «muted» spento, «err» da sistemare) e suggerimento dei due indicatori
    del piè di pagina: il modello del significato e l'OCR. `model` viene da `ModelCatalog.status`, `ocr` da
    `Tesseract.status`."""
    state = model["state"]
    if state == "on":
        text, tone = tr("Modello: {name} ({format})", name=model["name"], format=model["format"]), "text"
    else:
        text = tr("Significato: {state}", state=tr({"off": mark("spento"), "no_model": mark("modello assente"),
                                                    "no_libraries": mark("componenti mancanti"),
                                                    "error": mark("errore verifica")}[state]))
        tone = "muted" if state == "off" else "err"
    check = tr({"verified": mark("verificata: il modello è quello atteso"),
                "pending": mark("si controlla la prima volta che il modello serve"),
                "rejected": mark("RIFIUTATA: il file non è quello atteso o non è sicuro, il modello non viene usato"),
                "none": mark("non eseguita")}[model["check"]])
    lines = [tr({"on": mark("Ricerca per significato attiva"), "off": mark("Ricerca per significato spenta"),
                 "no_model": mark("Ricerca per significato non disponibile: il modello non c'è nella cartella indicata"),
                 "no_libraries": mark("Ricerca per significato non disponibile: mancano i componenti che la eseguono"),
                 "error": mark("Ricerca per significato non disponibile: il modello è stato rifiutato")}[state]),
             tr("Modello: {name} ({format})", name=model["name"], format=model["format"]),
             tr("Cartella dei modelli: {folder}", folder=model["dir"]),
             tr("Vettori: {dim} dimensioni · calcolati sul processore, senza internet", dim=model["dim"]),
             tr("Impronta SHA-256: {check}", check=check),
             tr("Ricerca per parole: SQLite FTS5 con radici italiane (Snowball IT), sempre attiva")]
    semantic = {"text": text, "tone": tone, "tip": "\n".join(lines)}

    if ocr["state"] == "on":
        o_text, o_tone = tr("OCR: Tesseract ({lang})", lang=ocr["lang"]), "text"
        o_tip = tr("Lettura dei documenti scansionati attiva\nProgramma: Tesseract, lingue {lang}\n"
                   "Percorso: {path}\nTutto sul tuo computer, senza internet", lang=ocr["lang"], path=ocr["exe"])
    elif ocr["state"] == "missing":
        o_text, o_tone = tr("OCR: Tesseract assente"), "err"
        o_tip = tr("L'OCR è attivo nelle Impostazioni, ma Tesseract non è stato trovato: i documenti scansionati non si leggono.")
    else:
        o_text, o_tone = tr("OCR: spento"), "muted"
        o_tip = tr("I PDF scansionati e le immagini non vengono letti. Si attiva da Impostazioni → Lettura e OCR.")
    return {"semantic": semantic, "ocr": {"text": o_text, "tone": o_tone, "tip": o_tip}}


# ---- blocco di avanzamento nel piè di pagina
WORK_PHASES = ("scan", "index", "embed", "forget")


def progress_percent(done: int, total: int) -> int:
    """Percentuale intera per difetto: 100% solo a lavoro finito, mai prima (99,6% si legge 99%)."""
    if total <= 0:
        return 0
    return 100 if done >= total else max(0, min(99, int(done * 100 / total)))


def footer_progress(st: dict, documents: int, quiet: bool = False) -> dict:
    """Che cosa mostra il blocco di avanzamento, a partire dallo stato del lavoratore (vedi `WorkerMonitor`).

    Restituisce `caption` (con la percentuale, in grassetto), `detail`, `value` (0-1, oppure None se la
    durata non è nota), `active` (c'è un lavoro in corso: compare «Ferma»), `percent` (None se non c'è) e `kind`
    (scan, index, embed, forget, paused, stopped, done, empty). `quiet`: controllo automatico delle novità,
    che non deve far lampeggiare «Scansione cartelle…» mentre cerca soltanto.
    `documents` è il numero di documenti nell'archivio."""
    phase = st.get("phase")
    done, total = int(st.get("done") or 0), int(st.get("total") or 0)
    manual = bool(st.get("manual_pause"))
    if quiet and phase == "scan":
        phase = None

    def block(kind, caption, detail="", value=None, active=False, percent=None):
        return {"kind": kind, "caption": caption, "detail": detail, "value": value, "active": active,
                "percent": percent}

    def count(unit):
        return f"{thousands(done)} / {thousands(total)} {tr(unit)}"

    if phase == "forget":
        return block("forget", tr("Rimozione dall’archivio…"), active=True)
    if phase in WORK_PHASES:
        battery = bool(st.get("paused"))
        unit = mark("doc.") if phase == "embed" else mark("file")
        if total <= 0:
            if phase == "embed":
                return block("embed", tr("Embedding: preparazione…"), active=True)
            return block("scan", tr("Scansione cartelle…"),
                         tr("{n} file trovati", n=thousands(int(st.get("scanned") or 0))), active=True)
        pct = progress_percent(done, total)
        paused = manual or battery
        if phase == "embed":
            caption = tr("Embedding in pausa: {pct}%", pct=pct) if paused else tr("Embedding: {pct}%", pct=pct)
        else:
            caption = tr("Indicizzazione in pausa: {pct}%", pct=pct) if paused else tr("Indicizzazione: {pct}%", pct=pct)
        return block("paused" if paused else phase, caption, count(unit), done / total, True, pct)
    # nessun lavoro in corso
    interrupted = bool(st.get("stopped")) or phase == "error"
    if total > 0 and done < total and (interrupted or manual):
        pct = progress_percent(done, total)
        caption = (tr("Indicizzazione interrotta: {pct}%", pct=pct) if interrupted
                   else tr("Indicizzazione in pausa: {pct}%", pct=pct))
        return block("stopped" if interrupted else "paused", caption,
                     tr("{count} · ne restano {left}", count=count(mark("file")), left=thousands(total - done)),
                     done / total, False, pct)
    if interrupted:
        return block("stopped", tr("Indicizzazione interrotta"), trn("{n} documento", "{n} documenti", documents),
                     1.0 if documents else 0.0)
    if manual:
        return block("paused", tr("Indicizzazione in pausa"), trn("{n} documento", "{n} documenti", documents),
                     1.0 if documents else 0.0)
    if not documents:
        return block("empty", tr("Nessun documento ancora"), value=0.0)
    return block("done", tr("Indicizzato: 100%"), trn("{n} documento", "{n} documenti", documents), 1.0, False, 100)
