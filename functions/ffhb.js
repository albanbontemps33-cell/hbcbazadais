// Netlify Function — scraping FFHB 100% gratuit
// Se déclenche quand le site appelle /.netlify/functions/ffhb?poule=XXXXXX&type=classement

exports.handler = async (event) => {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
  };

  const { poule, type } = event.queryStringParameters || {};

  if (!poule || !type) {
    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Paramètres manquants' }) };
  }

  const BASE = 'https://www.ffhandball.fr/competitions';

  // On cherche l'URL de la poule selon le type demandé
  // Le site FFHB utilise des slugs — on reconstruit l'URL depuis l'ID de poule
  // L'API interne FFHB expose les données en JSON via leur endpoint GraphQL/REST
  try {
    // FFHB expose une API publique non documentée qu'on peut utiliser directement
    // Format : /wp-json/ffhb/v1/poule/{id}/classement  ou  /calendrier  ou  /resultats
    const endpoints = {
      classement: `https://www.ffhandball.fr/wp-json/ffhb/v1/poules/${poule}/classement`,
      calendrier: `https://www.ffhandball.fr/wp-json/ffhb/v1/poules/${poule}/calendrier`,
      resultats:  `https://www.ffhandball.fr/wp-json/ffhb/v1/poules/${poule}/rencontres`,
    };

    const url = endpoints[type];
    if (!url) return { statusCode: 400, headers, body: JSON.stringify({ error: 'Type invalide' }) };

    const resp = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; HBCBazadais/1.0)',
        'Accept': 'application/json',
        'Referer': 'https://www.ffhandball.fr/',
      }
    });

    if (!resp.ok) {
      // Fallback : scraping HTML de la page publique
      return await scrapeFallback(poule, type, headers);
    }

    const data = await resp.json();
    return { statusCode: 200, headers, body: JSON.stringify(parseFFHB(data, type)) };

  } catch (err) {
    return await scrapeFallback(poule, type, headers);
  }
};

// Parsing des données JSON FFHB
function parseFFHB(data, type) {
  if (type === 'classement') {
    const rows = data?.classement || data?.data || data || [];
    return {
      type: 'classement',
      rows: rows.map((r, i) => ({
        rang:    r.rang || r.rank || (i + 1),
        club:    r.club?.nom || r.equipe || r.nom || '—',
        joues:   r.joues ?? r.matchs_joues ?? '—',
        gagnes:  r.gagnes ?? r.victoires ?? '—',
        nuls:    r.nuls ?? '—',
        perdus:  r.perdus ?? r.defaites ?? '—',
        points:  r.points ?? r.pts ?? '—',
        isHBC:   (r.club?.nom || r.equipe || r.nom || '').toLowerCase().includes('bazadais'),
      }))
    };
  }

  if (type === 'calendrier' || type === 'resultats') {
    const rows = data?.rencontres || data?.calendrier || data?.data || data || [];
    return {
      type: 'calendrier',
      rows: rows.map(r => ({
        date:      formatDate(r.date || r.dateMatch || ''),
        domicile:  r.equipe_domicile?.nom || r.domicile || '—',
        exterieur: r.equipe_exterieur?.nom || r.exterieur || '—',
        score_dom: r.score_domicile ?? r.but_domicile ?? null,
        score_ext: r.score_exterieur ?? r.but_exterieur ?? null,
        joue:      r.joue ?? r.termine ?? false,
        isHBC:     (r.equipe_domicile?.nom || r.domicile || r.equipe_exterieur?.nom || r.exterieur || '').toLowerCase().includes('bazadais'),
      }))
    };
  }

  return { type, rows: [] };
}

// Fallback scraping HTML si l'API JSON ne répond pas
async function scrapeFallback(poule, type, headers) {
  try {
    // On scrape directement la page HTML publique FFHB
    const url = `https://www.ffhandball.fr/competitions/?poule=${poule}`;
    const resp = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html' }
    });
    const html = await resp.text();

    // Extraction basique depuis le HTML
    const result = scrapeHTML(html, type);
    return { statusCode: 200, headers, body: JSON.stringify(result) };
  } catch (err) {
    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ type, rows: [], error: 'Données temporairement indisponibles' })
    };
  }
}

function scrapeHTML(html, type) {
  // Extraction des tableaux HTML FFHB
  const rows = [];

  if (type === 'classement') {
    // Regex pour capturer les lignes du tableau de classement FFHB
    const trRegex = /<tr[^>]*class="[^"]*(?:poule|classement|team)[^"]*"[^>]*>([\s\S]*?)<\/tr>/gi;
    const tdRegex = /<td[^>]*>([\s\S]*?)<\/td>/gi;
    let trMatch;
    while ((trMatch = trRegex.exec(html)) !== null) {
      const tds = [];
      let tdMatch;
      const tdRe = /<td[^>]*>([\s\S]*?)<\/td>/gi;
      while ((tdMatch = tdRe.exec(trMatch[1])) !== null) {
        tds.push(stripTags(tdMatch[1]).trim());
      }
      if (tds.length >= 5) {
        rows.push({
          rang: tds[0] || '—', club: tds[1] || '—',
          joues: tds[2] || '—', gagnes: tds[3] || '—',
          nuls: tds[4] || '—', perdus: tds[5] || '—',
          points: tds[tds.length - 1] || '—',
          isHBC: (tds[1] || '').toLowerCase().includes('bazadais'),
        });
      }
    }
  }

  return { type, rows, source: 'scrape' };
}

function stripTags(html) {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function formatDate(str) {
  if (!str) return '—';
  try {
    const d = new Date(str);
    if (isNaN(d)) return str;
    return d.toLocaleDateString('fr-FR', { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' });
  } catch { return str; }
}
