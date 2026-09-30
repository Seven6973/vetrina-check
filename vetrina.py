#!/usr/bin/env python3
"""vetrina-check — quanto sono "in vetrina" le foto di un sito?

Un'attivita' locale vende con le immagini: quelle dei suoi prodotti, del locale, del
laboratorio. Questo strumento legge una pagina pubblica e dice, in dieci righe, cosa c'e'
e cosa manca: quante foto, quante sembrano di catalogo, quante senza descrizione, quanto
pesano, se c'e' un video, se i social sono collegati. Serve a capire in 5 secondi se quel
sito ha bisogno di foto nuove — e a scrivere un'email che parla di fatti, non di opinioni.

Uso:
    python vetrina.py https://esempio.it
    python vetrina.py https://esempio.it --json
    python vetrina.py https://esempio.it --cdp            # conta le foto vere su siti JS
    python vetrina.py https://esempio.it --cdp-endpoint http://127.0.0.1:9222

Modalita':
  * default — analisi statica dell'HTML: nessuna dipendenza, nessuna chiave, nessun browser.
  * --cdp   — per i siti che disegnano tutto via JavaScript (Wix, Squarespace, molte
              vetrine nuove) l'HTML arriva vuoto: in quel caso si apre la pagina in un
              browser Chrome gia' avviato con --remote-debugging-port e si contano le
              immagini del DOM reale. Richiede il pacchetto `websockets`, e la scheda
              aperta viene chiusa a fine analisi.

Esce solo la richiesta HTTP al sito che gli dici tu: nessun dato inviato a terzi.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 vetrina-check/1.0")

# Foto che "profumano" di catalogo/stock: host o nome file tipici.
STOCK_HOSTS = ("shutterstock", "gettyimages", "istockphoto", "123rf", "depositphotos",
               "dreamstime", "adobestock", "stock.adobe", "freepik", "envato", "unsplash",
               "pexels", "pixabay", "placehold", "placeholder")
CAMERA_HINTS = re.compile(r"^(img|dsc|dscn|photo|foto|image)[-_ ]?\d{3,}", re.I)
SKIP_EXT = (".svg",)

SOCIALS = {
    "facebook": r"facebook\.com",
    "instagram": r"instagram\.com",
    "tiktok": r"tiktok\.com",
    "youtube": r"(youtube\.com|youtu\.be)",
}
BUILDERS = {
    "wix": r"wix\.com|wixstatic",
    "squarespace": r"squarespace",
    "wordpress": r"wp-content|wp-includes",
    "shopify": r"cdn\.shopify",
    "jimdo": r"jimdo",
    "google-sites": r"sites\.google\.com",
}


class Page(HTMLParser):
    """Raccoglie solo quello che serve: immagini, social, video, meta."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.imgs: list[dict] = []
        self.socials: set[str] = set()
        self.videos = 0
        self.meta: dict[str, str] = {}
        self.title = ""
        self.scripts = 0
        self._in_title = False
        self._hrefs: set[str] = set()

    def _add_img(self, attrs: dict) -> None:
        src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-lazy-src") or ""
        if not src and attrs.get("srcset"):
            src = attrs["srcset"].split(",")[0].strip().split(" ")[0]
        if not src:
            return
        self.imgs.append({
            "src": src,
            "alt": (attrs.get("alt") or "").strip(),
            "lazy": attrs.get("loading") == "lazy",
        })

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "img":
            self._add_img(a)
        elif tag == "script":
            self.scripts += 1
        elif tag in ("video", "iframe"):
            blob = (a.get("src") or "") + (a.get("data-src") or "")
            if tag == "video" or re.search(SOCIALS["youtube"], blob) or "vimeo" in blob:
                self.videos += 1
        elif tag == "source" and (a.get("srcset") or a.get("src")):
            self._add_img({"src": (a.get("srcset") or a.get("src")).split(",")[0].split(" ")[0]})
        elif tag == "a" and a.get("href"):
            self._hrefs.add(a["href"])
        elif tag == "meta":
            key = a.get("property") or a.get("name") or ""
            if key:
                self.meta[key.lower()] = a.get("content", "")
        elif tag == "title":
            self._in_title = True

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def find_socials(self) -> None:
        for href in self._hrefs:
            for name, pat in SOCIALS.items():
                if re.search(pat, href, re.I):
                    self.socials.add(name)


def fetch(url: str, timeout: int = 20) -> tuple[str, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw, headers = r.read(), dict(r.headers)
    if (headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.decompress(raw)
    m = re.search(r"charset=([\w-]+)", headers.get("Content-Type") or "", re.I)
    return raw.decode(m.group(1) if m else "utf-8", errors="replace"), headers


def head_size(url: str, timeout: int = 8) -> int:
    """Peso in byte di una risorsa (0 se non leggibile). Mai eccezioni all'esterno."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def resolve(base: str, src: str) -> str:
    return "" if src.startswith("data:") else urllib.parse.urljoin(base, src)


# ------------------------------------------------------------------ browser (opzionale)
def cdp_images(url: str, endpoint: str = "http://127.0.0.1:9222", attesa: float = 6.0) -> dict:
    """Conta le immagini del DOM reale aprendo la pagina in un Chrome con debug remoto.

    Serve per i siti che disegnano tutto via JavaScript: li' l'HTML statico e' vuoto e
    l'analisi statica direbbe "0 foto", che e' falso. La scheda viene chiusa a fine lavoro.

    Due trappole verificate su siti veri (30/09/2026, enotecabonbon.it):
    * la pagina si disegna solo se la scheda e' VISIBILE -> `Target.activateTarget`, altrimenti
      resta vuota e sembra un sito morto;
    * i contenuti possono stare dentro uno **shadow root**: `document.images` non li vede
      (restituisce 0 mentre a schermo le foto ci sono). Percio' si scende nei shadow root e si
      contano anche le `background-image`.
    Il risultato si aspetta: si interroga la pagina finche' non compaiono immagini (max ~attesa*2).
    """
    try:
        import websockets  # dipendenza opzionale: solo per questa modalita'
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("manca il pacchetto 'websockets' (pip install websockets)") from e

    ver = json.load(urllib.request.urlopen(endpoint + "/json/version", timeout=10))
    out: dict = {"url_finale": url, "foto": [], "media_rotti": 0, "video": 0}
    expr = """(() => {
        const out = [];
        const visti = new Set();
        const spingi = (src, alt) => {
            if (!src || src.startsWith('data:') || visti.has(src)) return;
            visti.add(src);
            out.push({src: src, alt: alt || ''});
        };
        const cammina = (root) => {
            root.querySelectorAll('img').forEach(i => { if (i.naturalWidth > 2) spingi(i.currentSrc || i.src, i.alt); });
            root.querySelectorAll('*').forEach(e => {
                const b = getComputedStyle(e).backgroundImage;
                if (b && b !== 'none' && b.includes('url(')) {
                    spingi(b.slice(b.indexOf('url(') + 4, b.lastIndexOf(')')).replace(/["']/g, ''),
                           e.getAttribute('aria-label'));
                }
                if (e.shadowRoot) cammina(e.shadowRoot);
            });
        };
        cammina(document);
        const vids = document.querySelectorAll('video, iframe[src*="youtube"], iframe[src*="vimeo"]').length;
        return JSON.stringify({
            url: location.href, video: vids, imgs: out.slice(0, 200),
            rotti: Array.from(document.querySelectorAll('img')).filter(i => i.complete && i.naturalWidth === 0).length,
            testo: (document.body.innerText || document.body.textContent || '').length,
            visibile: document.visibilityState
        });
    })()"""

    async def run():
        async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=42 * 1024 * 1024) as ws:
            i = [0]

            async def call(method, params=None, session=None):
                i[0] += 1
                msg = {"id": i[0], "method": method, "params": params or {}}
                if session:
                    msg["sessionId"] = session
                await ws.send(json.dumps(msg))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == i[0]:
                        return m

            t = await call("Target.createTarget", {"url": url})
            tid = t["result"]["targetId"]
            await call("Target.activateTarget", {"targetId": tid})       # senza questo la pagina resta vuota
            try:
                at = await call("Target.attachToTarget", {"targetId": tid, "flatten": True})
                sid = at["result"]["sessionId"]
                await asyncio.sleep(attesa)
                for _ in range(7):                                       # aspetta che il contenuto compaia
                    r = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, session=sid)
                    d = json.loads((r.get("result", {}).get("result") or {}).get("value") or "{}")
                    if d.get("imgs"):
                        break
                    await asyncio.sleep(attesa / 2)
                if not d:
                    d = {"imgs": [], "video": 0, "rotti": 0, "url": url}
                out["url_finale"] = d.get("url", url)
                out["foto"] = d.get("imgs", [])
                out["media_rotti"] = d.get("rotti", 0)
                out["video"] = d.get("video", 0)
                out["lunghezza_testo"] = d.get("testo", 0)
            finally:
                await call("Target.closeTarget", {"targetId": tid})

    asyncio.run(run())
    return out


def js_shell(html: str, p: Page) -> bool:
    """HTML quasi vuoto ma pieno di script: la pagina si disegna dopo, in JavaScript.

    Il testo "visibile" va misurato DOPO aver tolto script e style: nelle pagine moderne
    il JavaScript inline e' la maggior parte dei byte, e contarlo come testo fa sembrare
    piena una pagina che invece e' vuota (misurato: 9.890 caratteri "di testo" in una
    pagina senza una sola immagine).
    """
    senza_codice = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    testo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", senza_codice)).strip()
    return len(p.imgs) == 0 and (p.scripts >= 3 or len(testo) < 800)


# ------------------------------------------------------------------ analisi
def analizza(url: str, max_images: int = 30, measure: bool = True,
             use_cdp: bool = False, cdp_endpoint: str = "http://127.0.0.1:9222") -> dict:
    html, headers = fetch(url)
    p = Page()
    p.feed(html)
    p.find_socials()

    builder = next((k for k, pat in BUILDERS.items() if re.search(pat, html, re.I)), None)
    base = headers.get("Content-Location") or url
    html_len = len(html)
    shell = js_shell(html, p)
    resa = "statica"

    foto, video, rotti, testo_len = [], p.videos, 0, None
    if shell:
        resa = "statica (HTML vuoto: sito JavaScript)"

    # ripiego sul browser: se l'HTML non dice niente (o non mostra foto), oppure se lo chiedi tu
    if use_cdp or shell or not foto:
        try:
            d = cdp_images(url, cdp_endpoint)
            resa = "browser (CDP)"
            foto = [{"src": i["src"], "alt": (i.get("alt") or "").strip(),
                     "bytes": 0, "w": i.get("w"), "h": i.get("h")} for i in d["foto"]]
            video = max(video, d["video"])
            rotti = d["media_rotti"]
            testo_len = d.get("lunghezza_testo")
        except Exception as e:
            if use_cdp:                      # richiesto esplicitamente: l'errore si dice
                raise
            resa = f"statica (browser non disponibile: {type(e).__name__})"

    if not foto:
        visti: set[str] = set()
        for i in p.imgs:
            src = resolve(base, i["src"])
            if not src or src in visti or src.lower().endswith(SKIP_EXT):
                continue
            visti.add(src)
            foto.append({"src": src, "alt": i["alt"], "bytes": 0})

    # peso reale: una HEAD per immagine, solo sulle prime max_images
    peso = 0
    misurate = 0
    if measure:
        for f in foto[:max_images]:
            b = head_size(f["src"])
            f["bytes"] = b
            if b:
                peso += b
                misurate += 1

    stock, vere, senza_alt, pesanti = [], [], [], []
    for f in foto:
        nome = urllib.parse.unquote(urllib.parse.urlparse(f["src"]).path.rsplit("/", 1)[-1])
        host = urllib.parse.urlparse(f["src"]).netloc.lower()
        if any(s in host for s in STOCK_HOSTS) or re.search(r"stock|shutterstock|getty|depositphotos", nome, re.I):
            stock.append(nome or host)
        elif CAMERA_HINTS.match(nome):
            vere.append(nome)
        if not f["alt"]:
            senza_alt.append(nome or host)
        if f.get("bytes", 0) > 400_000:
            pesanti.append(f"{nome} ({f['bytes'] // 1024} KB)")

    return {
        "url": url,
        "titolo": p.title,
        "https": url.lower().startswith("https"),
        "resa": resa,
        "sito_javascript": shell,
        "builder": builder,
        "html_kb": round(html_len / 1024, 1),
        "script": p.scripts,
        "foto_totali": len(foto),
        "foto_con_alt": len(foto) - len(senza_alt),
        "senza_descrizione": len(senza_alt),
        "peso_foto_mb": round(peso / 1_048_576, 2),
        "foto_misurate": misurate,
        "media_rotti": rotti,
        "sospette_da_catalogo": stock[:12],
        "probabili_foto_vere": vere[:12],
        "foto_pesanti": pesanti[:8],
        "video": video,
        "social_presenti": sorted(p.socials),
        "social_mancanti": [k for k in SOCIALS if k not in p.socials],
        "og_image": bool(p.meta.get("og:image")),
        "meta_description": bool(p.meta.get("description") or p.meta.get("og:description")),
        "viewport_mobile": "viewport" in p.meta,
    }


def gancio(d: dict) -> str:
    """Una frase-fatto da usare in un'email: niente aggettivi, solo cio' che si vede."""
    pezzi = []
    if d["foto_totali"] <= 20:
        pezzi.append(f"il sito ha {d['foto_totali']} immagini in tutto")
    if d["senza_descrizione"]:
        pezzi.append(f"{d['senza_descrizione']} senza descrizione")
    if d["sospette_da_catalogo"]:
        pezzi.append(f"{len(d['sospette_da_catalogo'])} sembrano foto di catalogo")
    if d["media_rotti"]:
        pezzi.append(f"{d['media_rotti']} immagini non si caricano")
    if d["peso_foto_mb"] >= 5:
        pezzi.append(f"le foto pesano {d['peso_foto_mb']} MB (il sito si apre lento)")
    if not d["video"]:
        pezzi.append("nessun video")
    if d["social_mancanti"]:
        pezzi.append("nessun link a " + ", ".join(d["social_mancanti"]))
    if not pezzi:
        pezzi.append("il sito e' gia' ben fotografato: qui non c'e' niente da vendere")
    return ", ".join(pezzi) + "."


def formatta(d: dict) -> str:
    r = [
        f"vetrina-check — {d['url']}",
        f"  titolo: {d['titolo'][:70]}",
        f"  resa: {d['resa']}" + (f" · builder: {d['builder']}" if d["builder"] else ""),
        f"  foto: {d['foto_totali']} · con descrizione: {d['foto_con_alt']} · "
        + (f"peso stimato: {d['peso_foto_mb']} MB ({d['foto_misurate']} misurate)"
           if d["foto_misurate"] else "peso: non misurato"),
        f"  da catalogo/stock: {len(d['sospette_da_catalogo'])}"
        + (f" ({', '.join(d['sospette_da_catalogo'][:4])})" if d["sospette_da_catalogo"] else ""),
        f"  probabili foto vere: {len(d['probabili_foto_vere'])}"
        + (f" ({', '.join(d['probabili_foto_vere'][:4])})" if d["probabili_foto_vere"] else ""),
        f"  video: {'si' if d['video'] else 'NO'} · social: {', '.join(d['social_presenti']) or 'nessuno'}",
        f"  mobile/viewport: {'ok' if d['viewport_mobile'] else 'da controllare'} · "
        f"https: {'ok' if d['https'] else 'NO'} · og:image: {'ok' if d['og_image'] else 'MANCA'}",
        f"  gancio: {gancio(d)}",
    ]
    return "\n".join(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quanto sono in vetrina le foto di un sito?")
    ap.add_argument("url", nargs="?", help="pagina da analizzare")
    ap.add_argument("--json", action="store_true", help="output JSON")
    ap.add_argument("--max-images", type=int, default=30, help="quante immagini pesare (default 30)")
    ap.add_argument("--no-measure", action="store_true", help="salta la misura dei pesi (piu' veloce)")
    ap.add_argument("--cdp", action="store_true", help="forza il conteggio dal browser (siti JavaScript)")
    ap.add_argument("--cdp-endpoint", default="http://127.0.0.1:9222", help="endpoint di debug del Chrome")
    ap.add_argument("--selftest", action="store_true", help="prova il parser su HTML di esempio, senza rete")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.url:
        ap.print_help()
        return 2
    if not a.url.startswith(("http://", "https://")):
        a.url = "https://" + a.url
    try:
        d = analizza(a.url, max_images=a.max_images, measure=not a.no_measure,
                     use_cdp=a.cdp, cdp_endpoint=a.cdp_endpoint)
    except urllib.error.HTTPError as e:
        print(f"vetrina-check: il sito ha risposto {e.code} {e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"vetrina-check: non raggiungibile ({e.reason})", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"vetrina-check: {e}", file=sys.stderr)
        return 1
    print(json.dumps(d, ensure_ascii=False, indent=1) if a.json else formatta(d))
    return 0


# ---------------------------------------------------------------- selftest
HTML_PROVA = """<html><head><title>Trattoria Prova</title>
<meta property="og:image" content="/foto/sala.jpg">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Trattoria a Civitavecchia">
<link rel="stylesheet" href="/wp-content/style.css">
</head><body>
<img src="/img/pizza1.jpg" alt="">
<img src="/img/IMG_20240912_1830.jpg" alt="la nostra sala">
<img src="https://images.shutterstock.com/12345/stock-photo-pasta.jpg">
<img src="/img/logo.svg" alt="logo">
<img data-src="/img/torta.jpg" alt="torta di compleanno">
<video src="/media/gira.mp4"></video>
<a href="https://www.instagram.com/trattoriaprova/">IG</a>
</body></html>"""

HTML_SHELL = """<html><head><title>Vetrina Nuova</title>
<script src="/app.js"></script><script src="/vendor.js"></script><script src="/analytics.js"></script>
</head><body><div id="root"></div></body></html>"""


def selftest() -> int:
    p = Page()
    p.feed(HTML_PROVA)
    p.find_socials()
    problemi = []
    if len(p.imgs) != 5:                     # pizza, IMG_, shutterstock, logo.svg, torta (data-src)
        problemi.append(f"immagini: attese 5, trovate {len(p.imgs)}")
    if p.socials != {"instagram"}:
        problemi.append(f"social: attesi ['instagram'], trovati {sorted(p.socials)}")
    if p.videos != 1:
        problemi.append(f"video: atteso 1, trovato {p.videos}")
    if p.title != "Trattoria Prova":
        problemi.append(f"titolo letto: '{p.title}'")
    if not p.meta.get("og:image"):
        problemi.append("og:image non letto")
    pp = Page(); pp.feed(HTML_PROVA); pp.find_socials()
    if js_shell(HTML_PROVA, pp):
        problemi.append("js_shell ha giudicato 'sito JavaScript' una pagina statica con 5 immagini")
    ps = Page(); ps.feed(HTML_SHELL)
    if not js_shell(HTML_SHELL, ps):
        problemi.append("js_shell non ha riconosciuto la pagina JavaScript (0 immagini, 3 script)")
    if problemi:
        print("SELFTEST FALLITO:")
        for x in problemi:
            print(" -", x)
        return 1
    print("SELFTEST OK: parser, social, video, meta, titolo e riconoscimento siti JavaScript.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
