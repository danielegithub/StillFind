# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Controllo di un modello ONNX prima di caricarlo.

Tre verifiche, tutte da superare:
  1. **Impronta SHA-256** uguale a quella fissata nel codice: un solo byte diverso e il file è rifiutato.
  2. **Solo operazioni standard**: ogni nodo (anche dentro i sottografi e le funzioni) deve usare il dominio
     standard di ONNX e un'operazione che esiste nell'elenco ufficiale; domini personalizzati sono rifiutati.
  3. **Nessun file esterno**: i pesi devono stare dentro il file, senza rimandi ad altre posizioni del disco.

Il lettore del formato (protobuf) è scritto qui, senza librerie: serve anche nell'app installata. Legge la
stessa copia che poi va al motore e salta i pesi senza copiarli. `verified_runtime_source` mantiene
quella copia accessibile al motore per la durata del caricamento."""
from __future__ import annotations

import hashlib
import mmap
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from stillfind.semantic.onnx_ops import STANDARD_OPS

STANDARD_DOMAINS = frozenset({"", "ai.onnx"})
MAX_DEPTH = 24                      # annidamento massimo dei sottografi
MAX_MODEL_BYTES = 600 * 1024 * 1024  # oltre questa dimensione non è il modello atteso
CHUNK = 1024 * 1024


class ModelRejected(Exception):
    """Il modello non ha superato il controllo: la lista dei motivi è in `args[0]`."""


@dataclass
class ModelReport:
    domains: set = field(default_factory=set)
    ops: set = field(default_factory=set)            # (dominio, operazione)
    external_tensors: list = field(default_factory=list)
    nodes: int = 0
    functions: int = 0


# ---------------------------------------------------------------- lettore protobuf minimo
def _varint(buf, pos: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise ValueError("numero non valido")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _fields(buf, start: int, end: int):
    """(numero campo, tipo, valore o (inizio, fine)) per ogni campo del messaggio buf[start:end]."""
    pos = start
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wire = key >> 3, key & 7
        if num == 0:
            raise ValueError("campo non valido")
        if wire == 0:
            val, pos = _varint(buf, pos)
            yield num, wire, val
        elif wire == 2:
            size, pos = _varint(buf, pos)
            if pos + size > end:
                raise ValueError("campo oltre la fine del messaggio")
            yield num, wire, (pos, pos + size)
            pos += size
        elif wire == 1:
            pos += 8
            yield num, wire, None
        elif wire == 5:
            pos += 4
            yield num, wire, None
        else:
            raise ValueError("tipo di campo non supportato")
        if pos > end:
            raise ValueError("messaggio troncato")


def _text(buf, span) -> str:
    return bytes(buf[span[0]:span[1]]).decode("utf-8", "replace")


# ---------------------------------------------------------------- visita del modello
def _tensor(buf, span, rep: ModelReport) -> None:
    name, external = "", False
    for num, wire, val in _fields(buf, *span):
        if num == 8 and wire == 2:
            name = _text(buf, val)
        elif num == 14 and wire == 0 and val == 1:      # data_location = EXTERNAL
            external = True
        elif num == 13 and wire == 2:                   # external_data: rimando a un file
            external = True
    if external:
        rep.external_tensors.append(name or "?")


def _sparse(buf, span, rep: ModelReport) -> None:
    for num, wire, val in _fields(buf, *span):
        if num in (1, 2) and wire == 2:
            _tensor(buf, val, rep)


def _attribute(buf, span, rep: ModelReport, depth: int) -> None:
    for num, wire, val in _fields(buf, *span):
        if wire != 2:
            continue
        if num in (6, 11):          # g, graphs: sottografo (If, Loop, Scan…)
            _graph(buf, val, rep, depth + 1)
        elif num in (5, 10):        # t, tensors
            _tensor(buf, val, rep)
        elif num in (22, 23):       # sparse_tensor, sparse_tensors
            _sparse(buf, val, rep)


def _node(buf, span, rep: ModelReport, depth: int) -> None:
    op = domain = ""
    attrs = []
    for num, wire, val in _fields(buf, *span):
        if wire != 2:
            continue
        if num == 4:
            op = _text(buf, val)
        elif num == 7:
            domain = _text(buf, val)
        elif num == 5:
            attrs.append(val)
    rep.nodes += 1
    rep.domains.add(domain)
    rep.ops.add((domain, op))
    for a in attrs:
        _attribute(buf, a, rep, depth)


def _graph(buf, span, rep: ModelReport, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValueError("sottografi troppo annidati")
    for num, wire, val in _fields(buf, *span):
        if wire != 2:
            continue
        if num == 1:
            _node(buf, val, rep, depth)
        elif num == 5:
            _tensor(buf, val, rep)
        elif num == 15:
            _sparse(buf, val, rep)


def _function(buf, span, rep: ModelReport) -> None:
    rep.functions += 1
    for num, wire, val in _fields(buf, *span):
        if wire != 2:
            continue
        if num == 10:                       # domain della funzione
            rep.domains.add(_text(buf, val))
        elif num == 7:
            _node(buf, val, rep, 1)


def inspect_buffer(buf) -> ModelReport:
    """Legge la struttura di un modello ONNX (domini, operazioni, rimandi esterni). ValueError se non è ONNX."""
    rep = ModelReport()
    graph_seen = False
    for num, wire, val in _fields(buf, 0, len(buf)):
        if wire != 2:
            continue
        if num == 7:
            graph_seen = True
            _graph(buf, val, rep)
        elif num == 8:                      # opset_import
            for n2, w2, v2 in _fields(buf, *val):
                if n2 == 1 and w2 == 2:
                    rep.domains.add(_text(buf, v2))
        elif num == 25:
            _function(buf, val, rep)
    if not graph_seen:
        raise ValueError("nessun grafo: non è un modello ONNX")
    return rep


# ---------------------------------------------------------------- verifica completa
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def problems_in(rep: ModelReport, allowed_domains=STANDARD_DOMAINS) -> list[str]:
    out = []
    for d in sorted(rep.domains - set(allowed_domains)):
        out.append(f"dominio di operazioni non standard: «{d}»")
    for dom, op in sorted(rep.ops):
        if dom in STANDARD_DOMAINS and op not in STANDARD_OPS:
            out.append(f"operazione «{op}» che non esiste nello standard ONNX")
    if rep.external_tensors:
        n = len(rep.external_tensors)
        out.append(f"{n} tensori rimandano a file esterni (es. «{rep.external_tensors[0]}»): i pesi devono stare nel file")
    return out


def verify_model(path: str | Path, expected_sha256: str, allowed_domains=STANDARD_DOMAINS) -> ModelReport:
    """Controlla il file; se qualcosa non va solleva `ModelRejected` con tutti i motivi."""
    return _verify(Path(path), expected_sha256, allowed_domains)[0]


def read_verified(path: str | Path, expected_sha256: str, allowed_domains=STANDARD_DOMAINS) -> bytes:
    """Come `verify_model`, ma restituisce i byte controllati: caricare **questi** (non il percorso) nel motore."""
    return _verify(Path(path), expected_sha256, allowed_domains)[1]


@contextmanager
def verified_runtime_source(path: Path, expected: str):
    """Dà a ONNX Runtime gli stessi dati verificati, senza tenere una copia Python dei pesi.

    Su Unix il descrittore punta a una copia privata già scollegata dal filesystem. Su Windows il
    file aperto con FILE_SHARE_READ non può essere scritto, rinominato o sostituito durante il caricamento.
    """
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create_file.restype = wintypes.HANDLE
        # Il percorso privato evita anche sostituzioni di una cartella antenata del modello installato.
        with tempfile.TemporaryDirectory(prefix="stillfind_model_") as directory:
            snapshot = Path(directory) / "model.onnx"
            with snapshot.open("xb") as target:
                _copy_model(path, target)
            handle = create_file(str(snapshot), 0x80000000, 1, None, 3, 0x80, None)
            if handle == wintypes.HANDLE(-1).value:
                raise OSError(ctypes.get_last_error(), "Impossibile aprire il modello in sola lettura")
            with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb") as pinned:
                _check_snapshot(pinned, expected)
                yield str(snapshot)
    elif Path("/dev/fd").is_dir():
        with tempfile.TemporaryFile() as snapshot:
            _copy_model(path, snapshot)
            snapshot.flush()
            _check_snapshot(snapshot, expected)
            snapshot.seek(0)
            yield f"/dev/fd/{snapshot.fileno()}"
    else:
        yield read_verified(path, expected)


def _copy_model(path, target):
    with path.open("rb") as source:
        total = 0
        while chunk := source.read(CHUNK):
            total += len(chunk)
            if total > MAX_MODEL_BYTES:
                raise ModelRejected(["file del modello troppo grande"])
            target.write(chunk)


def _check_snapshot(file, expected):
    if os.fstat(file.fileno()).st_size == 0:
        raise ModelRejected(["file del modello vuoto"])
    with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
        _check_data(data, expected, STANDARD_DOMAINS)


def _verify(path: Path, expected: str, allowed_domains) -> tuple[ModelReport, bytes]:
    try:
        size = path.stat().st_size
    except OSError as ex:
        raise ModelRejected([f"file del modello non leggibile: {ex}"]) from None
    if size > MAX_MODEL_BYTES:
        raise ModelRejected([f"file troppo grande ({size // 1_000_000} MB)"])
    with open(path, "rb") as f:
        data = f.read()  # si controlla e poi si usa lo stesso contenuto: nessuno può cambiarlo in mezzo
    return _check_data(data, expected, allowed_domains), data


def _check_data(data, expected, allowed_domains):
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected.lower():
        raise ModelRejected([f"impronta SHA-256 diversa da quella attesa (trovata {digest[:16]}…)"])
    try:
        rep = inspect_buffer(data)
    except (ValueError, IndexError) as ex:
        raise ModelRejected([f"file non valido: {ex}"]) from None
    bad = problems_in(rep, allowed_domains)
    if bad:
        raise ModelRejected(bad)
    return rep
