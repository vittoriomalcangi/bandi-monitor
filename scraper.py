#!/usr/bin/env python3
"""
BandiMonitor — Scraper RSS + Score AI
Legge i feed RSS regionali, filtra i bandi formazione,
calcola lo score con Claude API e aggiorna bandi.json
"""

import json
import os
import re
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, date
from urllib.request import urlopen, Request
from urllib.error import URLError
import http.client

# ─── Configurazione ───────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"  # Haiku: più veloce e economico per lo scraping
BANDI_JSON = "bandi.json"

FEED_RSS = [
    {
        "nome": "Lombardia",
        "url": "https://www.regione.lombardia.it/wps/wcm/connect/DisegnoPRL/FeedRSS/stFeedRssBandi",
        "region": "Lombardia"
    },
    {
        "nome": "Emilia-Romagna",
        "url": "https://imprese.regione.emilia-romagna.it/Finanziamenti/RSS",
        "region": "Emilia-Romagna"
    },
    {
        "nome": "Campania",
        "url": "https://www.regione.campania.it/regione/it/feed-rss",
        "region": "Campania"
    },
    {
        "nome": "Lazio",
        "url": "https://www.regione.lazio.it/rss",
        "region": "Lazio"
    },
    {
        "nome": "Piemonte",
        "url": "https://bandi.regione.piemonte.it/contributi-finanziamenti?rss=1",
        "region": "Piemonte"
    },
    {
        "nome": "Puglia",
        "url": "http://formazione.regione.puglia.it/index.php?page=news&opz=readsch&id=1",
        "region": "Puglia"
    },
    {
        "nome": "Italia Domani (PNRR)",
        "url": "https://www.italiadomani.gov.it/content/sogei-ng/it/it/feed-rss/_jcr_content/par/accordion_container/accordion-item-0/column_container/par/accordion_item/column_container_copy/par/link_list_container/link_list/item0.stream/1638886579694/e1517d8d5b1d8d2dc1f7b01fd6b17ac5bddf18d0/bandi.xml",
        "region": "Nazionale PNRR"
    },
]

# Parole chiave per filtrare bandi rilevanti
KEYWORDS_POSITIVI = [
    "formazione", "fad", "e-learning", "elearning", "corso", "corsi",
    "competenze", "digitale", "digitali", "fse", "fse+", "pnrr",
    "fondi interprofessionali", "voucher formativo", "upskilling",
    "reskilling", "aggiornamento professionale", "blended", "online",
    "apprendimento", "qualificazione", "riqualificazione"
]

KEYWORDS_NEGATIVI = [
    "appalto", "gara", "lavori pubblici", "infrastrutture", "edilizia",
    "strade", "ponti", "rifiuti", "acqua", "energia", "agricoltura",
    "concorso pubblico", "assunzione", "bando di concorso"
]

# ─── Utilities ────────────────────────────────────────────────────────────────

def genera_id(titolo: str, url: str) -> str:
    """Genera un ID univoco basato su titolo + url."""
    testo = f"{titolo}{url}".lower().strip()
    return hashlib.md5(testo.encode()).hexdigest()[:12]

def pulisci_testo(testo: str) -> str:
    """Rimuove HTML e spazi extra."""
    if not testo:
        return ""
    testo = re.sub(r'<[^>]+>', ' ', testo)
    testo = re.sub(r'\s+', ' ', testo)
    return testo.strip()[:2000]  # max 2000 caratteri per non sprecare token

def fetch_rss(url: str, timeout: int = 15) -> str:
    """Scarica un feed RSS con user agent browser."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BandiMonitor/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ Errore fetch {url}: {e}")
        return ""

def parse_rss(xml_text: str) -> list[dict]:
    """Estrae items da un feed RSS/Atom."""
    items = []
    if not xml_text:
        return items
    try:
        # Rimuovi namespace per semplicità
        xml_text = re.sub(r'\sxmlns[^"]*"[^"]*"', '', xml_text)
        xml_text = re.sub(r'<([^/\s>]+):[^>]*>', lambda m: f'<{m.group(1)}>', xml_text)
        root = ET.fromstring(xml_text)
        # Supporta RSS 2.0 e Atom
        for item in root.iter("item"):
            titolo = item.findtext("title") or ""
            link = item.findtext("link") or ""
            desc = item.findtext("description") or item.findtext("summary") or ""
            pub_date = item.findtext("pubDate") or item.findtext("published") or ""
            items.append({
                "titolo": pulisci_testo(titolo),
                "url": link.strip(),
                "descrizione": pulisci_testo(desc),
                "data_pubblicazione": pub_date[:10] if pub_date else ""
            })
    except ET.ParseError as e:
        print(f"  ⚠ Errore parsing XML: {e}")
    return items

def is_rilevante(titolo: str, descrizione: str) -> bool:
    """Filtra localmente prima di chiamare l'AI."""
    testo = (titolo + " " + descrizione).lower()
    ha_keyword_pos = any(kw in testo for kw in KEYWORDS_POSITIVI)
    ha_keyword_neg = any(kw in testo for kw in KEYWORDS_NEGATIVI)
    return ha_keyword_pos and not ha_keyword_neg

# ─── Claude API ───────────────────────────────────────────────────────────────

def chiama_claude(prompt: str, max_tokens: int = 300) -> str:
    """Chiama Claude API via HTTP puro (senza librerie esterne)."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY non impostata")

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    conn = http.client.HTTPSConnection("api.anthropic.com")
    conn.request("POST", "/v1/messages", body=body, headers={
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    })
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()

    if "error" in data:
        raise ValueError(f"Errore API: {data['error']}")
    return data["content"][0]["text"]

def verifica_rilevanza_ai(titolo: str, descrizione: str) -> bool:
    """Usa AI per confermare se il bando è rilevante."""
    prompt = f"""Sei un filtro per bandi di formazione italiani.
Rispondi SOLO con "SI" o "NO".

Un bando è rilevante se riguarda formazione professionale erogabile in modalità e-learning, FAD, online, blended, video o materiale digitale fruibile autonomamente senza un docente in presenza.

TITOLO: {titolo}
DESCRIZIONE: {descrizione[:500]}

Il bando è rilevante?"""
    try:
        risposta = chiama_claude(prompt, max_tokens=10).strip().upper()
        return risposta.startswith("SI") or risposta == "SÌ"
    except Exception as e:
        print(f"  ⚠ Errore verifica AI: {e}")
        return False

def calcola_score_ai(titolo: str, descrizione: str) -> dict:
    """Calcola lo score del bando con AI."""
    prompt = f"""Analizza questo bando di formazione italiano e restituisci SOLO un oggetto JSON valido, senza testo aggiuntivo, senza markdown.

BANDO: {titolo}
DESCRIZIONE: {descrizione[:800]}

Restituisci esattamente:
{{"fad": <0-100>, "accessibilita": <0-100>, "trend": <0-100>, "budget": <0-100>}}

Dove:
- fad: quanto il bando ammette/favorisce e-learning o FAD senza docente in presenza (0=solo presenza, 100=solo FAD)
- accessibilita: quanto è accessibile a privati/freelance senza accreditamento regionale (0=richiede accreditamento, 100=aperto a tutti)
- trend: quanto l'argomento è di tendenza (AI, digitale, green, competenze trasversali)
- budget: stima relativa del valore economico (0=piccolo, 100=molto grande)"""
    try:
        risposta = chiama_claude(prompt, max_tokens=100).strip()
        risposta = risposta.replace("```json", "").replace("```", "").strip()
        scores = json.loads(risposta)
        # Valida e normalizza
        for k in ["fad", "accessibilita", "trend", "budget"]:
            scores[k] = max(0, min(100, int(scores.get(k, 50))))
        return scores
    except Exception as e:
        print(f"  ⚠ Errore score AI: {e}")
        return {"fad": 50, "accessibilita": 50, "trend": 50, "budget": 50}

def calcola_score_totale(scores: dict) -> int:
    return round(
        scores["fad"] * 0.5 +
        scores["accessibilita"] * 0.2 +
        scores["trend"] * 0.2 +
        scores["budget"] * 0.1
    )

def estrai_tags(titolo: str, descrizione: str, scores: dict) -> list[str]:
    """Genera tag automatici dal contenuto e dallo score."""
    tags = []
    testo = (titolo + " " + descrizione).lower()
    if scores["fad"] >= 60:
        tags.append("FAD")
    if "pnrr" in testo:
        tags.append("PNRR")
    if "fse" in testo or "fse+" in testo:
        tags.append("FSE+")
    if "digitale" in testo or "digital" in testo or "ai" in testo:
        tags.append("Digitale")
    if "green" in testo or "sostenib" in testo:
        tags.append("Green")
    if "voucher" in testo:
        tags.append("Voucher")
    if "catalogo" in testo:
        tags.append("Catalogo")
    if "pmi" in testo or "piccole" in testo:
        tags.append("PMI")
    return tags

# ─── Gestione database ────────────────────────────────────────────────────────

def carica_bandi() -> list[dict]:
    """Carica il database bandi esistente."""
    if os.path.exists(BANDI_JSON):
        with open(BANDI_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_bandi(bandi: list[dict]):
    """Salva il database bandi ordinato per score."""
    bandi_ordinati = sorted(bandi, key=lambda b: b.get("score_totale", 0), reverse=True)
    with open(BANDI_JSON, "w", encoding="utf-8") as f:
        json.dump(bandi_ordinati, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Salvati {len(bandi_ordinati)} bandi in {BANDI_JSON}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"🕐 BandiMonitor Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📡 Feed configurati: {len(FEED_RSS)}")
    print(f"🤖 Modello AI: {ANTHROPIC_MODEL}\n")

    # Carica bandi esistenti
    bandi_esistenti = carica_bandi()
    ids_esistenti = {b["id"] for b in bandi_esistenti}
    print(f"📚 Bandi già nel database: {len(bandi_esistenti)}")

    nuovi_bandi = []
    totale_trovati = 0
    totale_rilevanti = 0

    for feed in FEED_RSS:
        print(f"\n📡 Leggo: {feed['nome']} ({feed['region']})")
        xml = fetch_rss(feed["url"])
        items = parse_rss(xml)
        print(f"   Trovati {len(items)} item nel feed")
        totale_trovati += len(items)

        for item in items:
            if not item["titolo"]:
                continue

            # Genera ID e controlla duplicati
            bid = genera_id(item["titolo"], item["url"])
            if bid in ids_esistenti:
                continue

            # Filtro rapido locale
            if not is_rilevante(item["titolo"], item["descrizione"]):
                continue

            print(f"   🔍 Possibile: {item['titolo'][:60]}…")

            # Verifica con AI
            if not verifica_rilevanza_ai(item["titolo"], item["descrizione"]):
                print(f"      ↳ Scartato dall'AI")
                continue

            totale_rilevanti += 1
            print(f"      ✅ Rilevante! Calcolo score…")

            # Calcola score
            scores = calcola_score_ai(item["titolo"], item["descrizione"])
            score_totale = calcola_score_totale(scores)
            tags = estrai_tags(item["titolo"], item["descrizione"], scores)

            bando = {
                "id": bid,
                "titolo": item["titolo"],
                "ente": feed["nome"],
                "fonte": f"RSS {feed['nome']}",
                "url": item["url"],
                "scadenza": "",
                "budget": "",
                "descrizione": item["descrizione"],
                "tags": tags,
                "score_fad": scores["fad"],
                "score_accessibilita": scores["accessibilita"],
                "score_trend": scores["trend"],
                "score_budget": scores["budget"],
                "score_totale": score_totale,
                "data_inserimento": date.today().isoformat(),
                "analisi_ai": None,
                "stato": "Attivo",
                "region": feed["region"]
            }

            nuovi_bandi.append(bando)
            ids_esistenti.add(bid)
            print(f"      Score: {score_totale}/100 | Tags: {', '.join(tags)}")

    # Rimuovi bandi scaduti (se hanno data scadenza)
    oggi = date.today().isoformat()
    bandi_attivi = [
        b for b in bandi_esistenti
        if not b.get("scadenza") or b["scadenza"] >= oggi or b["scadenza"] == ""
    ]
    rimossi = len(bandi_esistenti) - len(bandi_attivi)
    if rimossi > 0:
        print(f"\n🗑 Rimossi {rimossi} bandi scaduti")

    # Merge e salva
    tutti_bandi = bandi_attivi + nuovi_bandi
    salva_bandi(tutti_bandi)

    # Report finale
    print(f"\n📊 Report:")
    print(f"   Feed letti: {len(FEED_RSS)}")
    print(f"   Item totali trovati: {totale_trovati}")
    print(f"   Nuovi bandi rilevanti: {totale_rilevanti}")
    print(f"   Bandi nel database: {len(tutti_bandi)}")

if __name__ == "__main__":
    main()
