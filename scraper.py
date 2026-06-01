#!/usr/bin/env python3
"""
BandiMonitor — Scraper RSS + Score AI
v8 — prompt B2B content provider, nuovi feed, keyword aggiornate
"""

import json
import os
import re
import time
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, date
from urllib.request import urlopen, Request
import http.client

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.1-8b-instant"
BANDI_JSON   = "bandi.json"

FEED_RSS = [
    # ── Aggregatori nazionali ──────────────────────────────────────────
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
        "nome": "Europa Innovazione — FSE",
        "url": "https://www.europainnovazione.com/tag/fse/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Europa Innovazione — PNRR",
        "url": "https://www.europainnovazione.com/tag/pnrr/feed/",
        "region": "Nazionale"
    },
    {
        "nome": "Formazione Finanziata",
        "url": "https://www.formazionefinanziata.com/feed/",
        "region": "Nazionale"
    },
    # ── Regioni ────────────────────────────────────────────────────────
    {
        "nome": "Europa Campania — FSE+ e Fondi Strutturali",
        "url": "https://europa.regione.campania.it/feed/",
        "region": "Campania"
    },
]

# Pre-filtro locale — solo bandi che passano questo test vengono mandati all'AI
# Riduce le chiamate API e i costi
KEYWORDS_POSITIVI = [
    "formazione", "fad", "e-learning", "elearning", "corso", "corsi",
    "competenze", "digitale", "digitali", "fse", "fse+", "pnrr",
    "fondi interprofessionali", "fondo interprofessionale",
    "voucher formativo", "voucher", "catalogo formativo",
    "upskilling", "reskilling", "aggiornamento professionale",
    "blended", "online", "apprendimento", "qualificazione",
    "riqualificazione", "avviso", "finanziamento formazione",
    "percorso formativo", "bando formazione", "ente di formazione",
    "società di formazione", "piano formativo", "fondimpresa",
    "fondirigenti", "fonarcom", "fon.ar.com", "for.te", "forte",
    "fondoprofessioni", "fondo nuove competenze", "fnc",
    "sicurezza sul lavoro", "soft skills", "intelligenza artificiale",
    "transizione digitale", "transizione ecologica"
]

KEYWORDS_NEGATIVI = [
    "appalto", "gara d'appalto", "lavori pubblici",
    "edilizia", "costruzione", "demolizione",
    "concorso pubblico", "selezione personale",
    "borse di studio", "dottorato", "assegno di ricerca"
]

# ─────────────────────────────────────────────
# Utilità
# ─────────────────────────────────────────────

def genera_id(titolo, url):
    return hashlib.md5(f"{titolo}{url}".lower().strip().encode()).hexdigest()[:12]

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
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
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
            link   = (item.findtext("link") or "").strip()
            desc   = pulisci_testo(item.findtext("description") or item.findtext("summary") or "")
            if titolo:
                items.append({"titolo": titolo, "url": link, "descrizione": desc})
        if not items:
            for entry in root.iter("entry"):
                titolo  = pulisci_testo(entry.findtext("title") or "")
                link_el = entry.find("link")
                link    = (link_el.get("href") if link_el is not None else "") or ""
                desc    = pulisci_testo(entry.findtext("summary") or entry.findtext("content") or "")
                if titolo:
                    items.append({"titolo": titolo, "url": link, "descrizione": desc})
    except ET.ParseError as e:
        print(f"  ⚠ XML parse error: {e}")
        titoli = re.findall(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', xml_text, re.DOTALL)
        links  = re.findall(r'<link[^>]*>(https?://[^<]+)</link>', xml_text)
        descs  = re.findall(r'<description[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', xml_text, re.DOTALL)
        for i, titolo in enumerate(titoli[1:], 0):
            titolo = pulisci_testo(titolo)
            if titolo and len(titolo) > 5:
                items.append({
                    "titolo": titolo,
                    "url": links[i] if i < len(links) else "",
                    "descrizione": pulisci_testo(descs[i]) if i < len(descs) else ""
                })
    return items

def is_rilevante_locale(titolo, descrizione):
    testo  = (titolo + " " + descrizione).lower()
    ha_pos = any(kw in testo for kw in KEYWORDS_POSITIVI)
    ha_neg = sum(1 for kw in KEYWORDS_NEGATIVI if kw in testo) >= 2
    return ha_pos and not ha_neg

# ─────────────────────────────────────────────
# Groq API
# ─────────────────────────────────────────────

def chiama_groq(prompt, max_tokens=200, retry=4):
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY non impostata")

    for tentativo in range(retry):
        body = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.1
        }).encode("utf-8")

        conn = http.client.HTTPSConnection("api.groq.com")
        conn.request("POST", "/openai/v1/chat/completions", body=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}"
        })
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()

        if "error" in data:
            err = data["error"]
            if err.get("code") == "rate_limit_exceeded":
                msg    = err.get("message", "")
                attesa = 5
                match  = re.search(r'try again in ([\d.]+)s', msg)
                if match:
                    attesa = float(match.group(1)) + 1.0
                print(f"  ⏳ TPM limit — attendo {attesa:.1f}s (tentativo {tentativo+1}/{retry})…")
                time.sleep(attesa)
                continue
            raise ValueError(f"Groq API error: {err}")

        time.sleep(3)
        return data["choices"][0]["message"]["content"]

    raise ValueError("Rate limit persistente dopo tutti i retry")

# ─────────────────────────────────────────────
# Prompt B2B — cuore del sistema
# ─────────────────────────────────────────────

def analizza_bando_ai(titolo, descrizione):
    """
    Valuta il bando dal punto di vista di un content provider B2B:
    produce corsi asincroni (video avatar, PDF, slide, LMS) da rivendere
    a società di formazione che partecipano ai bandi.
    """
    prompt = f"""Sei un analista per un'azienda che produce corsi di formazione asincroni (video con avatar AI, PDF, slide, piattaforme LMS) da rivendere a società di formazione che partecipano a bandi pubblici.

Analizza questo bando e rispondi SOLO con JSON valido, nessun testo extra, nessun markdown.

BANDO: {titolo}
DESCRIZIONE: {descrizione[:600]}

Valuta secondo questi 4 criteri:

1. "asincrono" (0-100): Il bando finanzia formazione erogabile tramite contenuti preregistrati acquistabili da catalogo (video, PDF, SCORM, LMS) senza docente live obbligatorio?
   - 100 = esplicitamente ammette FAD/e-learning asincrono o catalogo acquistabile
   - 50  = non specifica la modalità (potrebbe essere compatibile)
   - 0   = richiede docente in presenza obbligatorio o laboratorio fisico

2. "producibile" (0-100): L'argomento del bando è producibile con video+PDF senza esperti fisici presenti?
   - 100 = digitale, AI, soft skills, sicurezza normativa, compliance, lingue, contabilità, marketing
   - 50  = argomento generico o misto
   - 0   = richiede laboratorio fisico, simulatori hardware, tirocinio, chirurgia, guida veicoli

3. "mercato" (0-100): Quante società di formazione possono partecipare a questo bando?
   - 100 = fondo interprofessionale nazionale o bando aperto a tutti gli enti di formazione
   - 50  = bando regionale con accreditamento standard
   - 0   = riservato a enti specifici, università, PA, o soggetti con requisiti molto restrittivi

4. "timing" (0-100): Il bando è attuale e crea urgenza di acquisto contenuti?
   - 100 = bando appena aperto o in scadenza entro 60 giorni
   - 50  = bando aperto senza scadenza dichiarata
   - 0   = bando scaduto o con scadenza lontana oltre 6 mesi

Formato ESATTO (nessun testo prima o dopo):
{{"rilevante": true/false, "asincrono": 0-100, "producibile": 0-100, "mercato": 0-100, "timing": 0-100}}

"rilevante" = true SOLO se il bando può generare domanda di acquisto corsi asincroni da parte di società di formazione"""

    try:
        r = chiama_groq(prompt, max_tokens=100).strip()
        r = re.sub(r'```[a-z]*', '', r).strip().strip('`')
        match = re.search(r'\{[^}]+\}', r, re.DOTALL)
        if match:
            r = match.group(0)
        data = json.loads(r)

        rilevante = bool(data.get("rilevante", False))
        scores = {k: max(0, min(100, int(data.get(k, 50))))
                  for k in ["asincrono", "producibile", "mercato", "timing"]}
        return rilevante, scores

    except Exception as e:
        print(f"  ⚠ AI error: {e}")
        return False, {"asincrono": 50, "producibile": 50, "mercato": 50, "timing": 50}

# ─────────────────────────────────────────────
# Score e tag
# ─────────────────────────────────────────────

def calcola_totale(s):
    # Pesi: asincrono 50%, producibile 25%, mercato 15%, timing 10%
    return round(
        s["asincrono"]   * 0.50 +
        s["producibile"] * 0.25 +
        s["mercato"]     * 0.15 +
        s["timing"]      * 0.10
    )

def estrai_tags(titolo, descrizione, scores):
    tags  = []
    testo = (titolo + " " + descrizione).lower()

    # Modalità
    if scores["asincrono"] >= 70:                                           tags.append("FAD")
    if "blended" in testo:                                                  tags.append("Blended")

    # Finanziamento
    if "pnrr" in testo:                                                     tags.append("PNRR")
    if "fse" in testo:                                                      tags.append("FSE+")
    if any(f in testo for f in ["fondimpresa","fondirigenti","fonarcom",
                                 "for.te","forte","fondoprofessioni",
                                 "fondo nuove competenze","interprofessional"]): tags.append("Fondi Interprof.")

    # Argomento
    if any(k in testo for k in ["digitale","digital","intelligenza artificiale"," ai ","ict"]): tags.append("Digitale/AI")
    if any(k in testo for k in ["green","sostenib","ecolog","rinnovabil"]):  tags.append("Green")
    if any(k in testo for k in ["sicurezza sul lavoro","salute e sicurezza"]): tags.append("Sicurezza")
    if any(k in testo for k in ["soft skill","competenze trasversali","leadership","comunicazione"]): tags.append("Soft Skills")

    # Target
    if any(k in testo for k in ["pmi","piccole imprese","micro imprese"]):  tags.append("PMI")
    if any(k in testo for k in ["voucher","catalogo"]):                      tags.append("Voucher/Catalogo")

    return tags

# ─────────────────────────────────────────────
# Persistenza
# ─────────────────────────────────────────────

def carica_bandi():
    if os.path.exists(BANDI_JSON):
        with open(BANDI_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_bandi(bandi):
    seen     = {b["id"]: b for b in bandi}
    ordinati = sorted(seen.values(), key=lambda b: b.get("score_totale", 0), reverse=True)
    with open(BANDI_JSON, "w", encoding="utf-8") as f:
        json.dump(ordinati, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Salvati {len(ordinati)} bandi in {BANDI_JSON}")

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    print(f"🕐 BandiMonitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📡 Feed: {len(FEED_RSS)} | Modello: Groq {GROQ_MODEL}")
    print(f"🎯 Modalità: B2B content provider — corsi asincroni\n")

    bandi_esistenti = carica_bandi()
    ids_esistenti   = {b["id"] for b in bandi_esistenti}
    print(f"📚 Bandi nel database: {len(ids_esistenti)}")

    nuovi = []
    tot_items = tot_nuovi = feed_ok = 0

    for feed in FEED_RSS:
        print(f"\n📡 {feed['nome']}…")
        xml = fetch_url(feed["url"])
        if not xml:
            print("   ❌ Feed non raggiungibile")
            continue
        items = parse_feed(xml)
        if items:
            feed_ok += 1
            print(f"   ✅ {len(items)} item trovati")
        else:
            print("   ⚠ Nessun item parsato")
        tot_items += len(items)

        for item in items:
            if not item["titolo"]:
                continue
            bid = genera_id(item["titolo"], item["url"])
            if bid in ids_esistenti:
                continue
            # Pre-filtro locale — evita chiamate AI inutili
            if not is_rilevante_locale(item["titolo"], item["descrizione"]):
                continue

            print(f"   🔍 {item['titolo'][:65]}…")
            rilevante, scores = analizza_bando_ai(item["titolo"], item["descrizione"])

            if not rilevante:
                print("      ↳ Scartato dall'AI")
                continue

            tot_nuovi += 1
            score_tot  = calcola_totale(scores)
            tags       = estrai_tags(item["titolo"], item["descrizione"], scores)

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
                # Score B2B
                "score_asincrono":   scores["asincrono"],
                "score_producibile": scores["producibile"],
                "score_mercato":     scores["mercato"],
                "score_timing":      scores["timing"],
                "score_totale":      score_tot,
                # Campi legacy per compatibilità con app React esistente
                "score_fad":           scores["asincrono"],
                "score_accessibilita": scores["mercato"],
                "score_trend":         scores["producibile"],
                "score_budget":        scores["timing"],
                "data_inserimento": date.today().isoformat(),
                "stato": "Attivo",
                "region": feed["region"]
            }
            nuovi.append(bando)
            ids_esistenti.add(bid)
            print(f"      ✅ Score: {score_tot}/100 | Tags: {', '.join(tags)}")

    oggi    = date.today().isoformat()
    attivi  = [b for b in bandi_esistenti if not b.get("scadenza") or b["scadenza"] >= oggi]
    rimossi = len(bandi_esistenti) - len(attivi)
    if rimossi:
        print(f"\n🗑 Rimossi {rimossi} bandi scaduti")

    salva_bandi(attivi + nuovi)
    print(f"\n📊 Riepilogo:")
    print(f"   Feed funzionanti: {feed_ok}/{len(FEED_RSS)}")
    print(f"   Item RSS totali:  {tot_items}")
    print(f"   Nuovi bandi:      {tot_nuovi}")
    print(f"   Database finale:  {len(attivi) + len(nuovi)} bandi")

if __name__ == "__main__":
    main()
