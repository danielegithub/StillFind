# StillFind

> ## ⚠️ Versione ALPHA — solo per prova
>
> StillFind è in **alpha test**. Il programma e gli installatori possono contenere errori, comportarsi in modo **instabile** o cambiare senza preavviso. Installandolo lo usi **a tuo rischio**: gli autori **non si assumono alcuna responsabilità** per malfunzionamenti, **instabilità del sistema**, perdita o danneggiamento di dati o altri danni, diretti o indiretti, derivanti dall'installazione o dall'uso. Il programma è fornito "così com'è", senza garanzie di alcun tipo (vedi la licenza GPL-3.0-or-later).
>
> Prima di provarlo fai una copia di sicurezza dei dati importanti e, se possibile, usalo su un computer di prova. I documenti originali non vengono modificati dall'app, ma questo non è una garanzia.
>
> *English: StillFind is in alpha testing and may be unstable. Use it at your own risk; the authors accept no liability for system instability, data loss or any other damage. Provided "as is", without warranty.*

App desktop per **cercare dentro i tuoi documenti**, al **100% offline**.  
Funziona su **Windows, macOS e Linux** (tema chiaro e scuro). Indice e configurazione restano sul tuo computer, in `~/.stillfind/`.

Se usavi CercaDoc, StillFind riapre l’archivio in `~/.cercadoc/` con le stesse credenziali. Per indicare un’altra cartella dati puoi usare `STILLFIND_HOME`. Le configurazioni che usano già `CERCADOC_HOME` restano valide.

---

## Installazione (alpha)

Dalla pagina **Releases** di GitHub (le versioni sono marcate come *pre-release*) scarichi il file per il tuo sistema. Gli installatori sono **in alpha e non sono firmati**, quindi il sistema può mostrare un avviso di sicurezza.

| Sistema | File | Come si installa |
| --- | --- | --- |
| Windows | `StillFind-windows-setup.exe` | Esegui l'installatore (per l'utente corrente, senza amministratore). SmartScreen: *Ulteriori informazioni* → *Esegui comunque*. In alternativa `StillFind-windows.zip`, portatile. |
| macOS | `StillFind-macos.dmg` | Trascina StillFind in *Applicazioni*. Al primo avvio: tasto destro → *Apri*. In alternativa `StillFind-macos.zip`. |
| Linux | `stillfind_<versione>_amd64.deb` | `sudo apt install ./stillfind_<versione>_amd64.deb`. In alternativa `StillFind-linux.tar.gz`. |

Per toglierlo: disinstalla da *Impostazioni → App* (Windows), trascina l'app nel Cestino (macOS) o `sudo apt remove stillfind` (Linux). I dati (indice e configurazione) restano in `~/.stillfind/` e vanno cancellati a mano se non servono più.

Le Release si creano da sole con un tag `v*` (workflow [`build.yml`](.github/workflows/build.yml)), con gli stessi file e lo stesso avviso alpha ([`packaging/RELEASE_NOTES_ALPHA.md`](packaging/RELEASE_NOTES_ALPHA.md)). Gli installatori mostrano il testo di [`packaging/ALPHA.txt`](packaging/ALPHA.txt) prima dell'installazione (Windows) o accanto all'app (macOS e Linux).

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

## Interfaccia e impostazioni

La finestra principale affianca risultati e anteprima, con un divisore trascinabile. I controlli hanno bordi squadrati e seguono il tema chiaro o scuro. Al primo avvio scegli le cartelle da indicizzare e se includere le sottocartelle.

I menu **File**, **Strumenti** e **Aiuto** raccolgono i comandi dell’app. Le Impostazioni si aprono con `Ctrl+,` (`⌘+,` su macOS) e sono divise in cinque schede:

- **Cartelle e File**: cartelle incluse ed escluse, formati, limiti di dimensione e dischi scollegati.
- **Lettura e OCR**: scansioni e immagini, lingue, limite di pagine, scansione automatica e pausa a batteria.
- **Ricerca e Significato**: modello locale, passaggi indicizzati, entità da collegare e avvisi sulle scadenze.
- **Sicurezza e Privacy**: stato dell’archivio, cifratura e visibilità della password.
- **Aspetto e Sistema**: tema e scorciatoia globale.

**Salva** applica le modifiche; **Annulla** ripristina anche il tema mostrato in anteprima. La visibilità della password si applica subito, come indicato nella scheda Sicurezza.

Da *Strumenti → Stato e diagnostica dell’archivio* puoi vedere perché un file non è stato letto e riprovare la lettura o l’OCR. I file con errori e i PDF senza testo hanno elenchi separati.

---

## Lingua dell'interfaccia

L'interfaccia è disponibile in italiano, inglese, francese, spagnolo, tedesco e portoghese, con cataloghi di traduzione locali (nessuna rete, nessun download). Al primo avvio StillFind propone la lingua del sistema, se supportata, altrimenti l'inglese; la scelta vale subito per il resto del percorso. In seguito si cambia da **Impostazioni → Aspetto e Sistema → Lingua** e il cambio si applica alla successiva apertura dell'app. La lingua dell'interfaccia è indipendente dalle lingue OCR: cambiarla non modifica l'archivio né richiede una reindicizzazione. Le installazioni esistenti restano in italiano finché non si sceglie un'altra lingua.

Per chi sviluppa:

- I testi si scrivono in italiano nel codice con `tr("…")`, `trn("…", "…", n)` per i plurali e `mark("…")` per le tabelle (`stillfind/core/i18n.py`); le traduzioni sono in `stillfind/locales/*.json`.
- Il comportamento dell'app dipende solo da identificatori interni (`pdf`, `week`, `rank`…), mai dal testo tradotto. I motivi di lettura salvati nell'archivio si interpretano in un solo punto, `stillfind/core/problems.py`.
- `python -m stillfind.tools.i18n_check` verifica che ogni testo abbia tutte le lingue e gli stessi segnaposto.
- `STILLFIND_LANGUAGE=de` (o un altro codice) forza una lingua per le prove.

## Modelli e OCR inclusi nell'app

Le build distribuite includono Python, le librerie, il modello semantico, il tokenizer,
Tesseract e i dati OCR di italiano, inglese, francese, spagnolo, tedesco e portoghese (più i dati di orientamento `osd`). Non occorre
installare Python o Tesseract, copiare modelli o scaricare componenti al primo avvio.
La ricerca per parole chiave funziona subito; OCR e ricerca per significato si attivano
nelle impostazioni. Tutti i motori lavorano **interamente in locale**.

1. **Ricerca per significato (embeddings)**  
   Trova documenti per concetto anche senza le parole esatte (es. *«spese riscaldamento»* → *«ripartizione caldaia»*) e alimenta *Documenti simili*.
   - **Modello**: [`multilingual-e5-small`](https://huggingface.co/Xenova/multilingual-e5-small) (licenza MIT), quantizzato ONNX int8 (~118 MB) + tokenizer SentencePiece (~5 MB).
   - **Dove si trova**: `model_quantized.onnx` e `sentencepiece.bpe.model` sono incorporati nelle risorse dell'app. In sviluppo vengono letti da `models/` alla radice del progetto.
   - **Come gira**: su CPU tramite `onnxruntime`, `sentencepiece` e `numpy`, inclusi nella build, in un processo lavoratore separato a bassa priorità. I risultati vengono uniti a quelli testuali (*Reciprocal Rank Fusion*, dando precedenza alle parole esatte). La compilazione fallisce se mancano modello o librerie.

2. **OCR (scansioni e immagini)**  
   Legge PDF scansionati (solo le pagine prive di testo) e immagini usando **[Tesseract](https://github.com/tesseract-ocr/tesseract)** in italiano, inglese, francese, spagnolo, tedesco e portoghese (si scelgono nelle Impostazioni). Tesseract, le sue librerie e i dati `tessdata_fast` sono incorporati nel pacchetto; `pypdfium2`, anch'esso incluso, renderizza i PDF. Il testo estratto viene salvato pagina per pagina, così non va ricalcolato. Se cambi le lingue OCR e ci sono già documenti letti con OCR, StillFind chiede se applicarle solo alle prossime letture oppure rielaborare anche quelli già letti: la rielaborazione riguarda solo immagini e pagine scansionate (anche negli allegati di email e ZIP), conserva il risultato precedente finché la nuova lettura non riesce e riprende da sola se la interrompi.

   **Solo per sviluppare o compilare dai sorgenti**, installa Tesseract sulla macchina di build:
   ```bash
   brew install tesseract                          # macOS (compilazione)
   sudo apt install tesseract-ocr tesseract-ocr-ita  # Linux
   # Windows: choco install tesseract --yes
   ```
   `packaging/prepare_assets.py` prepara le lingue da incorporare nella build. Per
   l'avvio dai sorgenti, installa anche le lingue nel Tesseract di sistema (su macOS:
   `brew install tesseract-lang`). L'app compilata usa esclusivamente il proprio OCR.

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
- **Dischi scollegati.** In *Impostazioni → Cartelle e File → Conservazione dischi scollegati* puoi scegliere i giorni di attesa dalla prima assenza rilevata. Il valore iniziale **0** conserva i documenti a tempo indeterminato. Alla prima scansione dopo la scadenza, StillFind li rimuove dall’indice. Quando ricolleghi il disco, la scansione li indicizza di nuovo.
- **Rimozione selettiva.** Fai clic destro su un risultato per dimenticare il file o la cartella che lo contiene. Da *Strumenti → Dimentica una cartella…* puoi scegliere anche una cartella su un disco scollegato. La rimozione include sottocartelle, testo, passaggi, vettori ed entità rimaste senza riferimenti. Gli originali restano sul disco. Per consentire di nuovo l’indicizzazione del percorso, scegli *Impostazioni → Cartelle e File → Percorsi dimenticati → Includi di nuovo* e salva.
- **Archivio cifrato opzionale (SQLCipher)**: attivabile dal lucchetto nella finestra principale o da *Impostazioni → Sicurezza e Privacy*. Cifra testo, cronologia, entità e **anche i vettori** (salvati dentro il database cifrato, mai in chiaro su disco), con migrazione guidata e reversibile.
  - **Portachiavi di sistema**: chiave locale custodita da DPAPI (Windows), Keychain (macOS) o Secret Service (Linux).
  - **Cassaforte con passphrase**: chiave solo in RAM, blocco automatico per inattività o manuale (*File → Blocca cassaforte*). Nessuna chiave viene mai salvata in `config.json`.
- **Lucchetto e password**: il lucchetto indica se l’archivio è cifrato. Quando la visibilità è attiva, un clic mostra la passphrase o la chiave del portachiavi e permette di copiarla. Per nasconderla viene chiesta conferma; per mostrarla di nuovo devi reinserirla, con verifica contro l’archivio cifrato. La preferenza resta salvata, il segreto no: i buffer di sessione vengono azzerati quando lo nascondi, blocchi la cassaforte o chiudi l’app. Python/Tk non garantiscono la cancellazione di ogni copia temporanea interna al runtime.
- **Integrità dei modelli**: prima di caricare modello e tokenizer, StillFind ne verifica l'impronta **SHA-256**, controlla che il grafo usi solo operazioni ONNX standard senza riferimenti esterni e carica esattamente i byte verificati.
- **Audit e oscuramento reale**:
  - **Mappa dei dati personali** (menu *Strumenti*): individua in un clic tutti i file che contengono dati sensibili (CF, email, nomi, ecc.), anche in cartelle esposte come Scrivania o Download.
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
python3 -m pip install -r requirements-build.txt
python3 packaging/prepare_assets.py

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
.venv-build/bin/python -m pip install -r requirements-build.txt
.venv-build/bin/python packaging/prepare_assets.py

.venv-build/bin/python build.py
```

*(Su Windows sostituisci `.venv-build/bin/...` con `.venv-build\Scripts\...`.)*

- **Output**: `dist/StillFind.app` (macOS), `dist/StillFind/StillFind.exe` (Windows), `dist/StillFind/StillFind` (Linux).
- **Build completa obbligatoria**: include modello, tokenizer, ONNX Runtime, SentencePiece, Tesseract, le sue librerie e le sei lingue OCR. Non esiste un ripiego automatico a una build ridotta. Tesseract deve essere disponibile sulla macchina di compilazione; puoi indicarlo con `STILLFIND_TESSERACT`.
- **Download solo in preparazione**: `packaging/prepare_assets.py` scarica le risorse mancanti e verifica gli SHA-256 fissati in `packaging/assets.json`. Le risorse grandi restano fuori da Git. `build.py` verifica i file locali senza scaricarli; si può compilare offline dopo aver preparato dipendenze e risorse.
- **Verifica automatica**: oltre ai controlli sui moduli di rete, prova una copia ricollocata dell'app con PATH ridotto e cartella dati temporanea. Controlla gli hash, SQLCipher, Tcl/Tk, OCR delle sei lingue su immagini e PDF e inferenza ONNX. Su macOS il test nega anche la rete e l'accesso a Homebrew e ai sorgenti. Il rapporto è in `dist/bundle-report.json`.
- **Tutte e tre le piattaforme via CI**: il workflow [`.github/workflows/build.yml`](.github/workflows/build.yml) compila e verifica Windows, macOS e Linux su GitHub Actions. Oltre agli archivi crea gli installatori (Windows `.exe` con Inno Setup, macOS `.dmg`, Linux `.deb`, script in `packaging/`) e, con un tag `v*`, una pre-release. Gli installatori sono alpha e non firmati.
- **App non firmate**: su macOS al primo avvio usa tasto destro → *Apri*; su Windows *Ulteriori informazioni* → *Esegui comunque*.

---

## Licenza

Copyright (C) 2026 Daniele Sisto — rilasciato sotto licenza **GNU GPL v3 o successiva** ([LICENSE](LICENSE)).  
Puoi usarlo, modificarlo e ridistribuirlo gratuitamente; chi distribuisce versioni modificate deve pubblicarne il codice sorgente con la stessa licenza e mantenere l'attribuzione «StillFind, di Daniele Sisto» (GPL-3.0, sez. 7b). Le licenze delle librerie di terze parti sono in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
