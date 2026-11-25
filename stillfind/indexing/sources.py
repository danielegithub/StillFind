# SPDX-License-Identifier: GPL-3.0-or-later
"""Prima osservazione di un distacco e punti di montaggio delle cartelle indicizzate."""
import errno
import os
from pathlib import Path
import stat
import time

from stillfind.storage.archive import PathKey


def _mount(path):
    """Memorizza anche il volume che contiene una radice configurata più in profondità."""
    current = Path(path)
    for parent in (current, *current.parents):
        if parent.parent == parent:
            break
        if os.path.ismount(parent):
            return PathKey.of(parent)
    return ""


class Sources:
    def __init__(self, con, folders, days):
        self.days = days if type(days) is int and days > 0 else 0
        self.before = {p: (mount, since) for p, mount, since in
                       con.execute("SELECT path, mount, assente_da FROM sorgenti")}
        self.after = {}
        self.unreadable = set()
        roots = {PathKey.of(f["path"]): f.get("recursive", True) for f in folders}
        tracked = set(roots)
        # Un volume montato sotto una radice può lasciare una directory vuota dopo lo smontaggio.
        tracked.update(p for p, (mount, _) in self.before.items() if p == mount and
                       any(rec and PathKey.within(p, root) for root, rec in roots.items()))
        for path in tracked:
            self.after[path] = self.observe(path, *self.before.get(path, ("", None)))
        self._deadlines()

    def observe(self, path, mount, since):
        try:
            present = stat.S_ISDIR(os.stat(path).st_mode) and (not mount or os.path.ismount(mount))
        except (FileNotFoundError, NotADirectoryError):
            present = False
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.ENXIO) or getattr(exc, "winerror", None) in (15, 21, 53, 67, 1167):
                present = False
            else:
                self.unreadable.add(path)
                return mount, since
        if present:
            return mount or _mount(path), None
        return mount, time.time() if since is None else since

    def _deadlines(self):
        now = time.time()
        self.missing = {p for p, (_, since) in self.after.items() if since is not None}
        self.retained = {p for p in self.missing
                         if not self.days or now - self.after[p][1] < self.days * 86400}

    def recheck(self):
        # Un disco può sparire durante os.walk o tornare durante un OCR lungo.
        # In entrambi i casi una scansione parziale non prova la cancellazione dei file.
        for path, (mount, since) in self.after.items():
            self.after[path] = self.observe(path, mount, since)
            if since is not None and self.after[path][1] is None:
                self.unreadable.add(path)
        self._deadlines()

    def visit(self, path):
        key = PathKey.of(path)
        if key not in self.after and os.path.ismount(path):
            self.after[key] = (key, None)

    def scan_error(self, exc):
        if exc.filename:
            self.unreadable.add(PathKey.of(exc.filename))

    def unavailable(self, path):
        if not self.missing and not self.unreadable:
            return False
        key = PathKey.of(path)
        return any(PathKey.within(key, p) for p in self.missing) or any(PathKey.within(key, p) for p in self.unreadable)

    def expired(self, path):
        if not self.missing or self.missing == self.retained:
            return False
        key = PathKey.of(path)
        return (any(PathKey.within(key, p) for p in self.missing)
                and not any(PathKey.within(key, p) for p in self.retained)
                and not any(PathKey.within(key, p) for p in self.unreadable))

    def save(self, con):
        con.executemany("DELETE FROM sorgenti WHERE path=?", ((p,) for p in self.before.keys() - self.after.keys()))
        con.executemany("INSERT OR REPLACE INTO sorgenti VALUES(?,?,?)",
                        ((p, *data) for p, data in self.after.items() if self.before.get(p) != data))
        self.before = self.after.copy()
