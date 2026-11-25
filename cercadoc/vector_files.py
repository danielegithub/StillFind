# SPDX-License-Identifier: GPL-3.0-or-later
"""Generazioni dei file vettoriali. La manutenzione funziona anche senza modello e NumPy."""
from __future__ import annotations

from array import array
import os
from pathlib import Path
import re
from uuid import uuid4

CHUNK_ROWS = 4096
DELETED = -1


class VectorFiles:
    def __init__(self, path: str | os.PathLike, dim: int = 384):
        self.dim = dim
        self.base = Path(path)
        self.manifest = Path(str(path) + ".vec.current")

    def _generation(self):
        try:
            token = self.manifest.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return ""  # coppia di file degli archivi preesistenti
        if not re.fullmatch("[0-9a-f]{32}", token):
            raise ValueError("Generazione dei vettori non valida")
        return "." + token

    def _paths(self, generation):
        return (Path(str(self.base) + ".vec" + generation), Path(str(self.base) + ".ids" + generation))

    @property
    def vec_path(self):
        return self._paths(self._generation())[0]

    @property
    def ids_path(self):
        return self._paths(self._generation())[1]

    # ---- lettura
    def __len__(self) -> int:
        """Righe presenti nel file, comprese quelle cancellate e non ancora compattate."""
        try:
            return self.ids_path.stat().st_size // 8
        except FileNotFoundError:
            return 0

    def compact(self, exclude=()) -> int:
        """Pubblica una coppia coerente di file, omettendo le righe cancellate o richieste."""
        n = len(self)
        if not n:
            self.clean_retired()
            return 0
        excluded = {DELETED, *exclude}
        token = uuid4().hex
        tmp_v, tmp_i = self._paths("." + token)
        removed = 0
        with self.vec_path.open("rb") as vec, self.ids_path.open("rb") as ids, \
                tmp_v.open("xb") as fv, tmp_i.open("xb") as fi:
            for start in range(0, n, CHUNK_ROWS):
                count = min(CHUNK_ROWS, n - start)
                keys = array("q")
                keys.fromfile(ids, count)
                data = vec.read(count * self.dim)
                if len(data) != count * self.dim:
                    raise ValueError("File dei vettori incompleto.")
                if excluded.isdisjoint(keys):
                    fv.write(data)
                    keys.tofile(fi)
                else:
                    block = memoryview(data)
                    keep = [i for i, key in enumerate(keys) if key not in excluded]
                    fv.write(b"".join(block[i*self.dim:(i+1)*self.dim] for i in keep))
                    array("q", (keys[i] for i in keep)).tofile(fi)
                    removed += count - len(keep)
            for file in (fv, fi):
                file.flush()
                os.fsync(file.fileno())
        pending = self.manifest.with_suffix(".next")
        with pending.open("w", encoding="ascii") as marker:
            marker.write(token)
            marker.flush()
            os.fsync(marker.fileno())
        os.replace(pending, self.manifest)
        self.clean_retired()
        return removed

    def erase(self, ids):
        """Se è rimasta solo una vecchia generazione aperta, ritenta la pulizia senza ricopiare i vettori."""
        n = len(self)
        if n:
            excluded = ids | {DELETED}
            with self.ids_path.open("rb") as handle:
                for start in range(0, n, CHUNK_ROWS):
                    keys = array("q")
                    keys.fromfile(handle, min(CHUNK_ROWS, n - start))
                    if not excluded.isdisjoint(keys):
                        break
                else:
                    return self.clean_retired()
            self.compact(exclude=ids)
        return self.clean_retired()

    def clean_retired(self):
        """Il solo scrittore elimina le vecchie generazioni. Un lettore Windows può trattenerle."""
        keep = set(self._paths(self._generation()))
        clean = True
        for suffix in (".vec", ".ids"):
            base = Path(str(self.base) + suffix)
            paths = [base, Path(str(base) + ".new"), *(p for p in base.parent.glob(base.name + ".*")
                             if re.fullmatch(r"\.[0-9a-f]{32}", p.suffix))]
            for path in paths:
                if path not in keep:
                    try:
                        path.unlink(missing_ok=True)
                    except PermissionError:
                        clean = False
        return clean
