# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ripete la taratura su un indice esistente con i vettori pronti, usando CERCADOC_HOME.
Le domande e i criteri tematici non includono percorsi o estratti dei documenti.
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from itertools import product

from cercadoc import protection, search, semantic
from cercadoc.config import DB_PATH

# Entrambi i criteri devono comparire nel passaggio restituito.
DOMANDE = (
    ("posso dormire con il mio cane", "animali?|can[ei]\\b", "ammess|consent|ospit|accesso"),
    ("portare le carte del veterinario", "vaccin|sanitari", "animale|animali|veterinar"),
    ("chi paga se il cane rovina la stanza", "animale|animali", "danni|responsab"),
    ("posso usare gli asciugamani per il cane", "animale|animali", "biancheria|toelettatura"),
    ("annullare la camera già riservata", "disdett|rinunci|annull", "prenot|soggiorno"),
    ("quanto anticipo devo versare per fermare la stanza", "caparra|acconto", "prenot|soggiorno"),
    ("trasferire i soldi con la banca", "bonifico", "pagamento|versamento"),
    ("posso saldare con la carta", "moneta elettronica|bancomat|pos\\b|carta di credito", "pagamento|pagare|versamento"),
    ("posso lasciare l’auto nella caserma", "parchegg", "auto|veicol|caserma"),
    ("quanto tempo prima posso chiedere una camera", "prenotaz", "prima|anticip|giorni"),
    ("posso accendere una sigaretta nella stanza", "fumare|fumo", "vietat|divieto"),
    ("bambini in vacanza senza i genitori", "minor", "accompagn|genitor"),
    ("a che ora si devono liberare le stanze", "ore|orario", "rilasci|lascia|partenza|check.out"),
    ("dove ritirare le chiavi quando arrivo", "chiav", "ritir|consegn|ricev"),
    ("cosa succede se rompo qualcosa nell’alloggio", "danni|dannegg", "risarc|responsab|addeb|segnala"),
    ("chi cambia le lenzuola della camera", "biancheria|lenzuol", "cambio|sostituz|cambi[ao]"),
    ("posso cucinare nella camera", "cucin|fornell", "vietat|divieto|consent"),
    ("posso ascoltare la televisione ad alto volume", "rumor", "disturb|molest"),
    ("chi pulisce l’alloggio alla partenza", "puliz|pulit", "camera|stanze|alloggi"),
    ("quanto dura al massimo la permanenza", "massim|non superior", "permanenza|soggiorno|pernott"),
    ("a chi spetta per primo l’alloggio", "priorit|precedenz", "personale|assegnaz|prenotaz"),
    ("documenti da presentare al mio arrivo", "identit|riconoscimento", "esibir|document|consegn"),
    ("addebito dello stipendio per la rivista", "rivista|abbonamento", "stipend|delegaz|tratten"),
    ("accesso a colazione e mensa", "colazione|pasti", "bar\\b|mensa|pranzo"),
    ("come vengono trattate le mie informazioni personali", "dati personali", "trattamento|protezione"),
    ("voglio far cancellare i miei dati", "cancellaz|rettifica", "dati personali|trattamento"),
    ("posso far entrare amici nella stanza", "estrane|visitator|non autorizz", "vietat|divieto|accesso"),
    ("devo portare il cane al guinzaglio", "guinzaglio|museruola", "animali|cani|cane|ospite"),
    ("a chi segnalare un guasto", "guast|disservizi|anomal", "segnal|comunic|rifer"),
    ("posso fare una prenotazione per email", "mail|posta elettronica", "prenotaz|richiesta"),
)
CONTROLLI_NEGATIVI = (
    "riparare la caldaia a gas",
    "dichiarazione dei redditi per affitti brevi",
    "configurare un telescopio astronomico",
    "come cuocere la torta di mele",
    "assicurazione sul raccolto di grandine",
    "misurare la pressione dei pneumatici",
)


def osservazioni(con, modello, store):
    risultati = []
    for domanda, *criteri in (*DOMANDE, *((d,) for d in CONTROLLI_NEGATIVI)):
        patterns = [re.compile(c) for c in criteri]
        def pertinente(testo):
            testo = " ".join(testo.lower().split())
            return bool(patterns) and all(p.search(testo) for p in patterns)

        parole = search.search(con, domanda)
        for r in parole:
            r["pertinente"] = pertinente(r["snippet"].replace(search.HIT_START, "").replace(search.HIT_END, ""))
        vicini = semantic.semantic_docs(con, store, modello, domanda, min_score=.80, margin=1.)
        significato = []
        for h in vicini:
            brano = con.execute("SELECT substr(content,?,?) FROM fts WHERE rowid=?",
                                (h["start"] + 1, h["end"] - h["start"], h["doc_id"])).fetchone()[0]
            significato.append(dict(id=h["doc_id"], score=h["score"], pertinente=pertinente(brano)))
        risultati.append((parole, significato, bool(criteri)))
    return risultati


def misura(casi, soglia, scarto):
    riuscite = vuote = solo_parole = 0
    for parole, significato, positiva in casi:
        minimo = max(soglia, significato[0]["score"] - scarto) if significato else soglia
        righe = semantic.fuse(parole, [r for r in significato if r["score"] >= minimo], 5)
        if positiva:
            riuscite += any(r["pertinente"] for r in righe)
            solo_parole += any(r["pertinente"] for r in parole[:5])
        else:
            vuote += not righe
    return {"ibride_nei_primi_cinque": riuscite, "controlli_vuoti": vuote, "parole_nei_primi_cinque": solo_parole}


def main():
    from cercadoc.embedder import Embedder
    if not DB_PATH.is_file():
        raise SystemExit("Archivio assente.")
    # mode=ro impedisce anche la creazione di un database se viene spostato mentre si apre.
    if protection.settings(DB_PATH)["mode"] == "vault":
        from getpass import getpass
        protection.unlock(DB_PATH, getpass("Passphrase CercaDoc: "))
    with closing(protection.connection(DB_PATH, readonly=True)) as con:
        con.execute("BEGIN")
        stato = semantic.status(con)
        if not stato["passages"] or stato["pending"]:
            raise SystemExit("La taratura richiede i vettori di tutti i documenti del campione.")
        casi = osservazioni(con, Embedder(), semantic.open_store(con=con))
    griglia = product((.80, .815, .82, .825, .83, .835, .84, .85, .86), (.015, .025, .04))
    def criterio(coppia):
        m = misura(casi[::2], *coppia)
        return m["ibride_nei_primi_cinque"], m["controlli_vuoti"]
    soglie = max(griglia, key=criterio)
    print(json.dumps({"documenti": stato["docs"], "passaggi": stato["passages"], "soglia_e_scarto": soglie,
                      "taratura": misura(casi[::2], *soglie), "verifica": misura(casi[1::2], *soglie),
                      "totale": misura(casi, *soglie), "soglie_precedenti": misura(casi, .80, .04)},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
