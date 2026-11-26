# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Guida completa e illustrata di StillFind, interamente locale."""
from __future__ import annotations

import sys
import tkinter as tk

import customtkinter as ctk
from PIL import Image

from stillfind.core.i18n import tr
from stillfind.core.resources import guide_assets_dir
from stillfind.runtime.signals import AppSignals
from stillfind.ui.theme import PAL, card, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareDialog

MOD = "⌘" if sys.platform == "darwin" else "Ctrl"
MONO_FAMILY = "Menlo" if sys.platform == "darwin" else "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"


def examples() -> tuple[tuple[str, str], ...]:
    """Valutata a ogni apertura: i testi seguono la lingua dell'interfaccia."""
    return (
        ("contratto affitto", tr("Trova i documenti che contengono tutte le parole, anche nelle forme flesse italiane (contratti, affitti…).")),
        ('"canone mensile"', tr("Cerca la frase esatta, con le parole vicine e nello stesso ordine.")),
        ("affitto OR locazione", tr("Trova i documenti che contengono almeno una delle due parole.")),
        ("affitto NOT box", tr("Esclude tutti i risultati in cui compare la parola indicata dopo NOT.")),
        ("NEAR(clausola penale, 6)", tr("Trova i documenti in cui i termini compaiono a poche parole di distanza.")),
        ("contr*", tr("Prefisso da almeno 3 lettere: trova contratto, contratti, contrattuale, controparte.")),
        ("name:preventivo", tr("Cerca soltanto nel nome del file (usa content:parola per cercare solo nel testo interno).")),
        ("atto notarile:pdf", tr("Forma rapida per filtrare il formato: equivale a scrivere atto notarile ext:pdf (vale anche tipo:p7m).")),
        ('in:Fatture  /  in:"Pratiche 2026"', tr("Limita la ricerca a una cartella (e alle sue sottocartelle) con quel nome.")),
        ("after:2025-01  before:2025-12", tr("Filtra per data di modifica del file: accetta anno (2025), mese (2025-01) o giorno (2025-01-31).")),
        ("cf:XXXXXX99A01X000S", tr("Trova i documenti che citano quel codice fiscale (esempio fittizio; supporta anche omocodia).")),
        ("piva:90000000001  iban:IT78X000...", tr("Filtra per Partita IVA, codice IBAN o indirizzo email (es. email:utente@esempio.invalid).")),
        ("protocollo:PR-12/2026", tr("Trova tutti i documenti associati a uno specifico numero di protocollo o pratica.")),
        ("importo:1200..1500", tr("Trova i passaggi con un importo in euro compreso tra i due estremi indicati.")),
        ("~450  /  importo:~450", tr("Trova una cifra entro il ±5% di 450 euro (da 427,50 € a 472,50 €), utile quando ricordi l'importo circa.")),
    )


def shortcuts() -> tuple[tuple[str, str], ...]:
    """Valutata a ogni apertura: i testi seguono la lingua dell'interfaccia."""
    return (
        ("F1", tr("Apri questa guida")),
        (f"{MOD}+K / {MOD}+F", tr("Vai subito alla barra di ricerca")),
        (f"{MOD}+,", tr("Apri le Impostazioni")),
        ("F3 / Shift+F3", tr("Occorrenza successiva / precedente")),
        (AppSignals.LABEL, tr("Richiama StillFind da ogni app")),
        ("Esc", tr("Pulisci ricerca / chiudi finestra")),
    )


def _chapters() -> tuple[dict, ...]:
    return (
        {
            "id": "interface",
            "nav": tr("1. Mappa dell’interfaccia"),
            "title": tr("1. Mappa dell’interfaccia"),
            "lead": (
                tr("StillFind organizza tutte le funzioni in una griglia a spigolo vivo divisa in cinque fasce "
                "orizzontali: barra dei menu, intestazione con stato cifratura, barra di ricerca con filtri, "
                "area centrale divisa tra risultati e anteprima, e piè di pagina con avanzamento e modelli.")
            ),
            "blocks": (
                {
                    "kind": "figure",
                    "asset": "01-home-panoramica",
                    "caption": tr("Figura 1.1 — Vista generale di StillFind: barra menu in alto, lucchetto di cifratura, ricerca e filtri, elenco risultati con badge embedding, anteprima e footer con percentuale e modelli."),
                },
                {
                    "kind": "section",
                    "title": tr("Barra dei menu (File, Strumenti, Aiuto) e Intestazione"),
                    "body": (
                        tr("In cima alla finestra si trova la barra dei menu classica, utilizzabile con il mouse o da "
                        "tastiera (Tab, Invio, Spazio e frecce):")
                    ),
                    "bullets": (
                        tr("File — avvia subito una scansione delle cartelle, mette in pausa o riprende l’indicizzazione, esporta l’elenco dei risultati in CSV, apre le Impostazioni, blocca la cassaforte (se attiva) o chiude completamente applicazione e agente."),
                        tr("Strumenti — raccoglie le funzioni operative sull’archivio: Cronologia e ricerche salvate, Scadenze documenti, Mappa dei dati personali (Audit), Oscura copia del documento, Confronta due versioni selezionate, Documenti duplicati, Dimentica una cartella e Stato e diagnostica dell’archivio."),
                        tr("Aiuto — apre questa Guida completa (F1), salta direttamente alla Sintassi rapida di ricerca o mostra le Informazioni e licenze di StillFind."),
                        tr("Intestazione e Lucchetto — sotto la barra menu sono sempre visibili la dicitura OFFLINE e il pulsante del lucchetto (NON CIFRATO oppure CIFRATO): un clic mostra lo stato dell’archivio, permette di attivare la cifratura oppure di consultare e nascondere la password."),
                    ),
                },
                {
                    "kind": "section",
                    "title": tr("Fascia di ricerca, Elenco risultati e Badge Embedding"),
                    "body": (
                        tr("La fascia superiore ospita il campo di ricerca istantanea e quattro menu rapidi (Tipo, Data, "
                        "Cartella, Cerca in) per filtrare senza dover ricordare alcuna sintassi. Sotto, l’area centrale "
                        "è separata da un divisore verticale trascinabile col mouse.")
                    ),
                    "bullets": (
                        tr("Icona dello stato di Embedding — all’inizio di ogni risultato (e accanto al titolo nell’anteprima) compare un pittogramma a quattro nodi con suggerimento a comparsa: verde pieno = vettori del significato pronti e allineati al file; ambra = documento in coda o modificato da ricalcolare; rosso = file privo di testo leggibile; grigio = ricerca per significato disattivata."),
                        tr("Ordinamento e occorrenze multiple — in testa ai risultati puoi ordinare per pertinenza, data di modifica, data interna del documento o nome (A-Z). Sui documenti trovati per parole puoi cliccare «+ Altre occorrenze» per espandere tutti i punti e saltare direttamente alla riga desiderata."),
                        tr("Selezione multipla — usa clic per un singolo documento, Maiusc+clic per un intervallo o {mod}+clic per selezionare due file e confrontarne le versioni dal menu contestuale (tasto destro) o da Strumenti.", mod=MOD),
                    ),
                },
                {
                    "kind": "figure",
                    "asset": "06-ricerca-filtri-risultati",
                    "caption": tr("Figura 1.2 — Elenco dei risultati con icone di stato embedding, occorrenze espanse e pannello Anteprima affiancato con navigazione tra le corrispondenze."),
                },
                {
                    "kind": "section",
                    "title": tr("Pannello Anteprima e Piè di pagina (Percentuale e Modelli)"),
                    "body": (
                        tr("Il pannello di destra mostra subito il contenuto del documento selezionato senza dover aprire "
                        "programmi esterni, mentre il piè di pagina tiene sotto controllo l’avanzamento e i motori locali.")
                    ),
                    "bullets": (
                        tr("Azioni rapide dell’anteprima — quattro pulsanti icona in alto consentono di aprire il file col programma predefinito, mostrarlo nella cartella del sistema, copiare il testo selezionato (o l’intero documento) e creare una copia oscurata."),
                        tr("Tre viste dell’anteprima — «Occorrenze» mostra i brani rilevanti con numero di pagina PDF, foglio Excel o slide PowerPoint; «Testo completo» mostra l’intero documento indicando per ogni pagina se proviene da testo digitale o OCR; «Collegati» elenca entità, pratica cronologica, versioni e documenti simili. Con < e > (o F3 / Shift+F3) scorri tra le parole evidenziate."),
                        tr("Percentuale di indicizzazione — nel footer una barra rettangolare con percentuale in grassetto (es. «Indicizzazione: 64%», «Embedding: 42%» o «Indicizzato: 100%») indica in ogni istante a che punto è il lavoro, affiancata dal tasto «■ Ferma» durante l’elaborazione."),
                        tr("Riquadro Modelli attivi — nell’angolo inferiore destro sono sempre indicati il modello per il significato (es. multilingual-e5-small ONNX int8 o spento) e lo stato dell’OCR (es. Tesseract ita+eng o spento; le lingue sono indicate con i codici Tesseract); fermando il cursore compaiono i dettagli tecnici e di verifica SHA-256."),
                    ),
                },
            ),
        },
        {
            "id": "settings",
            "nav": tr("2. Impostazioni e configurazione"),
            "title": tr("2. Come configurare StillFind (Impostazioni a schede)"),
            "lead": tr(
                "Aprendo File → Impostazioni… ({mod}+,) accedi a cinque schede tematiche indipendenti. Puoi passare "
                "da una scheda all’altra con il mouse, con le frecce o con Ctrl+Tab; tutte le modifiche vengono "
                "applicate insieme quando premi «Salva» (mentre «Annulla» ripristina anche l’anteprima del tema).",
                mod=MOD),
            "blocks": (
                {
                    "kind": "figure",
                    "asset": "03-impostazioni-cartelle",
                    "caption": tr("Figura 2.1 — Finestra Impostazioni divisa nelle cinque schede tematiche: dettaglio della scheda «Cartelle e File»."),
                },
                {
                    "kind": "section",
                    "title": tr("Scheda 1 · Cartelle e File"),
                    "body": tr("Definisce quali percorsi del computer o di dischi esterni devono far parte dell’indice locale:"),
                    "bullets": (
                        tr("Cartelle indicizzate — aggiungi una o più cartelle con «Aggiungi cartella…», attiva o togli «Anche sottocartelle» per ciascuna riga o rimuovila con «✕»."),
                        tr("Cartelle da escludere — elenco di nomi separati da virgola (es. node_modules, Temp, Cache) che vengono ignorati ovunque si trovino."),
                        tr("Tipi di documento — spunta le famiglie di formati da leggere: Documenti di testo (txt, md, rtf, odt, docx, tex, log), PDF, Fogli di calcolo (xlsx, ods, csv), Presentazioni (pptx, odp), Email (.eml inclusi allegati), Archivi e libri (.zip, .epub), Pagine web e dati (html, xml/FatturaPA, json) e File firmati (.p7m)."),
                        tr("Limiti, dischi scollegati e percorsi dimenticati — imposta la dimensione massima per file (MB) e i giorni di conservazione dei dischi scollegati (con 0 i documenti restano cercabili anche a disco staccato). Da «Percorsi dimenticati» puoi riabilitare cartelle o file precedentemente esclusi."),
                    ),
                },
                {
                    "kind": "section",
                    "title": tr("Scheda 2 · Lettura e OCR"),
                    "body": (
                        tr("Controlla l’estrazione del testo dalle pagine scansionate dei PDF e dai file immagine "
                        "(PNG, JPG, TIFF, BMP), oltre al comportamento dell’indicizzatore automatico:")
                    ),
                    "bullets": (
                        tr("OCR pronto all’uso — nel pacchetto distribuito di StillFind il motore Tesseract e i dizionari di italiano, inglese, francese, spagnolo, tedesco e portoghese sono già incorporati: basta attivare l’interruttore «Leggi il testo dai PDF scansionati e dalle immagini» senza installare nulla dall’esterno."),
                        tr("Lingue e limite pagine — in Impostazioni → Lettura e OCR spunti le lingue dei documenti (Italiano, Inglese, Francese, Spagnolo, Tedesco, Portoghese; attivarne meno rende la lettura più rapida) e indichi il numero massimo di pagine scansionate da sottoporre a OCR per singolo PDF, evitando attese eccessive su volumi molto grandi. Il testo OCR viene salvato pagina per pagina nell’indice."),
                        tr("Cambio delle lingue OCR — se cambi le lingue e hai già documenti letti con OCR, al salvataggio scegli fra «Solo prossime letture» (il testo già letto resta invariato) e «Rielabora documenti esistenti» (rilegge soltanto immagini e pagine scansionate, anche negli allegati). Durante la rielaborazione i risultati precedenti restano ricercabili e vengono sostituiti documento per documento solo a lettura riuscita; se la interrompi o va in errore non perdi nulla e riprende da sola."),
                        tr("Scansione automatica e batteria — regola ogni quanti secondi controllare file nuovi, modificati o spostati (0 per disattivare il controllo periodico) e scegli se sospendere automaticamente la lettura quando il portatile funziona a batteria."),
                    ),
                },
                {
                    "kind": "figure",
                    "asset": "04-impostazioni-ocr-significato",
                    "caption": tr("Figura 2.2 — Configurazione della scheda «Ricerca e Significato»: attivazione del modello locale multilingual-e5-small, conteggio passaggi, entità e protocolli."),
                },
                {
                    "kind": "section",
                    "title": tr("Scheda 3 · Ricerca e Significato"),
                    "body": (
                        tr("Attiva la comprensione per concetto e le regole che costruiscono il grafo delle pratiche:")
                    ),
                    "bullets": (
                        tr("Cerca anche per significato — quando attivo, il lavoratore in background calcola i vettori semantici (embeddings a 384 dimensioni) con il modello locale multilingual-e5-small (ONNX quantizzato int8 su CPU). Permette di trovare ad esempio «ripartizione caldaia» cercando «spese riscaldamento», e abilita la sezione «Documenti simili» nella vista Collegati."),
                        tr("Entità da riconoscere e collegare — scegli quali elementi estrarre e validare nel testo: Codici fiscali, Partite IVA, IBAN, Protocolli e pratiche, Email e PEC, Cedenti e cessionari (FatturaPA) e Conversazioni email."),
                        tr("Sigle protocolli e avvisi scadenze — personalizza i prefissi riconosciuti come pratiche (es. PR, PROT, PRATICA, PROTOCOLLO) e scegli se mostrare nel footer il pulsante di avviso per i documenti con scadenze entro 30 giorni."),
                    ),
                },
                {
                    "kind": "section",
                    "title": tr("Schede 4 e 5 · Sicurezza e Privacy / Aspetto e Sistema"),
                    "body": tr("Le ultime due schede governano la protezione crittografica dell’indice e l’integrazione col sistema operativo:"),
                    "bullets": (
                        tr("Sicurezza e Privacy — mostra il percorso dell’archivio (index.db), il pulsante «Salva e configura cifratura…» per passare tra In chiaro, Portachiavi dell’account e Cassaforte con passphrase, e il pannello immediato per nascondere o rivisualizzare la password del lucchetto."),
                        tr("Aspetto e Sistema — permette di scegliere tra tema di Sistema, Chiaro o Scuro con anteprima in tempo reale e di abilitare la scorciatoia globale ({keys}) per richiamare StillFind sopra qualsiasi applicazione.", keys=AppSignals.LABEL),
                    ),
                },
                {
                    "kind": "figure",
                    "asset": "05-impostazioni-sicurezza",
                    "caption": tr("Figura 2.3 — Scheda «Sicurezza e Privacy» con stato di protezione SQLCipher e controlli immediati sulla visibilità della password."),
                },
            ),
        },
        {
            "id": "search",
            "nav": tr("3. Come cercare e filtri"),
            "title": tr("3. Come cercare (dalla ricerca semplice ai filtri avanzati)"),
            "lead": (
                tr("Nella maggior parte dei casi basta scrivere due o tre parole: StillFind ignora maiuscole e accenti, "
                "riconosce singolari, plurali e radici italiane, dà priorità ai documenti in cui i termini compaiono "
                "nello stesso paragrafo o nella stessa pagina e propone «Intendevi…?» in caso di errore di battitura.")
            ),
            "blocks": (
                {
                    "kind": "examples",
                    "title": tr("Tabella rapida della sintassi di ricerca"),
                    "items": examples(),
                },
                {
                    "kind": "section",
                    "title": tr("Combinare menu a discesa e filtri scritti"),
                    "body": (
                        tr("I quattro menu sotto la barra di ricerca (Tipo, Data, Cartella, Cerca in) possono essere "
                        "usati da soli — anche con casella di ricerca vuota per sfogliare tutti i PDF dell’ultimo mese — "
                        "oppure combinati liberamente con gli operatori della tabella qui sopra.")
                    ),
                    "bullets": (
                        tr("Scegli le date… — nel menu Data puoi impostare un intervallo preciso scrivendo una data completa (31/12/2025), un intero mese (12/2025) oppure un anno intero (2025), anche compilando solo «Dal» o solo «Al»."),
                        tr("Cerca in — scegli «Nome e contenuto» per la ricerca completa, «Solo nel contenuto» per escludere i nomi dei file, oppure «Solo nel nome» per una ricerca istantanea sui titoli dei file."),
                        tr("Numeri e codici composti — codici come PR-00124/2026 o importi come 1.250,00 vengono cercati automaticamente come sequenza ordinata, mentre i numeri puri (es. 123) richiedono corrispondenza esatta salvo aggiunta di asterisco (123*)."),
                    ),
                },
                {
                    "kind": "callout",
                    "title": tr("Come lavora la ricerca ibrida (Parole + Significato)"),
                    "body": (
                        tr("Quando «Cerca anche per significato» è attivo e cerchi parole normali ordinate per «Più pertinenti», "
                        "StillFind esegue sia la ricerca lessicale esatta sia quella vettoriale e unisce le classifiche "
                        "tramite Reciprocal Rank Fusion (RRF) dando peso doppio alle parole esatte: i documenti che "
                        "contengono davvero i termini cercati restano sempre in cima, seguiti dai passaggi affini per "
                        "concetto contrassegnati dal simbolo «≈». Se invece usi virgolette, operatori booleani (AND, OR, NOT) "
                        "o cerchi solo nel nome, viene usata rigorosamente la corrispondenza testuale.")
                    ),
                },
                {
                    "kind": "section",
                    "title": tr("Cronologia, Ricerche salvate ed Esportazione CSV"),
                    "body": (
                        tr("Da Strumenti → Cronologia e ricerche salvate… puoi riaprire con doppio clic una delle ultime "
                        "60 ricerche effettuate (testo + filtri scelti), salvare le ricerche ricorrenti dando loro un "
                        "nome, cancellare la cronologia o disattivare la memorizzazione. Da File → Esporta risultati in CSV… "
                        "salvi l’elenco attuale in un file leggibile con Excel o LibreOffice Calc.")
                    ),
                },
            ),
        },
        {
            "id": "relations",
            "nav": tr("4. Collegamenti, versioni e scadenze"),
            "title": tr("4. Collegamenti, Pratiche, Versioni e Scadenze"),
            "lead": (
                tr("Oltre a trovare parole singole, StillFind analizza la struttura dei documenti amministrativi, "
                "fiscali e legali per ricostruire automaticamente i legami tra file sparsi in cartelle diverse.")
            ),
            "blocks": (
                {
                    "kind": "section",
                    "title": tr("Buste firmate (.p7m), FatturaPA (.xml) ed Email (.eml)"),
                    "body": tr("Molti documenti professionali non sono semplici PDF o file Word: StillFind li apre e indicizza nativamente:"),
                    "bullets": (
                        tr("File firmati .p7m (CAdES / PKCS#7) — estrae automaticamente il documento contenuto anche in presenza di firme multiple annidate (es. contratto.pdf.p7m.p7m o fattura.xml.p7m), calcolandone l’impronta SHA-256 per collegarlo all’originale non firmato."),
                        tr("FatturaPA elettronica (.xml e .xml.p7m) — trasforma l’XML fiscale in un testo chiaro e leggibile con Cedente, Cessionario, Partite IVA, Codici Fiscali, numero e data documento, dettaglio righe, riepiloghi IVA, importi, scadenze di pagamento e IBAN."),
                        tr("Messaggi email (.eml) e Archivi (.zip) — legge intestazioni (Da, A, Cc, Oggetto, Data), corpo del messaggio, allegati supportati e i documenti contenuti al primo livello degli archivi .zip."),
                    ),
                },
                {
                    "kind": "figure",
                    "asset": "07-collegamenti-confronto",
                    "caption": tr("Figura 4.1 — Confronto affiancato tra due versioni di un documento con evidenziazione automatica delle parole e degli importi cambiati."),
                },
                {
                    "kind": "section",
                    "title": tr("Vista «Collegati» e Cronologia della pratica"),
                    "body": (
                        tr("Selezionando «Collegati» nel pannello Anteprima visualizzi un albero diviso in quattro gruppi: "
                        "Entità trovate nel documento, Documenti collegati (stesso CF, P.IVA, IBAN, protocollo, copia "
                        "firmata/originale o risposta email), Versioni del testo e Documenti simili per significato. "
                        "Un doppio clic su un’entità apre la scheda cronologica completa della pratica, ordinata per data "
                        "interna del documento (indicando sempre se la data proviene da FatturaPA, testo, intestazione o modifica file).")
                    ),
                },
                {
                    "kind": "section",
                    "title": tr("Confronto versioni tra formati diversi e Scadenzario"),
                    "body": tr("Anche senza usare sistemi di controllo versione, puoi verificare cosa è cambiato tra una bozza e il documento finale:"),
                    "bullets": (
                        tr("Confronta versioni — grazie a impronte testuali su gruppi di tre parole, StillFind individua da solo le stesure dello stesso testo anche tra formati diversi (ad esempio bozza .docx ↔ scansione PDF letta con OCR ↔ copia firmata .p7m). Puoi confrontare anche due risultati qualsiasi selezionandoli nell’elenco e scegliendo «Confronta versioni»."),
                        tr("Scadenze documenti — le date accompagnate da indicatori di termine («scade il», «entro», «rinnovo», «disdetta», «valido fino» o DataScadenzaPagamento in FatturaPA) vengono raccolte in Strumenti → Scadenze documenti…, filtrabili tra Prossimi 30 giorni, Future e Tutte."),
                        tr("Duplicati e Diagnostica — da Strumenti puoi individuare copie identiche al byte (SHA-256) per recuperare spazio senza cancellazioni automatiche, oppure aprire Stato e diagnostica dell’archivio per controllare file illeggibili, PDF privi di testo e forzare la rilettura o l’OCR di un singolo file."),
                    ),
                },
            ),
        },
        {
            "id": "security",
            "nav": tr("5. Sicurezza, cifratura e oscuramento"),
            "title": tr("5. Sicurezza, Cifratura, Audit e Oscuramento"),
            "lead": (
                tr("StillFind è progettato per trattare fascicoli riservati sul tuo computer senza che alcun dato possa "
                "uscire in rete, con cifratura opzionale dell’intero indice e strumenti per verificare e oscurare dati personali.")
            ),
            "blocks": (
                {
                    "kind": "figure",
                    "asset": "02-lucchetto-cifratura",
                    "caption": tr("Figura 5.1 — Pannello del Lucchetto nell’intestazione: verifica dello stato di cifratura, consultazione protetta e oscuramento della password."),
                },
                {
                    "kind": "section",
                    "title": tr("Cifratura dell’archivio (SQLCipher) e Lucchetto in Header"),
                    "body": (
                        tr("Di default l’indice nasce in chiaro, ma dal pulsante del lucchetto in alto a destra (o da "
                        "Impostazioni → Sicurezza e Privacy) puoi attivare in qualsiasi momento la cifratura integrale "
                        "SQLCipher, che cifra testo estratto, entità, ricerche salvate e persino i vettori semantici "
                        "(i tuoi documenti originali nelle cartelle non vengono mai toccati):")
                    ),
                    "bullets": (
                        tr("Portachiavi dell’account — StillFind genera una chiave casuale a 256 bit (64 caratteri esadecimali) e la custodisce nel portachiavi del sistema operativo (Keychain su macOS, DPAPI su Windows, Secret Service su Linux). Non richiede inserimento all’avvio e permette all’agente di continuare a indicizzare in background."),
                        tr("Cassaforte con passphrase — scegli tu una passphrase personale. Nessuna chiave viene salvata su disco: resta solo in RAM durante la sessione e la cassaforte si blocca automaticamente dopo i minuti di inattività impostati (oppure subito con File → Blocca cassaforte)."),
                        tr("Visibilità della password sul lucchetto — quando l’archivio è cifrato, cliccando sul pulsante «CIFRATO» puoi leggere o copiare la passphrase/chiave attiva. Premendo «Rendi invisibile la password…» (con avviso di annotarla prima), il segreto viene rimosso dalla vista e sostituito da pallini: da quel momento, per rivisualizzarlo o riabilitarne la visibilità sarà obbligatorio reinserire la password attuale."),
                    ),
                },
                {
                    "kind": "figure",
                    "asset": "08-audit-oscuramento",
                    "caption": tr("Figura 5.2 — Strumenti per la riservatezza: Mappa dei dati personali (Audit) e preparazione di una copia oscurata pagina per pagina."),
                },
                {
                    "kind": "section",
                    "title": tr("Mappa dei dati personali (Audit), Oscuramento reale e Rimozione selettiva"),
                    "body": tr("Tre strumenti integrati aiutano a gestire la riservatezza dei fascicoli prima di condividerli o conservarli:"),
                    "bullets": (
                        tr("Mappa dei dati personali (Audit) — da Strumenti → Mappa dei dati personali… individui tutti i documenti che contengono un codice fiscale, P.IVA, IBAN, email o il nome di una persona, oppure controlli in un clic le cartelle più esposte come Desktop e Download."),
                        tr("Oscura copia… (Redazione irreversibile) — crea un nuovo file oscurato lasciando intatto l’originale. Sui file di testo sostituisce i dati scelti con [OMISSIS]; sui PDF rileva i dati sia nel testo digitale sia nelle immagini (tramite OCR), ti fa controllare e integrare a mano col mouse i riquadri neri su ciascuna pagina e salva un nuovo PDF composto unicamente dalle immagini approvate, eliminando testo copiabile sottostante e metadati."),
                        tr("Dimentica file o cartella — clic destro su un risultato (o Strumenti → Dimentica una cartella…, anche su dischi esterni scollegati) per rimuovere dall’indice testo, passaggi, vettori ed entità senza cancellare i file dal disco."),
                    ),
                },
                {
                    "kind": "callout",
                    "title": tr("Garanzia tecnica 100% Offline verificabile"),
                    "body": (
                        tr("StillFind blocca i moduli di rete di Python all’avvio, li escludere fisicamente dal pacchetto "
                        "compilato (verificandolo in build.py), avvia il processo lavoratore di indicizzazione/OCR/ONNX "
                        "dentro un recinto del sistema operativo privo di rete (sandbox-exec su macOS, unshare/bwrap su "
                        "Linux, verifica firewall su Windows) e controlla l’impronta SHA-256 e la struttura interna del "
                        "modello ONNX prima di caricarlo. Puoi verificarlo in ogni momento da terminale con: "
                        "stillfind --self-test-offline.")
                    ),
                },
            ),
        },
    )


class GuideFigure(ctk.CTkFrame):
    """Riquadro illustrativo a spigolo vivo: carica lo screenshot locale (chiaro/scuro) e si adatta alla larghezza."""

    MAX_WIDTH = 820

    def __init__(self, master, asset: str, caption: str):
        super().__init__(master, corner_radius=0, fg_color=PAL["field"], border_width=1, border_color=PAL["line"])
        self.asset = asset
        self.caption_text = caption
        self._light_src, self._dark_src = self._load_sources(asset)
        self._rendered_width = 0
        self._ctk_image: ctk.CTkImage | None = None
        self.grid_columnconfigure(0, weight=1)
        self.image_label: ctk.CTkLabel | None = None
        if self._light_src is not None:
            self.image_label = ctk.CTkLabel(self, text="", corner_radius=0, fg_color=PAL["field"])
            self.image_label.grid(row=0, column=0, padx=10, pady=(10, 6))
        self.caption = ctk.CTkLabel(
            self,
            text=caption,
            font=font(11),
            text_color=PAL["muted"],
            anchor="w",
            justify="left",
            wraplength=680,
            corner_radius=0,
        )
        self.caption.grid(row=1, column=0, sticky="ew", padx=12, pady=(4 if self._light_src else 10, 10))

    @staticmethod
    def _load_sources(asset: str) -> tuple[Image.Image | None, Image.Image | None]:
        folder = guide_assets_dir()
        light_path = folder / f"{asset}-light.png"
        dark_path = folder / f"{asset}-dark.png"
        single_path = folder / f"{asset}.png"
        try:
            if light_path.is_file() and dark_path.is_file():
                with Image.open(light_path) as li, Image.open(dark_path) as di:
                    return li.convert("RGB"), di.convert("RGB")
            if single_path.is_file():
                with Image.open(single_path) as im:
                    rgb = im.convert("RGB")
                    return rgb, rgb
            if light_path.is_file():
                with Image.open(light_path) as li:
                    rgb = li.convert("RGB")
                    return rgb, rgb
        except OSError:
            return None, None
        return None, None

    @property
    def has_image(self) -> bool:
        return self._light_src is not None

    def fit_width(self, available: int) -> None:
        inner = max(260, available - 28)
        self.caption.configure(wraplength=inner)
        if self._light_src is None or self.image_label is None:
            return
        target_w = max(240, min(self.MAX_WIDTH, inner, self._light_src.width))
        if abs(target_w - self._rendered_width) < 12:
            return
        self._rendered_width = target_w
        src_w, src_h = self._light_src.size
        target_h = max(1, round(src_h * (target_w / max(1, src_w))))
        self._ctk_image = ctk.CTkImage(
            light_image=self._light_src,
            dark_image=self._dark_src or self._light_src,
            size=(target_w, target_h),
        )
        self.image_label.configure(image=self._ctk_image)


class _NavButton(ctk.CTkButton):
    """Pulsante capitolo nella barra laterale: mantiene il focus sulla cornice per frecce, Invio e Spazio."""

    focus = tk.Misc.focus
    focus_set = tk.Misc.focus_set
    focus_force = tk.Misc.focus_force


class _Lazy:
    """Attributo di classe calcolato a ogni accesso (i testi dipendono dalla lingua corrente)."""

    def __init__(self, factory):
        self.factory = factory

    def __get__(self, obj, owner):
        return self.factory()


class HelpDialog(SquareDialog):
    """Manuale completo interattivo diviso in cinque capitoli con esempi e illustrazioni locali."""

    # Accessibili anche dalla classe (verifiche e pacchetto); ogni finestra se ne costruisce una copia nella lingua corrente.
    EXAMPLES = _Lazy(examples)
    CHAPTERS = _Lazy(_chapters)

    def __init__(self, parent, chapter: str | None = None):
        super().__init__(parent, tr("Guida completa di StillFind"), "1080x740", minsize=(860, 560), resizable=True, modal=False)
        self.CHAPTERS = _chapters()
        self._chapters_by_id = {c["id"]: i for i, c in enumerate(self.CHAPTERS)}
        self.current_index = 0
        self._wrap_targets: list[tuple[ctk.CTkLabel, int]] = []
        self._figures: list[GuideFigure] = []
        self._resize_job: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- fascia superiore
        top = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent", border_width=0)
        top.grid(row=0, column=0, sticky="ew", padx=self.MARGIN, pady=(self.MARGIN, 8))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=tr("Guida di StillFind"), font=font(20, True), text_color=PAL["text"],
                     anchor="w", corner_radius=0).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(top, text=tr("Manuale completo di interfaccia, configurazione, ricerca e sicurezza · 100% locale"),
                     font=font(12), text_color=PAL["muted"], anchor="w", corner_radius=0).grid(row=1, column=0, sticky="w")

        # --- corpo a due colonne: indice capitoli a sinistra, lettura scorrevole a destra
        workspace = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent", border_width=0)
        workspace.grid(row=1, column=0, sticky="nsew", padx=self.MARGIN)
        workspace.grid_columnconfigure(1, weight=1)
        workspace.grid_rowconfigure(0, weight=1)

        self.sidebar = card(workspace, width=258)
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(len(self.CHAPTERS) + 2, weight=1)

        ctk.CTkLabel(self.sidebar, text=tr("CAPITOLI"), font=font(11, True), text_color=PAL["muted"],
                     anchor="w", corner_radius=0).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkFrame(self.sidebar, height=1, corner_radius=0, border_width=0, fg_color=PAL["line"]).grid(
            row=1, column=0, sticky="ew", padx=1)

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for idx, chap in enumerate(self.CHAPTERS):
            cid = chap["id"]
            btn = _NavButton(
                self.sidebar,
                text=chap["nav"],
                anchor="w",
                height=38,
                corner_radius=0,
                border_width=1,
                border_color=PAL["card"],
                fg_color="transparent",
                hover_color=PAL["ghost_hover"],
                text_color=PAL["text"],
                font=font(13),
                command=lambda key=cid: self.select_chapter(key),
            )
            btn.grid(row=idx + 2, column=0, sticky="ew", padx=6, pady=(6 if idx == 0 else 2, 0))
            tk.Frame.configure(btn, takefocus=True)
            tk.Misc.bind(btn, "<FocusIn>", lambda e, b=btn, key=cid: b.configure(
                border_color=PAL["focus"] if key != self.current_chapter_id else PAL["accent"]))
            tk.Misc.bind(btn, "<FocusOut>", lambda e, b=btn, key=cid: b.configure(
                border_color=PAL["accent"] if key == self.current_chapter_id else PAL["card"]))
            tk.Misc.bind(btn, "<Return>", lambda e, key=cid: (self.select_chapter(key), "break")[1])
            tk.Misc.bind(btn, "<space>", lambda e, key=cid: (self.select_chapter(key), "break")[1])
            tk.Misc.bind(btn, "<Up>", lambda e, i=idx: self._focus_nav(i - 1))
            tk.Misc.bind(btn, "<Down>", lambda e, i=idx: self._focus_nav(i + 1))
            self.nav_buttons[cid] = btn

        # riquadro scorciatoie in fondo alla barra laterale
        keys_box = ctk.CTkFrame(self.sidebar, corner_radius=0, fg_color=PAL["field"], border_width=1, border_color=PAL["line"])
        keys_box.grid(row=len(self.CHAPTERS) + 3, column=0, sticky="ew", padx=8, pady=8)
        keys_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(keys_box, text=tr("TASTI RAPIDI"), font=font(10, True), text_color=PAL["muted"],
                     anchor="w", corner_radius=0).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))
        for r_i, (combo, desc) in enumerate(shortcuts(), start=1):
            ctk.CTkLabel(keys_box, text=combo, font=ctk.CTkFont(family=MONO_FAMILY, size=10, weight="bold"),
                         text_color=PAL["accent"], anchor="w", corner_radius=0).grid(
                row=r_i, column=0, sticky="w", padx=(8, 6), pady=(0, 4 if r_i == len(shortcuts()) else 2))
            ctk.CTkLabel(keys_box, text=desc, font=font(10), text_color=PAL["text"], anchor="w",
                         wraplength=130, justify="left", corner_radius=0).grid(
                row=r_i, column=1, sticky="w", padx=(0, 6), pady=(0, 4 if r_i == len(shortcuts()) else 2))

        # area di lettura a destra
        self.reader = ctk.CTkScrollableFrame(
            workspace,
            corner_radius=0,
            border_width=1,
            border_color=PAL["line"],
            fg_color=PAL["card"],
        )
        self.reader.grid(row=0, column=1, sticky="nsew")
        self.reader.grid_columnconfigure(0, weight=1)
        self.reader._parent_canvas.bind("<Configure>", self._schedule_reflow, add="+")

        # --- barra inferiore di navigazione capitoli
        bottom = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent", border_width=0)
        bottom.grid(row=2, column=0, sticky="ew", padx=self.MARGIN, pady=self.MARGIN)
        bottom.grid_columnconfigure(0, weight=1)
        self.pos_label = ctk.CTkLabel(bottom, text="", font=font(12), text_color=PAL["muted"],
                                      anchor="w", corner_radius=0)
        self.pos_label.grid(row=0, column=0, sticky="w")
        self.prev_button = ghost_button(bottom, tr("< Capitolo precedente"), lambda: self.step_chapter(-1), width=176)
        self.prev_button.grid(row=0, column=1, padx=(0, 8))
        self.next_button = ghost_button(bottom, tr("Capitolo successivo >"), lambda: self.step_chapter(1), width=176)
        self.next_button.grid(row=0, column=2, padx=(0, 8))
        self.close_button = primary_button(bottom, tr("Chiudi"), self.close, width=100)
        self.close_button.grid(row=0, column=3)

        self.bind("<Prior>", lambda e: self._scroll_page(-1))
        self.bind("<Next>", lambda e: self._scroll_page(1))
        for seq in ("<Alt-Left>", "<Option-Left>"):
            try:
                self.bind(seq, lambda e: (self.step_chapter(-1), "break")[1])
            except tk.TclError:
                pass
        for seq in ("<Alt-Right>", "<Option-Right>"):
            try:
                self.bind(seq, lambda e: (self.step_chapter(1), "break")[1])
            except tk.TclError:
                pass

        self.select_chapter(chapter or self.CHAPTERS[0]["id"])

    @property
    def current_chapter_id(self) -> str:
        return self.CHAPTERS[self.current_index]["id"]

    def _focus_nav(self, index: int):
        target = index % len(self.CHAPTERS)
        cid = self.CHAPTERS[target]["id"]
        self.select_chapter(cid)
        self.nav_buttons[cid].focus_set()
        return "break"

    def _scroll_page(self, direction: int):
        self.reader._parent_canvas.yview_scroll(direction, "pages")
        return "break"

    def step_chapter(self, delta: int) -> None:
        target = max(0, min(len(self.CHAPTERS) - 1, self.current_index + delta))
        if target != self.current_index:
            self.select_chapter(self.CHAPTERS[target]["id"])

    def select_chapter(self, chapter_id: str) -> None:
        if chapter_id not in self._chapters_by_id:
            chapter_id = self.CHAPTERS[0]["id"]
        self.current_index = self._chapters_by_id[chapter_id]
        self._update_nav_styles()
        self._render_chapter(self.CHAPTERS[self.current_index])
        self.reader._parent_canvas.yview_moveto(0)
        self.pos_label.configure(
            text=tr("Capitolo {i} di {n}  ·  {title}", i=self.current_index + 1, n=len(self.CHAPTERS),
                    title=self.CHAPTERS[self.current_index]["title"])
        )
        self.prev_button.configure(state="normal" if self.current_index > 0 else "disabled")
        self.next_button.configure(state="normal" if self.current_index + 1 < len(self.CHAPTERS) else "disabled")

    def _update_nav_styles(self) -> None:
        active_id = self.current_chapter_id
        for cid, btn in self.nav_buttons.items():
            selected = cid == active_id
            btn.configure(
                fg_color=PAL["sel"] if selected else "transparent",
                border_color=PAL["accent"] if selected else PAL["card"],
                font=font(13, selected),
            )

    def restyle(self) -> None:
        self._update_nav_styles()
        self._reflow()

    def _clear_reader(self) -> None:
        self._wrap_targets.clear()
        self._figures.clear()
        for child in self.reader.winfo_children():
            child.destroy()

    def _add_wrapped_label(self, parent, text: str, *, fnt, color, pad_x=18, pad_y=(0, 8), subtract=44) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(parent, text=text, font=fnt, text_color=color, anchor="w", justify="left",
                           wraplength=680, corner_radius=0)
        lbl.pack(fill="x", padx=pad_x, pady=pad_y)
        self._wrap_targets.append((lbl, subtract))
        return lbl

    def _render_chapter(self, chap: dict) -> None:
        self._clear_reader()
        self._add_wrapped_label(self.reader, chap["title"], fnt=font(19, True), color=PAL["text"], pad_y=(16, 6))
        self._add_wrapped_label(self.reader, chap["lead"], fnt=font(13), color=PAL["muted"], pad_y=(0, 12))
        ctk.CTkFrame(self.reader, height=1, corner_radius=0, border_width=0, fg_color=PAL["line"]).pack(
            fill="x", padx=18, pady=(0, 10))

        for block in chap["blocks"]:
            kind = block["kind"]
            if kind == "section":
                self._add_wrapped_label(self.reader, block["title"], fnt=font(15, True), color=PAL["text"], pad_y=(10, 4))
                if block.get("body"):
                    self._add_wrapped_label(self.reader, block["body"], fnt=font(13), color=PAL["text"], pad_y=(0, 6))
                for item in block.get("bullets", ()):
                    row = ctk.CTkFrame(self.reader, corner_radius=0, fg_color="transparent", border_width=0)
                    row.pack(fill="x", padx=22, pady=3)
                    row.grid_columnconfigure(1, weight=1)
                    ctk.CTkLabel(row, text="▪", font=font(13, True), text_color=PAL["accent"], width=16,
                                 anchor="n", corner_radius=0).grid(row=0, column=0, sticky="nw", pady=(1, 0))
                    bullet_lbl = ctk.CTkLabel(row, text=item, font=font(13), text_color=PAL["text"],
                                              anchor="w", justify="left", wraplength=640, corner_radius=0)
                    bullet_lbl.grid(row=0, column=1, sticky="ew")
                    self._wrap_targets.append((bullet_lbl, 72))
            elif kind == "figure":
                fig = GuideFigure(self.reader, block["asset"], block["caption"])
                fig.pack(fill="x", padx=18, pady=(8, 12))
                self._figures.append(fig)
            elif kind == "examples":
                self._add_wrapped_label(self.reader, block["title"], fnt=font(15, True), color=PAL["text"], pad_y=(8, 6))
                table = ctk.CTkFrame(self.reader, corner_radius=0, fg_color=PAL["field"],
                                     border_width=1, border_color=PAL["line"])
                table.pack(fill="x", padx=18, pady=(0, 12))
                table.grid_columnconfigure(1, weight=1)
                mono = ctk.CTkFont(family=MONO_FAMILY, size=12, weight="bold")
                for idx, (syntax, meaning) in enumerate(block["items"]):
                    if idx > 0:
                        ctk.CTkFrame(table, height=1, corner_radius=0, border_width=0, fg_color=PAL["line"]).grid(
                            row=idx * 2 - 1, column=0, columnspan=2, sticky="ew", padx=1)
                    ctk.CTkLabel(table, text=syntax, font=mono, text_color=PAL["accent"], anchor="nw",
                                 justify="left", width=215, corner_radius=0).grid(
                        row=idx * 2, column=0, sticky="nw", padx=(12, 10), pady=7)
                    desc = ctk.CTkLabel(table, text=meaning, font=font(12), text_color=PAL["text"],
                                        anchor="w", justify="left", wraplength=410, corner_radius=0)
                    desc.grid(row=idx * 2, column=1, sticky="ew", padx=(0, 12), pady=7)
                    self._wrap_targets.append((desc, 340))
            elif kind == "callout":
                box = ctk.CTkFrame(self.reader, corner_radius=0, fg_color=PAL["field"],
                                   border_width=1, border_color=PAL["accent"])
                box.pack(fill="x", padx=18, pady=(8, 12))
                self._add_wrapped_label(box, block["title"], fnt=font(13, True), color=PAL["text"],
                                        pad_x=14, pad_y=(10, 4), subtract=76)
                self._add_wrapped_label(box, block["body"], fnt=font(12), color=PAL["text"],
                                        pad_x=14, pad_y=(0, 10), subtract=76)

        self.after_idle(self._reflow)

    def _schedule_reflow(self, _event=None) -> None:
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.after(40, self._reflow)

    def _reflow(self) -> None:
        self._resize_job = None
        if not self.winfo_exists():
            return
        width = self.reader._parent_canvas.winfo_width()
        if width <= 40:
            width = max(560, self.winfo_width() - 310)
        for lbl, subtract in self._wrap_targets:
            if lbl.winfo_exists():
                lbl.configure(wraplength=max(200, width - subtract))
        for fig in self._figures:
            if fig.winfo_exists():
                fig.fit_width(width - 36)

    def close(self) -> None:
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
            self._resize_job = None
        super().close()
