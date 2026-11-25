# StillFind

App desktop per **cercare dentro i tuoi documenti**, al **100% offline**.  
Funziona su **Windows, macOS e Linux** (tema chiaro e scuro). Indice e configurazione restano sul tuo computer, in `~/.stillfind/`.

Se usavi CercaDoc, StillFind riapre l’archivio in `~/.cercadoc/` con le stesse credenziali. Per indicare un’altra cartella dati puoi usare `STILLFIND_HOME`. Le configurazioni che usano già `CERCADOC_HOME` restano valide.

---

## Cosa fa

- **Formati letti**: testo (`txt`, `md`, `csv`, `log`, `rtf`, `html`, `json`, `tex`, `xml`), **Office** (`docx`, `xlsx`, `pptx`), **OpenDocument** (`odt`, `ods`, `odp`), **PDF** (anche misti o scansionati), **email `.eml`** (inclusi gli allegati supportati), **fatture FatturaPA `.xml`**, **file firmati `.p7m`** (anche `.xml.p7m` e firme multiple), archivi **`.zip`** (un livello), **`.epub`** e **immagini** con OCR (`png`, `jpg`, `tif`, `bmp`). Indicizza anche titolo, autore e oggetto.
- **Ricerca rapida e precisa**: ignora maiuscole e accenti, riconosce singolari/plurali e radici italiane (Snowball) e propone *«Intendevi…?»* in caso di refusi.
  - Filtri da menu (**Tipo**, **Data**, **Cartella**, **Cerca in**) o da tastiera: `"frase esatta"`, `OR`, `NOT`, `NEAR`, `ext:pdf` (o `parola:pdf`), `name:`, `in:Cartella`, `after:2024-01`, `before:2024-12`, `cf:`, `piva:`, `iban:`, `protocollo:`, `importo:1200..1500`, `~450` (±5%).
- Le ricerche con più parole danno precedenza ai documenti che le contengono nello stesso passaggio. Il passaggio segue il paragrafo e si ferma al cambio di pagina, foglio o slide.
- **Anteprima e posizioni.** Risultati e brani indicano la pagina PDF, il nome del foglio Excel o il numero della slide PowerPoint. La vista *Testo completo* conserva gli a capo e segnala per ciascuna pagina PDF il testo digitale, l’OCR o le pagine ancora da leggere. Puoi espandere *Altre occorrenze*, passare tra le corrispondenze con `F3` o `⌘/Ctrl+G` e copiare il testo.
- **Collegamenti, versioni e scadenze**:
  - **Collegati**: trova documenti con lo stesso CF, P.IVA, IBAN, protocollo, thread email o file firmato, più la scheda cronologica della pratica.
  - **Confronta versioni**: affianca le differenze di parole e importi anche tra formati diversi (es. `.docx` ↔ PDF OCR ↔ `.p7m`).
  - **Scadenze**: estrae date interne e scadenze, con vista dedicata e avviso opzionale a 30 giorni.
  - **Strumenti**: ricerca **duplicati** (SHA-256), esportazione in **CSV**, cronologia e ricerche salvate, diagnostica con rilettura o OCR di un singolo file.
- **Leggero e automatico**: controlla le cartelle in background, riconosce spostamenti e rinomine senza rileggere i file, lavora a bassa priorità CPU (con pausa opzionale a batteria).
- **Icona di sistema**. Apri *Cerca…*, *Impostazioni* o *Informazioni*, controlla quanti file sono indicizzati e quanti attendono l’OCR, oppure metti in pausa e riprendi la lettura. La pausa resta attiva anche dopo il riavvio. *Esci* chiude la finestra e l’agente. La scorciatoia `Ctrl+Alt+K` (`⌘+⌥+K` su macOS) e un secondo avvio riportano in primo piano la finestra aperta.

---

## Modelli locali (facoltativi)

StillFind funziona subito per parole chiave senza alcun modello. Due funzioni facoltative usano motori **interamente locali**, senza download automatici:

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

- **Blocco a runtime**: all'avvio StillFind disabilita tutti i moduli di rete di Python (`socket`, `ssl`, `urllib`, `http`, `ftplib`, `smtplib`, `webbrowser`…).
- **Blocco nell'eseguibile**: i moduli di rete non vengono inclusi nel pacchetto; `build.py` ispeziona il risultato e fallisce se ne trova anche uno solo.
- **Recinto di sistema**: il processo lavoratore (incluso il motore ONNX) viene avviato senza accesso alla rete tramite `sandbox-exec` su macOS e `unshare`/`bwrap` su Linux.
- Anche la lettura avviata dalla finestra, la ricostruzione e l’OCR di un singolo file passano dal lavoratore separato. La finestra legge l’avanzamento dal database locale. Se il recinto non parte, la finestra segnala il problema. Su Windows occorrono regole di blocco in uscita per l’eseguibile e gli strumenti OCR installati, attive su tutti i profili.
- **Verifica diretta**: esegui `python3 -m stillfind --self-test-offline` (e, a programma aperto, controlla con `lsof -nP -i | grep -i python` o `netstat -ano`).

*Nota*: aprire un file con doppio clic lo affida al programma predefinito del sistema (es. il browser per un `.html`).

---

## Sicurezza e privacy dei dati

- **Temporanei OCR.** StillFind scrive le pagine renderizzate e i file di lavoro dell’OCR in `.ocr-tmp`, una sottocartella privata dell’indice. Li elimina a fine lavoro e, all’avvio successivo, ripulisce quelli lasciati da un arresto improvviso. I lavori ancora attivi possono proseguire.
- **Dischi scollegati.** In *Impostazioni → Opzioni avanzate → Conservazione dei dischi scollegati* puoi scegliere i giorni di attesa dalla prima assenza rilevata. Il valore iniziale **0** conserva i documenti a tempo indeterminato. Alla prima scansione dopo la scadenza, StillFind li rimuove dall’indice. Quando ricolleghi il disco, la scansione li indicizza di nuovo.
- **Rimozione selettiva.** Fai clic destro su un risultato per dimenticare il file o la cartella che lo contiene. Da *⋯ → Dimentica una cartella…* puoi scegliere anche una cartella su un disco scollegato. La rimozione include sottocartelle, testo, passaggi, vettori ed entità rimaste senza riferimenti. Gli originali restano sul disco. Per consentire di nuovo l’indicizzazione del percorso, scegli *Impostazioni → Opzioni avanzate → Percorsi dimenticati → Includi di nuovo* e salva.
- **Archivio cifrato opzionale (SQLCipher)**: attivabile al primo avvio o da *Impostazioni → Opzioni avanzate*. Cifra testo, cronologia, entità e **anche i vettori** (salvati dentro il database cifrato, mai in chiaro su disco), con migrazione guidata e reversibile.
  - **Portachiavi di sistema**: chiave locale custodita da DPAPI (Windows), Keychain (macOS) o Secret Service (Linux).
  - **Cassaforte con passphrase**: chiave solo in RAM, blocco automatico per inattività o manuale (*⋯ → Blocca cassaforte*). Nessuna chiave viene mai salvata in `config.json`.
- **Integrità dei modelli**: prima di caricare modello e tokenizer, StillFind ne verifica l'impronta **SHA-256**, controlla che il grafo usi solo operazioni ONNX standard senza riferimenti esterni e carica esattamente i byte verificati.
- **Audit e oscuramento reale**:
  - **Mappa dei dati personali** (*⋯*): individua in un clic tutti i file che contengono dati sensibili (CF, email, nomi, ecc.), anche in cartelle esposte come Scrivania o Download.
  - **Oscura copia…** (nell'anteprima): crea una copia oscurata senza toccare l'originale. Sui PDF copre irreversibilmente i pixel delle pagine approvate eliminando testo copiabile e metadati; sui testi sostituisce i valori con `[OMISSIS]`.
- **Difese locali**: protezione anti ZIP/XML bomb e cartella dati `~/.stillfind/` con permessi riservati all'utente.

---

## Architettura del codice

Il codice è in `stillfind/`, diviso per responsabilità. Ogni concetto è una classe; i moduli che restano a funzioni (`core/facts.py`, `core/stemmer.py`, `core/labels.py`, `core/system.py`, `ui/theme.py`) sono tabelle di regole o piccole utilità senza stato.

| Pacchetto | Contenuto |
|---|---|
| `core/` | `Settings` (configurazione), `ProcessLock`, `PrivateWorkspace`, errori, isolamento dalla rete a livello Python |
| `storage/` | `Database` (connessione e stato), `Schema` (migrazioni), `ArchiveProtection` (archivio cifrato), `CredentialStore`, `ArchiveEraser`, `VectorStore` |
| `extractors/` | `Extractor` e una sottoclasse per formato (`PdfExtractor`, `DocxExtractor`, `EmlExtractor`, `P7mExtractor`…), `ExtractorRegistry`, `Tesseract`, `MetadataReader` |
| `indexing/` | `Indexer` (scansione e lettura), `DocumentStore`, `Sources`, `RelationGraph`, `TextVersions`, `DuplicateFinder` |
| `search/` | `Query` e `SearchFilters` (cosa scrive l'utente), `SearchEngine` (ricerca FTS5), `DocumentPreview` |
| `semantic/` | `SemanticIndex`, `EmbeddingJob`, `SemanticSearch`, `Embedder`, `ModelCatalog` (modelli locali verificati) |
| `runtime/` | `Agent`, `WorkerRun`, `WorkerLauncher`, `NetworkIsolation`, `PowerMonitor`, `AppSignals`, `ShortcutBackend` e `TrayBackend` con una sottoclasse per sistema operativo |
| `ui/` | `App` (finestra principale), `dialogs/` (una finestra per file), `views/` (elenco risultati, schermata iniziale, collegamenti), `theme.py`, `widgets.py` |
| `tools/` | `CsvExporter`, `SavedSearches`, `PersonalDataAudit`, `Redactor` |
| `cli.py` | `CommandLine`: gli argomenti e le operazioni da terminale |

---

## Avvio da sorgente

```bash
python3 -m pip install customtkinter pypdf snowballstemmer "sqlcipher3>=0.6.2" "pypdfium2>=5.10" Pillow numpy
# Opzionale, per la ricerca per significato:
python3 -m pip install onnxruntime sentencepiece

python3 -m stillfind
```

Su una macchina isolata dalla rete puoi installare gli stessi pacchetti da una cartella locale di wheel:  
`python3 -m pip install --no-index --find-links /percorso/wheel ...`

Comandi principali da terminale:

```bash
python3 -m stillfind --add-folder ~/Documenti   # aggiunge una cartella
python3 -m stillfind --index                    # indicizza da terminale
python3 -m stillfind --search 'affitto:pdf'     # cerca da terminale
python3 -m stillfind --agent                    # agente in background + icona di sistema
python3 -m stillfind --semantic on              # attiva ricerca per significato (--embed per i vettori)
python3 -m stillfind --self-test-offline        # verifica il blocco della rete
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

- **Output**: `dist/StillFind.app` (macOS), `dist/StillFind/StillFind.exe` (Windows), `dist/StillFind/StillFind` (Linux).
- **Verifica automatica**: `build.py` esclude i moduli di rete, controlla che nessuno sia presente nell'archivio compilato e lancia `--self-test-offline` sull'eseguibile prodotto.
- **Ricerca per significato nell'app compilata**: copia la cartella `models/` accanto all'eseguibile.
- **Tutte e tre le piattaforme via CI**: il workflow [`.github/workflows/build.yml`](.github/workflows/build.yml) compila e verifica gli archivi per Windows, macOS e Linux su GitHub Actions.
- **App non firmate**: su macOS al primo avvio usa tasto destro → *Apri*; su Windows *Ulteriori informazioni* → *Esegui comunque*.

---

## Licenza

Copyright (C) 2026 Daniele Sisto — rilasciato sotto licenza **GNU GPL v3 o successiva** ([LICENSE](LICENSE)).  
Puoi usarlo, modificarlo e ridistribuirlo gratuitamente; chi distribuisce versioni modificate deve pubblicarne il codice sorgente con la stessa licenza e mantenere l'attribuzione «StillFind, di Daniele Sisto» (GPL-3.0, sez. 7b). Le licenze delle librerie di terze parti sono in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
