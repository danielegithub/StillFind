# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Modelli ammessi, con l'impronta SHA-256 fissata: un file diverso, anche di un solo byte, non si carica.

I modelli si cercano solo nella cartella di installazione (`models/`), mai da altre posizioni e mai da internet."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from stillfind.semantic.modelcheck import ModelRejected, read_verified, verify_model

MODELS = {
    # Ricerca per significato. Verificato il 24/11/2026: 1.511 nodi, 22 operazioni, tutte del dominio standard
    # ONNX (opset 11), nessun file esterno. Licenza del modello originale (intfloat/multilingual-e5-small):
    # MIT, riverificata il 24/11/2026 sull'API di Hugging Face; va inclusa nei notice.
    "multilingual-e5-small": {
        "file": "model_quantized.onnx",
        "sha256": "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
        "bytes": 118_308_185,
        "dim": 384,
        "source": "https://huggingface.co/Xenova/multilingual-e5-small",
        # il modello è addestrato con questi prefissi: senza, i risultati peggiorano
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "max_tokens": 256,          # token per passaggio (il modello ne accetta 512; 256 è più veloce)
        # tokenizer SentencePiece di XLM-RoBERTa (lo stesso del modello originale), anch'esso con impronta fissata
        "tokenizer": {
            "file": "sentencepiece.bpe.model",
            "sha256": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
            "bytes": 5_069_051,
            "source": "https://huggingface.co/intfloat/multilingual-e5-small",
        },
    },
}

DEFAULT_MODEL = "multilingual-e5-small"


class ModelCatalog:
    """Cerca e controlla i file dei modelli ammessi nella cartella `models/`."""

    @staticmethod
    def default_dir() -> Path:
        """Cartella `models/` accanto all'eseguibile installato (o alla radice del progetto in sviluppo)."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "models"
        return Path(__file__).resolve().parents[2] / "models"

    @staticmethod
    def model_path(name: str, models_dir: str | Path) -> Path:
        return Path(models_dir) / MODELS[name]["file"]

    @staticmethod
    def tokenizer_path(name: str, models_dir: str | Path) -> Path:
        return Path(models_dir) / MODELS[name]["tokenizer"]["file"]

    @classmethod
    def is_installed(cls, name: str = DEFAULT_MODEL, models_dir: str | Path | None = None) -> bool:
        """I file del modello ci sono (controllo veloce, senza calcolare le impronte)."""
        d = Path(models_dir) if models_dir else cls.default_dir()
        return name in MODELS and cls.model_path(name, d).is_file() and cls.tokenizer_path(name, d).is_file()

    @classmethod
    def check(cls, name: str, models_dir: str | Path):
        """Controllo completo del modello `name`; solleva `ModelRejected` se non è quello atteso."""
        if name not in MODELS:
            raise ModelRejected([f"modello sconosciuto: «{name}»"])
        return verify_model(cls.model_path(name, models_dir), MODELS[name]["sha256"])

    @classmethod
    def load_model(cls, name: str, models_dir: str | Path) -> bytes:
        """Byte del modello già controllati: passare **questi** al motore, non il percorso del file."""
        if name not in MODELS:
            raise ModelRejected([f"modello sconosciuto: «{name}»"])
        return read_verified(cls.model_path(name, models_dir), MODELS[name]["sha256"])

    @classmethod
    def load_tokenizer(cls, name: str, models_dir: str | Path) -> bytes:
        """Byte del tokenizer con l'impronta verificata (si carica questo contenuto, non il percorso)."""
        if name not in MODELS:
            raise ModelRejected([f"modello sconosciuto: «{name}»"])
        spec = MODELS[name]["tokenizer"]
        path = cls.tokenizer_path(name, models_dir)
        try:
            if path.stat().st_size > 4 * spec["bytes"]:
                raise ModelRejected([f"tokenizer troppo grande ({path.stat().st_size // 1_000_000} MB)"])
            data = path.read_bytes()
        except OSError as ex:
            raise ModelRejected([f"tokenizer non leggibile: {ex}"]) from None
        digest = hashlib.sha256(data).hexdigest()
        if digest != spec["sha256"]:
            raise ModelRejected([f"impronta SHA-256 del tokenizer diversa da quella attesa (trovata {digest[:16]}…)"])
        return data
