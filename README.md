# vetrina-check

**Quante foto ha davvero il sito di un negozio, e quante servono?** In dieci righe, senza
aprire il sito a mano.

`vetrina-check` legge una pagina pubblica e riporta quello che un visitatore vede:
quante immagini, quante sembrano foto di catalogo invece che del prodotto vero, quante
sono senza descrizione, quanto pesano (il sito che si apre lento perde clienti), se c'è
un video, se i social sono collegati, se la pagina è pronta per il telefono. In fondo
stampa un **gancio**: una frase fatta di fatti, da usare in un'email o in una proposta.

Nato per un lavoro reale: capire in cinque secondi se un'attività locale ha bisogno di
foto nuove — e scriverle dicendo dati, non opinioni.

## Avvio rapido

```bash
python vetrina.py https://esempio.it
python vetrina.py https://esempio.it --json          # per farne uno script
python vetrina.py https://esempio.it --no-measure    # più veloce: salta i pesi
python vetrina.py --selftest                         # prova il parser, senza rete
```

Solo libreria standard di Python 3.9+. Nessuna chiave API, nessun account, nessun dato
mandato a terzi: l'unica connessione è la richiesta HTTP al sito che indichi tu.

## Esempio di uscita

```
vetrina-check — https://www.lollobet.it
  titolo: Previsioni partite di oggi | Pronostici calcio AI | LolloBet
  resa: browser (CDP)
  foto: 16 · con descrizione: 16 · peso stimato: 2.4 MB (16 misurate)
  da catalogo/stock: 0
  probabili foto vere: 0
  video: NO · social: nessuno
  mobile/viewport: ok · https: ok · og:image: ok
  gancio: il sito ha 16 immagini in tutto, nessun video, nessun link a facebook, instagram, tiktok, youtube.
```

## Siti che si disegnano con JavaScript

Molte vetrine (Wix, Squarespace, i siti nuovi fatti con un builder) arrivano al client
con un HTML **senza immagini**: le foto compaiono dopo, in JavaScript. Un controllo
statico lì direbbe "0 foto", che è falso.

Perciò:

- se l'HTML statico non mostra nessuna immagine, lo strumento **ripiega da solo** su un
  browser Chrome già avviato con la porta di debug aperta;
- con `--cdp-endpoint` puoi puntare a un endpoint diverso (predefinito
  `http://127.0.0.1:9222`).

```bash
# avvia una volta Chrome con la porta di debug (profilo dedicato: non tocca il tuo browser)
chrome --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=/tmp/per-check

pip install websockets          # serve solo per la modalità browser
python vetrina.py https://sito-costruito-con-builder.it
```

La scheda aperta dal browser viene chiusa alla fine dell'analisi.

## Cosa guarda, e quanto ci si può fidare

| Riga | Come la misura | Quanto è affidabile |
|---|---|---|
| foto | `<img>` reali + `data-src`/`srcset`, oppure il DOM vivo in modalità browser | alta |
| senza descrizione | `alt` vuoto | alta |
| da catalogo | host/nome file di banca immagini (shutterstock, getty, …) | **indizio, non prova** |
| foto vere | nome file da fotocamera (`IMG_20240912.jpg`) | **indizio, non prova** |
| peso | `Content-Length` in HEAD, sulle prime N immagini | alta, ma solo sulle misurate |
| video, social, og:image, viewport | presenza nell'HTML | alta |

Le due righe "da catalogo" e "foto vere" sono **euristiche sul nome del file**: servono a
decidere cosa guardare per primo, non a dichiarare un fatto. La riga `gancio` infatti usa
solo i numeri solidi.

## Perché è fatto così

- **Zero dipendenze** nel percorso normale: si copia il file e funziona, anche su una
  macchina senza permessi di installazione.
- **Nessun dato a terzi**: chi analizza siti di clienti non deve mandare quegli URL a un
  servizio esterno.
- **Una sola uscita leggibile**: il valore non è il JSON, è la frase-fatto in fondo.

## Chi l'ha scritto

**Alex Ai** — studio di contenuti per attività e piccoli brand: immagini di prodotto
generate e ritoccate partendo dalle **foto vere** del prodotto, reel verticali con voce,
schede prodotto per gli annunci.

- Sito: <https://studiocontenuti.it>
- Email: alex@studiocontenuti.it
- Pacchetto di prova: **35 €** — Abbonamento mensile: **99 €/mese**

Se questo strumento ti ha fatto risparmiare tempo, il passo successivo è la cosa che non
automatizzi con uno script: le foto.

Licenza MIT — vedi [LICENSE](LICENSE).
