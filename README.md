# CercaDoc

App desktop per **cercare dentro i tuoi documenti**, al **100% offline**.  
Funziona su **Windows, macOS e Linux** (tema chiaro e scuro). Indice e configurazione restano sul tuo computer, in `~/.cercadoc/`.

---

## Cosa fa

- **Formati letti**: testo (`txt`, `md`, `csv`, `log`, `rtf`, `html`, `json`, `tex`, `xml`), **Office** (`docx`, `xlsx`, `pptx`), **OpenDocument** (`odt`, `ods`, `odp`), **PDF** (anche misti o scansionati), **email `.eml`** (inclusi gli allegati supportati), **fatture FatturaPA `.xml`**, **file firmati `.p7m`** (anche `.xml.p7m` e firme multiple), archivi **`.zip`** (un livello), **`.epub`** e **immagini** con OCR (`png`, `jpg`, `tif`, `bmp`). Indicizza anche titolo, autore e oggetto.
- **Ricerca rapida e precisa**: ignora maiuscole e accenti, riconosce singolari/plurali e radici italiane (Snowball) e propone *«Intendevi…?»* in caso di refusi.
  - Filtri da menu (**Tipo**, **Data**, **Cartella**, **Cerca in**) o da tastiera: `"frase esatta"`, `OR`, `NOT`, `NEAR`, `ext:pdf` (o `parola:pdf`), `name:`, `in:Cartella`, `after:2024-01`, `before:2024-12`, `cf:`, `piva:`, `iban:`, `protocollo:`, `importo:1200..1500`, `~450` (±5%).
- **Anteprima completa**: numero di pagina nei PDF, elenco espandibile *Altre occorrenze*, vista *Occorrenze* o *Testo completo* (navigazione con `F3` / `⌘/Ctrl+G`) e copia rapida del testo.
- **Collegamenti, versioni e scadenze**:
  - **Collegati**: trova documenti con lo stesso CF, P.IVA, IBAN, protocollo, thread email o file firmato, più la scheda cronologica della pratica.
  - **Confronta versioni**: affianca le differenze di parole e importi anche tra formati diversi (es. `.docx` ↔ PDF OCR ↔ `.p7m`).
  - **Scadenze**: estrae date interne e scadenze, con vista dedicata e avviso opzionale a 30 giorni.
  - **Strumenti**: ricerca **duplicati** (SHA-256), esportazione in **CSV**, cronologia e ricerche salvate, diagnostica con rilettura o OCR di un singolo file.
- **Leggero e automatico**: controlla le cartelle in background, riconosce spostamenti e rinomine senza rileggere i file, lavora a bassa priorità CPU (con pausa opzionale a batteria) e si richiama con `Ctrl+Alt+K` (`⌘+⌥+K` su macOS) o dall'icona di sistema con `--agent`.

---

## Modelli locali (facoltativi)

CercaDoc funziona subito per parole chiave senza alcun modello. Due funzioni facoltative usano motori **interamente locali**, senza download automatici:

1. **Ricerca per significato (embeddings)**  
   Trova documenti per concetto anche senza le parole esatte (es. *«spese riscaldamento»* → *«ripartizione caldaia»*) e alimenta *Documenti simili*.
   - **Modello**: [`multilingual-e5-small`](https://huggingface.co/Xenova/multilingual-e5-small) (licenza MIT), quantizzato ONNX int8 (~118 MB) + tokenizer SentencePiece (~5 MB).
   - **Dove va**: file `model_quantized.onnx` e `sentencepiece.bpe.model` nella cartella `models/` accanto al programma.
   - **Come gira**: su CPU tramite `onnxruntime`, `sentencepiece` e `numpy`, in un processo lavoratore separato a bassa priorità. I risultati vengono uniti a quelli testuali (*Reciprocal Rank Fusion*, dando precedenza alle parole esatte). Senza modello o librerie, l'interruttore in Impostazioni resta spento e tutto il resto funziona normalmente.

2. **OCR (scansioni e immagini)**  
   Legge PDF scansionati (solo le pagine prive di testo) e immagini usando **[Tesseract](https://github.com/tesseract-ocr/tesseract)** con lingua italiana (`pypdfium2` per renderizzare i PDF, o Poppler come ripiego). Il testo estratto viene salvato pagina per pagina, così non va ricalcolato.
   ```bash
   brew install tesseract tesseract-lang             # macOS
   sudo apt install tesseract-ocr tesseract-ocr-ita  # Linux
   # Windows: installa Tesseract con la lingua italiana (percorso rilevato in automatico)
   ```

---

## 100% Offline

Nessun dato lascia il computer: zero telemetria, zero aggiornamenti automatici, zero download di modelli.

- **Blocco a runtime**: all'avvio CercaDoc disabilita tutti i moduli di rete di Python (`socket`, `ssl`, `urllib`, `http`, `ftplib`, `smtplib`, `webbrowser`…).
- **Blocco nell'eseguibile**: i moduli di rete non vengono inclusi nel pacchetto; `build.py` ispeziona il risultato e fallisce se ne trova anche uno solo.
- **Recinto di sistema**: il processo lavoratore (incluso il motore ONNX) viene avviato senza accesso alla rete tramite `sandbox-exec` su macOS e `unshare`/`bwrap` su Linux.
- **Verifica diretta**: esegui `python3 -m cercadoc --self-test-offline` (e, a programma aperto, controlla con `lsof -nP -i | grep -i python` o `netstat -ano`).

*Nota*: aprire un file con doppio clic lo affida al programma predefinito del sistema (es. il browser per un `.html`).

---

## Sicurezza e privacy dei dati

- **Archivio cifrato opzionale (SQLCipher)**: attivabile al primo avvio o da *Impostazioni → Opzioni avanzate*. Cifra testo, cronologia, entità e **anche i vettori** (salvati dentro il database cifrato, mai in chiaro su disco), con migrazione guidata e reversibile.
  - **Portachiavi di sistema**: chiave locale custodita da DPAPI (Windows), Keychain (macOS) o Secret Service (Linux).
  - **Cassaforte con passphrase**: chiave solo in RAM, blocco automatico per inattività o manuale (*⋯ → Blocca cassaforte*). Nessuna chiave viene mai salvata in `config.json`.
- **Integrità dei modelli**: prima di caricare modello e tokenizer, CercaDoc ne verifica l'impronta **SHA-256**, controlla che il grafo usi solo operazioni ONNX standard senza riferimenti esterni e carica esattamente i byte verificati.
- **Audit e oscuramento reale**:
  - **Mappa dei dati personali** (*⋯*): individua in un clic tutti i file che contengono dati sensibili (CF, email, nomi, ecc.), anche in cartelle esposte come Scrivania o Download.
  - **Oscura copia…** (nell'anteprima): crea una copia oscurata senza toccare l'originale. Sui PDF copre irreversibilmente i pixel delle pagine approvate eliminando testo copiabile e metadati; sui testi sostituisce i valori con `[OMISSIS]`.
- **Difese locali**: protezione anti ZIP/XML bomb e cartella dati `~/.cercadoc/` con permessi riservati all'utente.

---

## Avvio da sorgente

```bash
python3 -m pip install customtkinter pypdf snowballstemmer "sqlcipher3>=0.6.2" "pypdfium2>=5.10" Pillow numpy
# Opzionale, per la ricerca per significato:
python3 -m pip install onnxruntime sentencepiece

python3 -m cercadoc
```

Su una macchina isolata dalla rete puoi installare gli stessi pacchetti da una cartella locale di wheel:  
`python3 -m pip install --no-index --find-links /percorso/wheel ...`

Comandi principali da terminale:

```bash
python3 -m cercadoc --add-folder ~/Documenti   # aggiunge una cartella
python3 -m cercadoc --index                    # indicizza da terminale
python3 -m cercadoc --search 'affitto:pdf'     # cerca da terminale
python3 -m cercadoc --agent                    # agente in background + icona di sistema
python3 -m cercadoc --semantic on              # attiva ricerca per significato (--embed per i vettori)
python3 -m cercadoc --self-test-offline        # verifica il blocco della rete
```

---

## Ricompilare l'eseguibile

PyInstaller non fa cross-compilazione: esegui i comandi sul sistema di destinazione (Windows, macOS o Linux).

```bash
python3 -m venv .venv-build
.venv-build/bin/pip install pyinstaller pypdf customtkinter snowballstemmer "sqlcipher3>=0.6.2" "pypdfium2>=5.10" Pillow numpy
# Opzionale, per includere il supporto alla ricerca per significato:
.venv-build/bin/pip install onnxruntime sentencepiece

.venv-build/bin/python build.py
```

*(Su Windows sostituisci `.venv-build/bin/...` con `.venv-build\Scripts\...`.)*

- **Output**: `dist/CercaDoc.app` (macOS), `dist/CercaDoc/CercaDoc.exe` (Windows), `dist/CercaDoc/CercaDoc` (Linux).
- **Verifica automatica**: `build.py` esclude i moduli di rete, controlla che nessuno sia presente nell'archivio compilato e lancia `--self-test-offline` sull'eseguibile prodotto.
- **Ricerca per significato nell'app compilata**: copia la cartella `models/` accanto all'eseguibile.
- **Tutte e tre le piattaforme via CI**: il workflow [`.github/workflows/build.yml`](.github/workflows/build.yml) compila e verifica gli archivi per Windows, macOS e Linux su GitHub Actions.
- **App non firmate**: su macOS al primo avvio usa tasto destro → *Apri*; su Windows *Ulteriori informazioni* → *Esegui comunque*.

### Test

```bash
python3 -m unittest -q test_cercadoc test_privacy_features
```

---

## Licenza

Copyright (C) 2026 Daniele Sisto — rilasciato sotto licenza **GNU GPL v3 o successiva** ([LICENSE](LICENSE)).  
Puoi usarlo, modificarlo e ridistribuirlo gratuitamente; chi distribuisce versioni modificate deve pubblicarne il codice sorgente con la stessa licenza e mantenere l'attribuzione «CercaDoc, di Daniele Sisto» (GPL-3.0, sez. 7b). Le licenze delle librerie di terze parti sono in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
