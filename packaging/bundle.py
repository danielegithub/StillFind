"""Preparazione delle risorse native e verifica della distribuzione ricollocata."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def tesseract_path():
    requested = os.environ.get("STILLFIND_TESSERACT")
    choices = [requested] if requested else [shutil.which("tesseract")]
    if not requested:
        choices += ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract",
                    str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe")]
    for choice in choices:
        if choice and Path(choice).is_file():
            return Path(choice).resolve()
    raise RuntimeError("Tesseract manca sulla macchina di build. Installalo o imposta STILLFIND_TESSERACT.")


def arguments():
    # La compilazione in sé non scarica nulla: le risorse devono essere preparate prima.
    subprocess.run([sys.executable, str(ROOT / "packaging/prepare_assets.py"), "--offline"], check=True)
    exe = tesseract_path()
    subprocess.run([str(exe), "--version"], check=True, capture_output=True)
    guide_dir = ROOT / "stillfind/ui/guide_assets"
    if not guide_dir.is_dir() or not list(guide_dir.glob("*.png")):
        raise RuntimeError("Illustrazioni della guida mancanti in stillfind/ui/guide_assets.")
    locales_dir = ROOT / "stillfind/locales"
    if len(list(locales_dir.glob("*.json"))) < 1:
        raise RuntimeError("Cataloghi di traduzione mancanti in stillfind/locales.")
    args = ["--add-binary", f"{exe}{os.pathsep}ocr",
            "--add-data", f"{ROOT / 'models/model_quantized.onnx'}{os.pathsep}models",
            "--add-data", f"{ROOT / 'models/sentencepiece.bpe.model'}{os.pathsep}models",
            "--add-data", f"{ROOT / '.build-assets/tessdata'}{os.pathsep}ocr/tessdata",
            "--add-data", f"{guide_dir}{os.pathsep}guide_assets",
            "--add-data", f"{locales_dir}{os.pathsep}locales",
            "--add-data", f"{ROOT / 'stillfind/ui/brand'}{os.pathsep}brand",
            "--add-data", f"{ROOT / 'packaging/licenses'}{os.pathsep}licenses",
            "--add-data", f"{ROOT / 'packaging/assets.json'}{os.pathsep}."]
    if sys.platform == "win32":
        # Le DLL accanto al programma vanno mantenute accanto a tesseract.exe.
        for dll in sorted(exe.parent.glob("*.dll")):
            args += ["--add-binary", f"{dll}{os.pathsep}ocr"]
    # Include anche le licenze dei pacchetti Python transitivi.
    # Si copiano in una cartella sola: una voce `--add-data` per file supera il limite della riga di comando di Windows.
    python_notices = ROOT / ".build-assets/python-licenses"
    shutil.rmtree(python_notices, ignore_errors=True)
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        for entry in dist.files or []:
            if any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    destination = python_notices / name / entry.parent
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination / source.name)
    args += ["--add-data", f"{python_notices}{os.pathsep}licenses/python"]
    native_notices = ROOT / ".build-assets/native-licenses"
    native_notices.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        visited = set()

        def collect(binary):
            binary = binary.resolve()
            if binary in visited:
                return
            visited.add(binary)
            # Homebrew mantiene licenze, README e SBOM alla radice della formula.
            for parent in binary.parents:
                if (parent / "INSTALL_RECEIPT.json").exists():
                    destination = native_notices / parent.parent.name
                    destination.mkdir(exist_ok=True)
                    for file in parent.iterdir():
                        if file.is_file() and file.suffix != ".json":
                            shutil.copy2(file, destination / file.name)
                    break
            out = subprocess.check_output(["otool", "-L", str(binary)], text=True)
            for line in out.splitlines()[1:]:
                dep = line.strip().split(" (")[0]
                if dep.startswith(("/opt/homebrew/", "/usr/local/")):
                    collect(Path(dep))
        collect(exe)
    elif sys.platform.startswith("linux"):
        # Debian/Ubuntu conservano gli avvisi delle librerie native in /usr/share/doc.
        out = subprocess.check_output(["ldd", str(exe)], text=True)
        paths = [exe] + [Path(token) for line in out.splitlines() for token in line.split()
                         if token.startswith("/") and Path(token).is_file()]
        for path in paths:
            owner = subprocess.run(["dpkg-query", "-S", str(path)], capture_output=True, text=True)
            for line in owner.stdout.splitlines():
                package = line.split(": ")[0].split(":")[0]
                notice = Path("/usr/share/doc") / package / "copyright"
                if notice.is_file():
                    shutil.copy2(notice, native_notices / (package + "-copyright"))
    else:
        for file in exe.parent.rglob("*"):
            if file.is_file() and any(word in file.name.lower() for word in ("license", "copying", "notice")):
                destination = native_notices / file.relative_to(exe.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, destination)
    args += ["--add-data", f"{native_notices}{os.pathsep}licenses/native"]
    return args


def verify(package):
    """Prova una copia senza sorgenti, PATH di sviluppo o modelli esterni."""
    with tempfile.TemporaryDirectory(prefix="stillfind-bundle-") as directory:
        root = Path(directory)
        copied = root / package.name
        shutil.copytree(package, copied, symlinks=True)
        if sys.platform == "darwin":
            exe = copied / "Contents/MacOS/StillFind"
        else:
            exe = copied / ("StillFind.exe" if sys.platform == "win32" else "StillFind")
        report = root / "report.json"
        env = dict(os.environ, STILLFIND_HOME=str(root / "data"),
                   PATH=os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32")
                   if sys.platform == "win32" else "/usr/bin:/bin",
                   TESSDATA_PREFIX=str(root / "inesistente"))
        for key in ("PYTHONPATH", "PYTHONHOME", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
            env.pop(key, None)
        cmd = [str(exe), "--self-test-bundle", "--bundle-report", str(report)]
        if sys.platform == "darwin":
            # La build Mac deve funzionare senza rete e senza Homebrew o sorgenti.
            profile = '(version 1)(allow default)(deny network*)'
            for path in ("/opt/homebrew", "/usr/local", str(ROOT)):
                profile += f'(deny file-read* (subpath {json.dumps(path)}))'
            cmd = ["/usr/bin/sandbox-exec", "-p", profile, *cmd]
        result = subprocess.run(cmd, env=env, cwd=root, capture_output=True, text=True, timeout=240)
        if report.exists():
            data = json.loads(report.read_text())
            print(json.dumps(data, indent=2, ensure_ascii=False))
            (ROOT / "dist/bundle-report.json").write_text(json.dumps(data, indent=2) + "\n")
        else:
            data = {}
        if result.returncode or not data.get("ok"):
            raise RuntimeError(f"Pacchetto incompleto o non avviabile: {result.stdout}\n{result.stderr}\n{data}")
