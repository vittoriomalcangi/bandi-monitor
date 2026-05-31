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
import http.client

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
BANDI_JSON = "bandi.json"

FEED_RSS = [
    {"nome": "Lombardia", "url": "https://www.regione.lombardia.it/wps/wcm/connect/DisegnoPRL/FeedRSS/stFeedRssBandi", "region": "Lombardia"},
    {"nome": "Emilia-Romagna", "url": "https://imprese.regione.emilia-romagna.it/Finanziamenti/RSS", "region": "Emilia-Romagna"},
    {"nome": "Campania", "url": "https://www.regione.campania.it/regione/it/feed-rss", "region": "Campania"},
    {"nome": "Lazio", "url": "https://www.regione.lazio.it/rss", "region": "Lazio"},
    {"nome": "Piemonte", "url": "https://bandi.regione.piemonte.it/contributi-finanziamenti?rss=1", "region": "Piemonte"},
    {"nome": "Puglia", "url": "http://formazione.regione.puglia.it/index.php?page=news&opz=readsch&id=1", "region": "Puglia"},
    {"nome": "Italia Domani (PNRR)", "url": "https://www.italiadomani.gov.it/content/sogei-ng/it/it/feed-rss/_jcr_content/par/accordion_container/accordion-item-0/column_container/par/accordion_item/column_container_copy/par/link_list_container/link_list/item0.stream/1638886579694/e1517d8d5b1d8d2dc1f7b01fd6b17ac5bddf18d0/bandi.xml", "region": "Nazionale PNRR"},
]

KEYWORDS_POSITIVI = [
    "formazione", "fad", "e-learning", "elearning", "corso", "corsi",
    "competenze", "digitale", "digitali", "fse", "fse+", "pnrr",
    "fondi interprofessionali", "voucher formativo", "upskilling",
    "reskilling", "aggiornamento professionale", "blended", "online",
    "apprendimento", "qualificazione", "riqualificazione"
]

KEYWORDS_NEGATIVI = [
    "appalto", "gara d'appalto", "lavori pubblici", "infrastrutture",
    "edilizia", "strade", "ponti", "rifiuti", "acqua potabile",
    "concorso pubblico", "selezione pubblica"
]

def genera_id(titolo, url):
    testo = f"{titolo}{url}".lower().strip()
    return hashlib.md5(testo.encode()).hexdigest()[:12]

def pulisci_testo(testo):
    if not testo:
        return ""
    testo = re.sub(r'<[^>]+>', ' ', testo)
    testo = re.sub(r'\s+', ' ', testo)
    return testo.strip()[:2000]

def fetch_rss(url, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BandiMonitor/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            # Prova UTF-8, poi latin-1
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return content.decode("latin-1", errors="replace")
    except Exception as e:
        print(f"  ⚠ Errore fetch {url}: {e}")
        return ""

def parse_rss(xml_text):
    items = []
    if not xml_text:
        return items
    try:
        # Rimuovi namespace per semplificare il parsing
        xml_clean = re.sub(r'\s+xmlns[^=]*="[^"]*"', '', xml_text)
        xml_clean = re.sub(r'<(\w+):[^>]*>', r'<\1>', xml_clean)
        xml_clean = re.sub(r'</(\w+):[^>]*>', r'</\1>', xml_clean)
        root = ET.fromstring(xml_clean)
        for item in root.iter("item"):
            titolo = pulisci_testo(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            desc = pulisci_testo(item.findtext("description") or item.findtext("summary") or "")
            if titolo:
                items.append({"titolo": titolo, "url": link, "descrizione": desc})
    except ET.ParseError as e:
        print(f"  ⚠ Errore XML: {e}")
    return items

def is_rilevante_locale(titolo, descrizione):
    testo = (titolo + " " + descrizione).lower()
    ha_pos = any(kw in testo for kw in KEYWORDS_POSITIVI)
    ha_neg = sum(1 for kw in KEYWORDS_NEGATIVI if kw in testo) >= 2
    return ha_pos and not ha_neg

def chiama_claude(prompt, max_tokens=300):
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

def verifica_rilevanza_ai(titolo, descrizione):
    prompt = f"""Rispondi SOLO con SI o NO.
Un bando è rilevante se riguarda formazione professionale erogabile online, e-learning, FAD, blended o video fruibile autonomamente senza docente in presenza.
TITOLO: {titolo}
DESCRIZIONE: {descrizione[:400]}
Rilevante?"""
    try:
        r = chiama_claude(prompt, max_tokens=5).strip().upper()
        return r.startswith("SI") or r == "SÌ"
    except Exception as e:
        print(f"  ⚠ Errore AI rilevanza: {e}")
        return False

def calcola_score_ai(titolo, descrizione):
    prompt = f"""Analizza questo bando italiano. Rispondi SOLO con JSON valido, nessun testo.
BANDO: {titolo}
DESCRIZIONE: {descrizione[:600]}
{{"fad":<0-100>,"accessibilita":<0-100>,"trend":<0-100>,"budget":<0-100>}}
fad=ammette e-learning senza docente, accessibilita=accessibile a privati senza accreditamento, trend=argomento di tendenza, budget=valore economico relativo"""
    try:
        r = chiama_claude(prompt, max_tokens=80).strip()
        r = r.replace("```json", "").replace("```", "").strip()
        scores = json.loads(r)
        for k in ["fad", "accessibilita", "trend", "budget"]:
            scores[k] = max(0, min(100, int(scores.get(k, 50))))
        return scores
    except Exception as e:
        print(f"  ⚠ Errore AI score: {e}")
        return {"fad": 50, "accessibilita": 50, "trend": 50, "budget": 50}

def calcola_totale(s):
    return round(s["fad"]*0.5 + s["accessibilita"]*0.2 + s["trend"]*0.2 + s["budget"]*0.1)

def estrai_tags(titolo, descrizione, scores):
    tags = []
    testo = (titolo + " " + descrizione).lower()
    if scores["fad"] >= 60: tags.append("FAD")
    if "pnrr" in testo: tags.append("PNRR")
    if "fse" in testo: tags.append("FSE+")
    if "digitale" in testo or "digital" in testo: tags.append("Digitale")
    if "green" in testo or "sostenib" in testo: tags.append("Green")
    if "voucher" in testo: tags.append("Voucher")
    if "pmi" in testo: tags.append("PMI")
    return tags

def carica_bandi():
    if os.path.exists(BANDI_JSON):
        with open(BANDI_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_bandi(bandi):
    # Deduplicazione per id prima di salvare
    seen = {}
    for b in bandi:
        seen[b["id"]] = b
    bandi_unici = sorted(seen.values(), key=lambda b: b.get("score_totale", 0), reverse=True)
    with open(BANDI_JSON, "w", encoding="utf-8") as f:
        json.dump(bandi_unici, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Salvati {len(bandi_unici)} bandi (deduplicati) in {BANDI_JSON}")

def main():
    print(f"🕐 BandiMonitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📡 Feed: {len(FEED_RSS)} | Modello: {ANTHROPIC_MODEL}\n")

    bandi_esistenti = carica_bandi()
    # Deduplicazione anche in fase di caricamento
    ids_esistenti = {b["id"] for b in bandi_esistenti}
    print(f"📚 Bandi nel database: {len(ids_esistenti)}")

    nuovi = []
    tot_items = 0
    tot_nuovi = 0

    for feed in FEED_RSS:
        print(f"\n📡 {feed['nome']}…")
        xml = fetch_rss(feed["url"])
        items = parse_rss(xml)
        print(f"   {len(items)} item nel feed")
        tot_items += len(items)

        for item in items:
            if not item["titolo"]:
                continue
            bid = genera_id(item["titolo"], item["url"])
            if bid in ids_esistenti:
                continue
            if not is_rilevante_locale(item["titolo"], item["descrizione"]):
                continue
            print(f"   🔍 {item['titolo'][:65]}…")
            if not verifica_rilevanza_ai(item["titolo"], item["descrizione"]):
                print(f"      ↳ Scartato dall'AI")
                continue
            tot_nuovi += 1
            scores = calcola_score_ai(item["titolo"], item["descrizione"])
            score_tot = calcola_totale(scores)
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
                "score_totale": score_tot,
                "data_inserimento": date.today().isoformat(),
                "analisi_ai": None,
                "stato": "Attivo",
                "region": feed["region"]
            }
            nuovi.append(bando)
            ids_esistenti.add(bid)
            print(f"      ✅ Score: {score_tot}/100 | Tags: {', '.join(tags)}")

    # Rimuovi scaduti
    oggi = date.today().isoformat()
    attivi = [b for b in bandi_esistenti if not b.get("scadenza") or b["scadenza"] >= oggi]
    rimossi = len(bandi_esistenti) - len(attivi)
    if rimossi:
        print(f"\n🗑 Rimossi {rimossi} bandi scaduti")

    salva_bandi(attivi + nuovi)

    print(f"\n📊 Riepilogo:")
    print(f"   Item RSS totali: {tot_items}")
    print(f"   Nuovi bandi aggiunti: {tot_nuovi}")
    print(f"   Database finale: {len(attivi) + len(nuovi)} bandi")

if __name__ == "__main__":
    main()
