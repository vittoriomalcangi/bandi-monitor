#!/usr/bin/env python3
"""
BandiMonitor — Scraper RSS + Score AI (Gemini API - gratuita)
"""

import json
import os
import re
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, date
from urllib.request import urlopen, Request
import http.client

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-lite"
BANDI_JSON = "bandi.json"

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
        "nome": "Obiettivo Europa — Formazione e Lavoro",
        "url": "https://www.obiettivoeuropa.com/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Talentform — Formazione Finanziata",
        "url": "https://www.talentform.it/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Lavoro.gov.it — Notizie",
        "url": "https://www.lavoro.gov.it/feed",
        "region": "Nazionale"
    },
    {
        "nome": "ItaliaOggi — Formazione",
        "url": "https://www.italiaoggi.it/rss/formazione",
        "region": "Nazionale"
    },
]

KEYWORDS_POSITIVI = [
    "formazione", "fad", "e-learning", "elearning", "corso", "corsi",
    "competenze", "digitale", "digitali", "fse", "fse+", "pnrr",
    "fondi interprofessionali", "voucher formativo", "upskilling",
    "reskilling", "aggiornamento professionale", "blended", "online",
    "apprendimento", "qualificazione", "riqualificazione", "avviso",
    "finanziamento formazione", "percorso formativo", "bando formazione"
]

KEYWORDS_NEGATIVI = [
    "appalto", "gara d'appalto", "lavori pubblici",
    "edilizia", "costruzione", "demolizione",
    "concorso pubblico dipendenti", "selezione personale"
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
    items = []
    if not xml_text or len(xml_text) < 50:
        return items
    try:
        xml_clean = re.sub(r' xmlns[^=]*="[^"]*"', '', xml_text)
        xml_clean = re.sub(r'<([a-zA-Z]+):([a-zA-Z]+)', r'<\2', xml_clean)
        xml_clean = re.sub(r'</([a-zA-Z]+):([a-zA-Z]+)', r'</\2', xml_clean)
        root = ET.fromstring(xml_clean)

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
        # Fallback regex per feed malformati
        titoli = re.findall(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', xml_text, re.DOTALL)
        links = re.findall(r'<link[^>]*>(https?://[^<]+)</link>', xml_text)
        descs = re.findall(r'<description[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', xml_text, re.DOTALL)
        for i, titolo in enumerate(titoli[1:], 0):
            titolo = pulisci_testo(titolo)
            if titolo and len(titolo) > 5:
                link = links[i] if i < len(links) else ""
                desc = pulisci_testo(descs[i]) if i < len(descs) else ""
                items.append({"titolo": titolo, "url": link, "descrizione": desc})

    return items

def is_rilevante_locale(titolo, descrizione):
    testo = (titolo + " " + descrizione).lower()
    ha_pos = any(kw in testo for kw in KEYWORDS_POSITIVI)
    ha_neg = sum(1 for kw in KEYWORDS_NEGATIVI if kw in testo) >= 2
    return ha_pos and not ha_neg

def chiama_gemini(prompt, max_tokens=300):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY non impostata")
    
    url = f"/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1}
    }).encode("utf-8")
    
    conn = http.client.HTTPSConnection("generativelanguage.googleapis.com")
    conn.request("POST", url, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()
    
    if "error" in data:
        raise ValueError(f"Gemini API error: {data['error']}")
    
    return data["candidates"][0]["content"]["parts"][0]["text"]

def verifica_rilevanza_ai(titolo, descrizione):
    prompt = f"""Rispondi SOLO con SI o NO.
Un bando è rilevante se riguarda formazione professionale erogabile in modalità e-learning, FAD, online, blended, video o materiale digitale fruibile autonomamente senza docente in presenza obbligatoria.
TITOLO: {titolo}
DESCRIZIONE: {descrizione[:400]}
Rilevante?"""
    try:
        r = chiama_gemini(prompt, max_tokens=5).strip().upper()
        return r.startswith("SI") or r == "SÌ" or r.startswith("YES")
    except Exception as e:
        print(f"  ⚠ AI relevance error: {e}")
        return False

def calcola_score_ai(titolo, descrizione):
    prompt = f"""Analizza questo bando italiano. Rispondi SOLO con JSON valido, nessun testo extra, nessun markdown.
BANDO: {titolo}
DESCRIZIONE: {descrizione[:600]}
Formato richiesto: {{"fad":<0-100>,"accessibilita":<0-100>,"trend":<0-100>,"budget":<0-100>}}
fad=ammette e-learning senza docente (0=solo presenza, 100=solo FAD)
accessibilita=accessibile a privati senza accreditamento (0=richiede accreditamento, 100=aperto a tutti)
trend=argomento di tendenza AI/digitale/green (0=obsoleto, 100=molto trendy)
budget=valore economico relativo (0=piccolo, 100=molto grande)"""
    try:
        r = chiama_gemini(prompt, max_tokens=80).strip()
        r = re.sub(r'```[a-z]*', '', r).strip().strip('`')
        # Estrai solo il JSON se c'è testo intorno
        match = re.search(r'\{[^}]+\}', r)
        if match:
            r = match.group(0)
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
    if "digitale" in testo or "digital" in testo or " ai " in testo: tags.append("Digitale")
    if "green" in testo or "sostenib" in testo: tags.append("Green")
    if "voucher" in testo: tags.append("Voucher")
    if "pmi" in testo or "piccole imprese" in testo: tags.append("PMI")
    if "interprofessional" in testo: tags.append("Fondi Interprof.")
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
    print(f"📡 Feed: {len(FEED_RSS)} | Modello: Gemini {GEMINI_MODEL} (gratuito)\n")

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
            print(f"   ⚠ Nessun item parsato")
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
