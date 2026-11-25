# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Calcolo dei vettori «di significato» con un modello ONNX, tutto sul processore del computer.

Il tokenizer riceve i byte verificati e ONNX Runtime apre una copia privata verificata del modello.
Le librerie si importano alla prima ricerca per significato o nel lavoratore che calcola i vettori.

Il tokenizer è quello di XLM-RoBERTa (SentencePiece), con la numerazione dei token del modello: `<s>`=0,
`<pad>`=1, `</s>`=2, `<unk>`=3 e ogni altro pezzo è l'identificativo di SentencePiece più uno."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from stillfind.semantic import modelcheck, models
from stillfind.semantic.models import ModelCatalog

BATCH = 8  # passaggi calcolati insieme: più grande = un po' più veloce ma più memoria


class EmbedderUnavailable(Exception):
    """Il modello non si può usare (file mancanti, librerie non installate, controllo non superato)."""


class Embedder:
    def __init__(self, name: str = models.DEFAULT_MODEL, models_dir: str | Path | None = None,
                 threads: int | None = None):
        self.name = name
        spec = models.MODELS.get(name)
        if spec is None:
            raise EmbedderUnavailable(f"modello sconosciuto: «{name}»")
        self.spec, self.dim = spec, int(spec["dim"])
        d = Path(models_dir) if models_dir else ModelCatalog.default_dir()
        try:
            import onnxruntime as ort
            import sentencepiece as spm
        except ImportError as ex:
            raise EmbedderUnavailable(f"libreria non installata: {ex.name}") from None
        ort.disable_telemetry_events()
        try:
            tok_bytes = ModelCatalog.load_tokenizer(name, d)
        except modelcheck.ModelRejected as ex:
            raise EmbedderUnavailable("; ".join(map(str, ex.args[0])) if ex.args and isinstance(ex.args[0], list)
                                      else str(ex)) from None
        self.sp = spm.SentencePieceProcessor(model_proto=tok_bytes)
        del tok_bytes
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads or max(1, min(2, os.cpu_count() or 1))  # pochi core: il PC resta libero
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        try:
            with modelcheck.verified_runtime_source(ModelCatalog.model_path(name, d), spec["sha256"]) as source:
                self.session = ort.InferenceSession(source, sess_options=opts, providers=["CPUExecutionProvider"])
        except (OSError, RuntimeError, ValueError, modelcheck.ModelRejected) as ex:
            raise EmbedderUnavailable(str(ex)) from ex
        self._inputs = {i.name for i in self.session.get_inputs()}

    # ---- tokenizzazione
    def tokens(self, text: str, max_tokens: int | None = None) -> list[int]:
        limit = max_tokens or int(self.spec["max_tokens"])
        pieces = [3 if p == 0 else p + 1 for p in self.sp.encode(text)]
        return [0] + pieces[:limit - 2] + [2]

    # ---- vettori
    def embed(self, texts: list[str], kind: str = "passage", max_tokens: int | None = None) -> np.ndarray:
        """Vettori normalizzati (float32, uno per testo). `kind`: «passage» per i documenti, «query» per la ricerca."""
        prefix = self.spec["query_prefix" if kind == "query" else "passage_prefix"]
        seqs = [self.tokens(prefix + t, max_tokens) for t in texts]
        out = np.zeros((len(seqs), self.dim), dtype=np.float32)
        order = sorted(range(len(seqs)), key=lambda i: len(seqs[i]))  # simili per lunghezza: meno riempimento
        for start in range(0, len(order), BATCH):
            idx = order[start:start + BATCH]
            width = max(len(seqs[i]) for i in idx)
            ids = np.ones((len(idx), width), dtype=np.int64)  # 1 = <pad>
            mask = np.zeros((len(idx), width), dtype=np.int64)
            for row, i in enumerate(idx):
                ids[row, :len(seqs[i])] = seqs[i]
                mask[row, :len(seqs[i])] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]                       # (lotto, token, dim)
            pooled = np.einsum("btd,bt->bd", hidden, mask, dtype=np.float32, casting="unsafe")
            pooled /= mask.sum(axis=1, keepdims=True)
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            out[idx] = pooled / np.where(norm == 0, 1, norm)
        return out
