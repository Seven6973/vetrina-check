"""Test offline di vetrina-check: niente rete, solo HTML di esempio.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vetrina  # noqa: E402

HTML = """<html><head><title>Trattoria Prova</title>
<meta property="og:image" content="/foto/sala.jpg">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Trattoria a Civitavecchia">
<link rel="stylesheet" href="/wp-content/style.css">
</head><body>
<img src="/img/pizza1.jpg" alt="">
<img src="/img/IMG_20240912_1830.jpg" alt="la nostra sala">
<img src="https://images.shutterstock.com/12345/stock-photo-pasta.jpg" alt="pasta">
<img src="/img/logo.svg" alt="logo">
<img data-src="/img/torta.jpg" alt="torta di compleanno">
<video src="/media/gira.mp4"></video>
<a href="https://www.instagram.com/trattoriaprova/">IG</a>
</body></html>"""

SHELL = """<html><head><title>Vetrina Nuova</title>
<script src="/app.js"></script><script src="/vendor.js"></script><script src="/analytics.js"></script>
</head><body><div id="root"></div></body></html>"""


def parse(html):
    p = vetrina.Page()
    p.feed(html)
    p.find_socials()
    return p


class TestParser(unittest.TestCase):
    def test_immagini_incluse_lazy_e_srcset(self):
        p = parse(HTML)
        # pizza, IMG_, shutterstock, logo.svg, torta(data-src) -> 5 nodi immagine
        self.assertEqual(len(p.imgs), 5)

    def test_alt_vuoto_rilevato(self):
        p = parse(HTML)
        vuoti = [i for i in p.imgs if not i["alt"]]
        self.assertEqual(len(vuoti), 1)
        self.assertIn("pizza1.jpg", vuoti[0]["src"])

    def test_social_e_video(self):
        p = parse(HTML)
        self.assertEqual(p.socials, {"instagram"})
        self.assertEqual(p.videos, 1)

    def test_titolo_e_meta(self):
        p = parse(HTML)
        self.assertEqual(p.title, "Trattoria Prova")
        self.assertTrue(p.meta.get("og:image"))
        self.assertIn("viewport", p.meta)

    def test_riconosce_il_builder(self):
        self.assertIsNotNone(vetrina.re.compile("|".join(vetrina.BUILDERS.values())).search(HTML))


class TestJsShell(unittest.TestCase):
    def test_pagina_statica_non_e_shell(self):
        self.assertFalse(vetrina.js_shell(HTML, parse(HTML)))

    def test_pagina_javascript_e_shell(self):
        self.assertTrue(vetrina.js_shell(SHELL, parse(SHELL)))

    def test_il_javascript_inline_non_conta_come_testo_visibile(self):
        # il caso reale: 9.890 caratteri "di testo" che erano tutti codice inline
        pieno_di_js = ("<html><head><title>x</title>"
                       + "<script>" + ("var a=1;" * 2000) + "</script>" * 1
                       + "</head><body><div id='root'></div></body></html>")
        self.assertTrue(vetrina.js_shell(pieno_di_js, parse(pieno_di_js)))


class TestGancio(unittest.TestCase):
    def base(self, **kw):
        d = {"foto_totali": 30, "senza_descrizione": 0, "sospette_da_catalogo": [],
             "media_rotti": 0, "peso_foto_mb": 1.0, "video": True,
             "social_mancanti": []}
        d.update(kw)
        return d

    def test_sito_messoa_lo_dice(self):
        self.assertIn("niente da vendere", vetrina.gancio(self.base()))

    def test_usa_i_numeri_non_gli_aggettivi(self):
        g = vetrina.gancio(self.base(foto_totali=8, senza_descrizione=3, video=False))
        self.assertIn("8 immagini", g)
        self.assertIn("3 senza descrizione", g)
        self.assertIn("nessun video", g)

    def test_segnala_le_foto_pesanti_e_i_social_mancanti(self):
        g = vetrina.gancio(self.base(peso_foto_mb=9.4, social_mancanti=["facebook", "instagram"]))
        self.assertIn("9.4 MB", g)
        self.assertIn("facebook", g)


class TestUtility(unittest.TestCase):
    def test_resolve(self):
        self.assertEqual(vetrina.resolve("https://a.it/x/", "/img/1.jpg"), "https://a.it/img/1.jpg")
        self.assertEqual(vetrina.resolve("https://a.it/", "img/1.jpg"), "https://a.it/img/1.jpg")

    def test_data_uri_ignorata(self):
        self.assertEqual(vetrina.resolve("https://a.it/", "data:image/png;base64,AAAA"), "")

    def test_formatta_contiene_il_gancio(self):
        d = {"url": "https://a.it", "titolo": "T", "resa": "statica", "builder": None,
             "foto_totali": 3, "foto_con_alt": 3, "senza_descrizione": 0, "peso_foto_mb": 0.0,
             "foto_misurate": 0,
             "sospette_da_catalogo": [], "probabili_foto_vere": [], "video": False,
             "social_presenti": [], "social_mancanti": ["facebook"], "viewport_mobile": True,
             "https": True, "og_image": False, "media_rotti": 0, "script": 0, "html_kb": 1.0,
             "sito_javascript": False}
        out = vetrina.formatta(d)
        self.assertIn("gancio:", out)
        self.assertIn("peso: non misurato", out)

    def test_selftest_interno(self):
        self.assertEqual(vetrina.selftest(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
