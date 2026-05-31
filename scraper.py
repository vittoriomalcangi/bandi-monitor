#!/usr/bin/env python3
"""
BandiMonitor — Scraper RSS + Score AI
Usa aggregatori WordPress (feed stabili) + Gazzetta Ufficiale
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

# Feed RSS verificati e stabili — tutti WordPress o istituzionali con feed standard
FEED_RSS = [
    {
        "nome": "Europa Innovazione — Bandi Nazionali",
        "url": "https://www.europainnovazione.com/category/bandi-nazionali/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Europa Innovazione — Bandi Europei",
        "url": "https://www.europainnovazione.com/category/bandi-europei/feed/",
        "region": "Europeo"
    },
    {
        "nome": "Europa Innovazione — Formazione",
        "url": "https://www.europainnovazione.com/tag/formazione/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Gazzetta Ufficiale — Serie Generale",
        "url": "https://www.gazzettaufficiale.it/rss/homepage.jsp",
        "region": "Nazionale"
    },
    {
        "nome": "Fondimpresa",
        "url": "https://www.fondimpresa.it/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Incentivimpresa — Formazione",
        "url": "https://www.incentivimpresa.it/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Bandi e Finanziamenti IT",
        "url": "https://www.agevolazioni.net/feed/",
        "region": "Nazionale"
    },
]

KEYWORDS_POSITIVI = [
    "formazione", "fad", "e-learning", "elearning", "corso", "corsi",
    "competenze", "digitale", "digitali", "fse", "fse+", "pnrr",
    "fondi interprofessionali", "voucher formativo", "upskilling",
    "reskilling", "aggiornamento professionale", "blended", "online",
    "apprendimento", "qualificazione", "riqualificazione", "avviso",
    "finanziamento formazione", "percorso formativo"
]

KEYWORDS_NEGATIVI = [
    "appalto", "gara d'appalto", "lavori pubblici", "infrastrutture stradali",
    "edilizia", "costruzione", "demolizione", "smaltimento rifiuti",
    "concorso pubblico dipendenti", "selezione pubblica personale"
]

def genera_id(titolo, url):
    testo = f"{titolo}{url}".lower().strip()
    return hashlib.md5(testo.encode()).hexdigest()[:12]

def pulisci_testo(testo):
    if not testo:
        return ""
    testo = re.sub(r'<[^>]+>', ' ', testo)
    testo = re.sub(r'&[a-z]+;', ' ', testo)
    testo = re.sub(r'\s+', ' ', testo)
    return testo.strip()[:2000]

def fetch_url(url, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 BandiMonitor/1.0",
        "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml, */*"
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            for enc in ["utf-8", "latin-1", "iso-8859-1"]:
                try:
                    return content.decode(enc)
                except UnicodeDecodeError:
                    continue
            return content.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ Fetch error: {e}")
        return ""

def parse_feed(xml_text):
    """Parsing robusto che supporta RSS 2.0 e Atom."""
    items = []
    if not xml_text or len(xml_text) < 50:
        return items
    try:
        # Pulizia namespace per semplificare
        xml_clean = re.sub(r' xmlns[^=]*="[^"]*"', '', xml_text)
        # Rimuovi prefissi namespace ma mantieni il tag
        xml_clean = re.sub(r'<([a-zA-Z]+):([a-zA-Z]+)', r'<\2', xml_clean)
        xml_clean = re.sub(r'</([a-zA-Z]+):([a-zA-Z]+)', r'</\2', xml_clean)
        root = ET.fromstring(xml_clean)

        # RSS 2.0
        for item in root.iter("item"):
            titolo = pulisci_testo(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            desc = pulisci_testo(
                item.findtext("description") or
                item.findtext("summary") or
                item.findtext("content") or ""
            )
            if titolo:
                items.append({"titolo": titolo, "url": link, "descrizione": desc})

        # Atom (entry invece di item)
        if not items:
            for entry in root.iter("entry"):
                titolo = pulisci_testo(entry.findtext("title") or "")
                link_el = entry.find("link")
                link = (link_el.get("href") if link_el is not None else "") or ""
                desc = pulisci_testo(
                    entry.findtext("summary") or
                    entry.findtext("content") or ""
                )
                if titolo:
                    items.append({"titolo": titolo, "url": link, "descrizione": desc})

    except ET.ParseError as e:
        print(f"  ⚠ XML parse error: {e}")
        # Fallback: estrazione regex per feed malformati
        titoli = re.findall(r'<title[^>]*><!\[CDATA\[(.*?)\]\]></title>|<title[^>]*>(.*?)</title>', xml_text, re.DOTALL)
        links = re.findall(r'<link[^>]*>(https?://[^<]+)</link>', xml_text)
        descs = re.findall(r'<description[^>]*><!\[CDATA\[(.*?)\]\]></description>|<description[^>]*>(.*?)</description>', xml_text, re.DOTALL)
        for i, (t1, t2) in enumerate(titoli[1:], 0):  # Salta il primo (titolo del feed)
            titolo = pulisci_testo(t1 or t2)
            if titolo and len(titolo) > 5:
                link = links[i] if i < len(links) else ""
                d1, d2 = descs[i] if i < len(descs) else ("", "")
                items.append({"titolo": titolo, "url": link, "descrizione": pulisci_testo(d1 or d2)})

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
        raise ValueError(f"API error: {data['error']}")
    return data["content"][0]["text"]

def verifica_rilevanza_ai(titolo, descrizione):
    prompt = f"""Rispondi SOLO con SI o NO.
Un bando è rilevante se riguarda formazione professionale erogabile in modalità e-learning, FAD, online, blended, video o materiale digitale fruibile autonomamente senza docente in presenza obbligatoria.
TITOLO: {titolo}
DESCRIZIONE: {descrizione[:400]}
Rilevante?"""
    try:
        r = chiama_claude(prompt, max_tokens=5).strip().upper()
        return r.startswith("SI") or r == "SÌ"
    except Exception as e:
        print(f"  ⚠ AI relevance error: {e}")
        return False

def calcola_score_ai(titolo, descrizione):
    prompt = f"""Analizza questo bando italiano. Rispondi SOLO con JSON valido, nessun testo extra.
BANDO: {titolo}
DESCRIZIONE: {descrizione[:600]}
{{"fad":<0-100>,"accessibilita":<0-100>,"trend":<0-100>,"budget":<0-100>}}
fad=ammette e-learning senza docente, accessibilita=accessibile a privati senza accreditamento, trend=argomento di tendenza AI/digitale/green, budget=valore economico relativo"""
    try:
        r = chiama_claude(prompt, max_tokens=80).strip()
        r = re.sub(r'```[a-z]*', '', r).strip()
        scores = json.loads(r)
        for k in ["fad", "accessibilita", "trend", "budget"]:
            scores[k] = max(0, min(100, int(scores.get(k, 50))))
        return scores
    except Exception as e:
        print(f"  ⚠ AI score error: {e}")
        return {"fad": 50, "accessibilita": 50, "trend": 50, "budget": 50}

def calcola_totale(s):
    return round(s["fad"]*0.5 + s["accessibilita"]*0.2 + s["trend"]*0.2 + s["budget"]*0.1)

def estrai_tags(titolo, descrizione, scores):
    tags = []
    testo = (titolo + " " + descrizione).lower()
    if scores["fad"] >= 60: tags.append("FAD")
    if "pnrr" in testo: tags.append("PNRR")
    if "fse" in testo: tags.append("FSE+")
    if "digitale" in testo or "digital" in testo or "ai" in testo: tags.append("Digitale")
    if "green" in testo or "sostenib" in testo: tags.append("Green")
    if "voucher" in testo: tags.append("Voucher")
    if "pmi" in testo or "piccole imprese" in testo: tags.append("PMI")
    if "interprofessional" in testo: tags.append("Fondi Interprofessionali")
    return tags

def carica_bandi():
    if os.path.exists(BANDI_JSON):
        with open(BANDI_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_bandi(bandi):
    seen = {}
    for b in bandi:
        seen[b["id"]] = b
    ordinati = sorted(seen.values(), key=lambda b: b.get("score_totale", 0), reverse=True)
    with open(BANDI_JSON, "w", encoding="utf-8") as f:
        json.dump(ordinati, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Salvati {len(ordinati)} bandi in {BANDI_JSON}")

def main():
    print(f"🕐 BandiMonitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📡 Feed: {len(FEED_RSS)} | Modello: {ANTHROPIC_MODEL}\n")

    bandi_esistenti = carica_bandi()
    ids_esistenti = {b["id"] for b in bandi_esistenti}
    print(f"📚 Bandi nel database: {len(ids_esistenti)}")

    nuovi = []
    tot_items = 0
    tot_nuovi = 0
    feed_ok = 0

    for feed in FEED_RSS:
        print(f"\n📡 {feed['nome']}…")
        xml = fetch_url(feed["url"])
        if not xml:
            print(f"   ❌ Feed non raggiungibile")
            continue
        items = parse_feed(xml)
        if items:
            feed_ok += 1
            print(f"   ✅ {len(items)} item trovati")
        else:
            print(f"   ⚠ Nessun item parsato (feed potrebbe essere vuoto o malformato)")
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
                "fonte": feed["nome"],
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
    print(f"   Feed funzionanti: {feed_ok}/{len(FEED_RSS)}")
    print(f"   Item RSS totali: {tot_items}")
    print(f"   Nuovi bandi aggiunti: {tot_nuovi}")
    print(f"   Database finale: {len(attivi) + len(nuovi)} bandi")

if __name__ == "__main__":
    main()
