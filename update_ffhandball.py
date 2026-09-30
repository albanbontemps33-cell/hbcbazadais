"""
Met à jour data/<equipe>.json (classement + calendrier/résultats) depuis ffhandball.fr.

- Ouvre chaque page de poule dans un vrai navigateur (Playwright), parcourt toutes les journées.
- Ne réécrit un fichier que si son contenu a changé (pas de commit inutile).
- Si une équipe échoue, son ancien fichier est conservé et le script termine en erreur
  (les autres équipes sont quand même mises à jour).
"""
import json
import pathlib
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

BASE = "https://www.ffhandball.fr/competitions/saison-2026-2027-22/departemental/"

# clé -> (nom affiché, URL FFHandball).  À adapter quand une nouvelle phase/poule démarre.
TEAMS = {
    "srg1":  ("SG1",       BASE + "gironde-16-masculins-pre-regionale-32457/poule-190872/"),
    "srg2":  ("SG2",       BASE + "gironde-16-masculins-promotion-32460/"),
    "srf":   ("SF",        BASE + "gironde-16-feminines-honneur-32465/"),
    "u18g":  ("-18 G",     BASE + "gironde-u18-masculins-32774/poule-194137/"),
    "u18f":  ("-18 F",     BASE + "gironde-u18-feminine-32775/poule-193706/"),
    "u15g":  ("-15 G",     BASE + "gironde-u15-masculin-32826/poule-194103/"),
    "u15f":  ("-15 F",     BASE + "gironde-u15-feminine-32776/poule-194055/"),
    "u13g":  ("-13 G",     BASE + "gironde-u13-masculin-32827/poule-194151/"),
    "u13f":  ("-13 F 1",   BASE + "gironde-u13-feminine-32777/poule-193722/"),
    "u13f2": ("-13 F 2",   BASE + "gironde-u13-feminine-32777/poule-194061/"),
}

CLUB_KEYWORD = "BAZADAIS"  # pour repérer nos matchs / notre ligne de classement
OUT_DIR = pathlib.Path("data")
TZ = ZoneInfo("Europe/Paris")

MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
}

# ── Extraction dans la page (JavaScript) ──────────────────────────────────────
JS_MATCHES = """() => Array.from(document.querySelectorAll('a[class*="styles_rencontre"]')).map(a => {
  const txt = el => el ? el.innerText.trim() : '';
  const names = a.querySelectorAll('[class*="styles_teamName"]');
  const scores = a.querySelectorAll('[class*="styles_score"]');
  return {
    href: a.getAttribute('href') || '',
    date: txt(a.querySelector('[class*="block_date"]')),
    live: txt(a.querySelector('[class*="block_live"]')),
    text: a.innerText,
    dom: txt(names[0]), ext: txt(names[1]),
    sdom: txt(scores[0]), sext: txt(scores[1]),
  };
})"""

JS_CLASSEMENT = """() => Array.from(document.querySelectorAll('table[class*="style_classement"] tbody tr')).map(tr => {
  const td = tr.querySelectorAll('td');
  const name = tr.querySelector('[class*="equipeName"]');
  return {
    pos: td[0] ? td[0].innerText.trim() : '',
    club: name ? name.innerText.trim() : (td[1] ? td[1].innerText.trim() : ''),
    pts: td.length ? td[td.length - 1].innerText.trim() : '',
  };
})"""

JS_SIGNATURE = """() => Array.from(document.querySelectorAll('a[class*="styles_rencontre"]'))
  .map(a => a.getAttribute('href')).join('|')"""

JS_SELECTED = """() => {
  const b = document.querySelector('[class*="styles_pagination"] button[class*="styles_selected"]');
  return b ? b.innerText.trim() : '';
}"""

JS_MAX_JOURNEE = """() => {
  let max = 0;
  document.querySelectorAll('[class*="styles_pagination"] button').forEach(b => {
    const m = b.innerText.trim().match(/^J(\\d+)$/);
    if (m) max = Math.max(max, parseInt(m[1], 10));
  });
  return max;
}"""


# ── Outils ────────────────────────────────────────────────────────────────────
def parse_date(text):
    """'samedi 17 octobre 2026 à 20H00' -> ('2026-10-17T20:00', 'Samedi 17 octobre 2026 à 20h00')."""
    m = re.search(r"(\d{1,2})\s+([A-Za-zéèêûîôàç]+)\s+(\d{4})(?:\s+à\s+(\d{1,2})[Hh](\d{2}))?", text)
    if not m:
        return "", text.strip()
    day, month_name, year, hh, mm = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return "", text.strip()
    hh = int(hh) if hh is not None else 0
    mm = int(mm) if mm is not None else 0
    iso = f"{int(year):04d}-{month:02d}-{int(day):02d}T{hh:02d}:{mm:02d}"
    display = text.strip()
    display = re.sub(r"(\d)H(\d{2})", r"\1h\2", display)
    display = display[:1].upper() + display[1:]
    return iso, display


def to_int(s):
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def extract_current(page):
    """Lit ce qui est affiché : matchs de la journée courante + classement + date de mise à jour."""
    matches = page.evaluate(JS_MATCHES)
    classement = page.evaluate(JS_CLASSEMENT)
    body = page.evaluate("() => document.body.innerText")
    m = re.search(r"Date de mise à jour\s*:\s*([^\n]+)", body)
    return matches, classement, (m.group(1).strip() if m else "")


def match_signature(page):
    return page.evaluate(JS_SIGNATURE)


def click_js(page, selector):
    """Clic via JavaScript (insensible aux bandeaux cookies qui recouvrent la page)."""
    ok = page.evaluate(
        """sel => { const el = document.querySelector(sel); if (!el) return false; el.click(); return true; }""",
        selector,
    )
    return ok


def wait_signature_change(page, previous, timeout=20000):
    page.wait_for_function(
        """prev => {
             const s = Array.from(document.querySelectorAll('a[class*="styles_rencontre"]'))
               .map(a => a.getAttribute('href')).join('|');
             return s && s !== prev;
           }""",
        arg=previous,
        timeout=timeout,
    )


def go_to_first_journee(page):
    """Se place sur J1 (sans rien faire si on y est déjà)."""
    if page.evaluate(JS_SELECTED) == "J1":
        return
    before = match_signature(page)
    clicked = page.evaluate(
        """() => {
             const b = Array.from(document.querySelectorAll('[class*="styles_pagination"] button'))
               .find(x => x.innerText.trim() === 'J1');
             if (!b) return false; b.click(); return true;
           }"""
    )
    if not clicked:
        raise RuntimeError("bouton J1 introuvable")
    wait_signature_change(page, before)


def scrape_all_journees(page):
    total = page.evaluate(JS_MAX_JOURNEE)
    if total < 1:
        raise RuntimeError("nombre de journées introuvable")

    classement = None
    source_updated = ""
    all_matches = {}

    go_to_first_journee(page)
    for j in range(1, total + 1):
        if j > 1:
            before = match_signature(page)
            if not click_js(page, 'button[title="Journée suivante"]'):
                raise RuntimeError("bouton 'Journée suivante' introuvable")
            wait_signature_change(page, before)
        matches, cl, upd = extract_current(page)
        if classement is None and cl:
            classement = cl
            source_updated = upd
        for m in matches:
            m["journee"] = j
            all_matches[m["href"]] = m
    return list(all_matches.values()), classement or [], source_updated, total


# ── Construction du JSON ──────────────────────────────────────────────────────
def build_json(name, url, raw_matches, raw_classement, source_updated, total_journees):
    calendrier = []
    stats = {}  # club -> dict

    def st(club):
        return stats.setdefault(club, {"j": 0, "g": 0, "n": 0, "p": 0, "bp": 0, "bc": 0})

    for m in raw_matches:
        iso, display = parse_date(m["date"])
        sd, se = to_int(m["sdom"]), to_int(m["sext"])
        played = sd is not None and se is not None
        low = m["text"].lower()
        if played:
            statut = "joué"
        elif "report" in low:
            statut = "reporté"
        else:
            statut = "à venir"
        rid = re.search(r"rencontre-(\d+)", m["href"])
        calendrier.append({
            "id": rid.group(1) if rid else "",
            "journee": m["journee"],
            "iso": iso,
            "date": display,
            "domicile": m["dom"],
            "exterieur": m["ext"],
            "score_dom": sd if played else None,
            "score_ext": se if played else None,
            "statut": statut,
            "nous": CLUB_KEYWORD in (m["dom"] + " " + m["ext"]).upper(),
        })
        if played:
            a, b = st(m["dom"]), st(m["ext"])
            a["j"] += 1; b["j"] += 1
            a["bp"] += sd; a["bc"] += se
            b["bp"] += se; b["bc"] += sd
            if sd > se:
                a["g"] += 1; b["p"] += 1
            elif sd < se:
                b["g"] += 1; a["p"] += 1
            else:
                a["n"] += 1; b["n"] += 1

    calendrier.sort(key=lambda x: (x["iso"] or "9999", x["journee"], x["id"]))

    classement = []
    for r in raw_classement:
        s = stats.get(r["club"], {"j": 0, "g": 0, "n": 0, "p": 0, "bp": 0, "bc": 0})
        classement.append({
            "rang": to_int(r["pos"]) or r["pos"],
            "club": r["club"],
            "joues": s["j"], "gagnes": s["g"], "nuls": s["n"], "perdus": s["p"],
            "bp": s["bp"], "bc": s["bc"], "diff": s["bp"] - s["bc"],
            "points": to_int(r["pts"]) if to_int(r["pts"]) is not None else r["pts"],
            "nous": CLUB_KEYWORD in r["club"].upper(),
        })

    if not calendrier:
        raise RuntimeError("aucun match trouvé")
    if not classement:
        raise RuntimeError("classement vide")

    return {
        "equipe": name,
        "source": url,
        "source_mise_a_jour": source_updated,
        "journees": total_journees,
        "classement": classement,
        "calendrier": calendrier,
    }


def save_if_changed(key, data):
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{key}.json"
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            old.pop("genere_le", None)
            if old == data:
                return False
        except Exception:
            pass
    out = dict(data)
    out["genere_le"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


# ── Programme principal ───────────────────────────────────────────────────────
def main():
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="fr-FR", timezone_id="Europe/Paris",
                                  viewport={"width": 1400, "height": 900})
        for key, (name, url) in TEAMS.items():
            last_error = None
            for attempt in (1, 2, 3):
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    page.wait_for_selector('a[class*="styles_rencontre"]', timeout=30000)
                    raw, cl, upd, total = scrape_all_journees(page)
                    data = build_json(name, url, raw, cl, upd, total)
                    changed = save_if_changed(key, data)
                    print(f"[OK]  {key:6s} {len(data['calendrier'])} matchs, "
                          f"{len(data['classement'])} équipes, {total} journées"
                          f"{' (modifié)' if changed else ' (inchangé)'}")
                    last_error = None
                    page.close()
                    break
                except Exception as e:  # noqa: BLE001
                    last_error = f"{type(e).__name__}: {e}"
                    print(f"[..]  {key:6s} tentative {attempt} échouée : {last_error}")
                    page.close()
            if last_error:
                failures.append((key, last_error))
                print(f"[ERR] {key:6s} abandon, ancien fichier conservé")
        browser.close()

    if failures:
        print("\nÉchecs :", ", ".join(k for k, _ in failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
