# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Cartelle e file che normalmente non serve indicizzare (come un .gitignore già pronto).

Gli elementi sono nomi, anche con i caratteri jolly `*` e `?` (es. `*.app`, `*.min.js`), validi ovunque si
trovino e senza distinguere maiuscole da minuscole. L'utente può modificarli nelle Impostazioni."""
from __future__ import annotations

from fnmatch import fnmatchcase

DEFAULT_DIRS = [
    # dipendenze e pacchetti dei linguaggi di programmazione
    "node_modules", "bower_components", "jspm_packages", "vendor", "third_party", "Pods", "Carthage",
    "__pycache__", "venv", "virtualenv", "site-packages", "*.egg-info", ".*cache", "htmlcov",
    "target", "obj", "bin", "build", "dist", "DerivedData", "cmake-build-*", "gradle", ".gradle",
    "packages", "deps", "_build", "elm-stuff", "__MACOSX", "coverage",
    # progetti e strumenti di sviluppo
    "*.xcodeproj", "*.xcworkspace", "*.idea", "*.vscode", "*.dSYM", "*.framework", "*.bundle",
    # pacchetti e dati di sistema
    "Library", "Applications", "*.app", "*.photoslibrary", "*.musiclibrary", "Backups.backupdb",
    "$RECYCLE.BIN", "System Volume Information", "Windows", "Program Files", "Program Files (x86)",
    "ProgramData", "AppData", "Recovery", "lost+found", "Trash", ".Trash", "snap", "proc", "sys",
]

DEFAULT_FILES = [
    # file di blocco, di configurazione e generati dai gestori di pacchetti
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "tsconfig*.json",
    "composer.json", "composer.lock", "Pipfile.lock", "poetry.lock", "Cargo.lock", "Gemfile.lock", "go.sum",
    "*.lock", "*.csproj", "*.sln", "*.pbxproj", "*.xcconfig",
    # prodotti di compilazione e file minimizzati
    "*.min.js", "*.min.css", "*.bundle.js", "*.map", "*.d.ts", "*.pyc", "*.pyo", "*.class", "*.o", "*.obj", "*.a",
    "*.so", "*.dll", "*.dylib", "*.exe", "*.jar", "*.wasm",
    # file di servizio dei sistemi operativi
    ".DS_Store", "Thumbs.db", "desktop.ini", "ehthumbs.db", "*.tmp", "*.temp", "*.swp", "~*",
    # codice sorgente nei vari linguaggi (se qualcuno li aggiungesse ai tipi cercati)
    "*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.java", "*.kt", "*.c", "*.h", "*.cpp", "*.hpp", "*.cs", "*.go",
    "*.rs", "*.rb", "*.php", "*.swift", "*.m", "*.mm", "*.scala", "*.sh", "*.bat", "*.ps1", "*.lua", "*.pl",
    "*.r", "*.sql", "*.css", "*.scss", "*.less", "*.vue", "*.svelte", "*.yml", "*.yaml", "*.toml", "*.ini",
    "*.cfg", "*.gradle", "*.cmake", "Makefile", "Dockerfile",
]


class Exclusions:
    """Confronto dei nomi con gli elenchi di esclusione."""

    def __init__(self, dirs=(), files=()):
        self.dirs = [d.strip().casefold() for d in dirs if d.strip()]
        self.files = [f.strip().casefold() for f in files if f.strip()]

    @staticmethod
    def _match(name: str, patterns) -> bool:
        name = name.casefold()
        return any(name == p or fnmatchcase(name, p) for p in patterns)

    def skip_dir(self, name: str) -> bool:
        return self._match(name, self.dirs)

    def skip_file(self, name: str) -> bool:
        return self._match(name, self.files)
