"""
SONDE FFHandball — à lancer UNE fois.
Ouvre chaque page de poule dans un vrai navigateur (Playwright), capture toutes les
requêtes JSON (XHR/fetch) que la page envoie, et sauvegarde le HTML rendu + le texte visible.
Résultat : dossier probe_out/ (récupérable dans l'onglet Actions > Artifacts).
"""
import json
import pathlib
from playwright.sync_api import sync_playwright

BASE = "https://www.ffhandball.fr/competitions/saison-2026-2027-22/departemental/"
TEAMS = {
    "srg1": BASE + "gironde-16-masculins-pre-regionale-32457/poule-190872/",
    "srg2": BASE + "gironde-16-masculins-promotion-32460/",
    "srf":  BASE + "gironde-16-feminines-honneur-32465/",
    "u18g": BASE + "gironde-u18-masculins-32774/poule-194137/",
    "u18f": BASE + "gironde-u18-feminine-32775/poule-193706/",
    "u15g": BASE + "gironde-u15-masculin-32826/poule-194103/",
    "u15f": BASE + "gironde-u15-feminine-32776/poule-194055/",
    "u13g": BASE + "gironde-u13-masculin-32827/poule-194151/",
    "u13f": BASE + "gironde-u13-feminine-32777/poule-193722/",
    "u13f2": BASE + "gironde-u13-feminine-32777/poule-194061/",
}

OUT = pathlib.Path("probe_out")
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="fr-FR")
    for key, url in TEAMS.items():
        page = ctx.new_page()
        calls = []

        def on_response(r, calls=calls):
            try:
                if r.request.resource_type in ("xhr", "fetch"):
                    calls.append({
                        "url": r.url,
                        "status": r.status,
                        "content_type": r.headers.get("content-type", ""),
                        "body": r.text()[:300000],
                    })
            except Exception as e:
                calls.append({"url": r.url, "error": str(e)})

        page.on("response", on_response)
        status = "ok"
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            (OUT / f"{key}.html").write_text(page.content(), encoding="utf-8")
            (OUT / f"{key}.txt").write_text(page.inner_text("body"), encoding="utf-8")
        except Exception as e:
            status = f"erreur: {e}"
        (OUT / f"{key}.calls.json").write_text(
            json.dumps({"page": url, "status": status, "calls": calls}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(key, status, len(calls), "requêtes JSON capturées")
        page.close()
    browser.close()
