"""
wyroki_cbosa.py — Scraper Centralnej Bazy Orzeczen Sadow Administracyjnych
(orzeczenia.nsa.gov.pl). Rdzen modulu Wyroki (modul 4 PickPivot).

ARCHITEKTURA (dlaczego tak):
  CBOSA nie ma API — to stara aplikacja formularzowa (HTML + sesja).
  Zapytanie = POST formularza, wyniki = stronicowane HTML (/cbo/find?p=N),
  szczegoly = /doc/<ID>. Dlatego:

  1. DYNAMICZNE ODKRYWANIE POL FORMULARZA: nie zgadujemy nazw inputow.
     Przy starcie pobieramy strone formularza i mapujemy pola po kluczach
     pomocy p3Help('klucz') stojacych przy kazdym polu (klucze sa stabilne:
     'symbole', 'data_orzeczenia', 'z_uzasadnieniem', 's_prawomocne'...).
     Dzieki temu zmiana nazw pol przez NSA nie psuje scrapera, o ile
     klucze pomocy zostana.

  2. SESJA: paginacja /cbo/find?p=N dziala w kontekscie sesji (cookie),
     w ktorej zyje ostatnie zapytanie — wszystko robimy na jednym
     requests.Session.

  3. TEMPO: celowo sekwencyjnie i powoli (PAUZA_S między zadaniami).
     To stary system publiczny — traktujemy go z szacunkiem. Zadnych
     watkow, zadnego rownoleglego pobierania.

SYMBOLE SPRAW (klasyfikacja sadowa) — filtr glowny:
  6110 = Podatek od towarow i uslug (VAT)   [potwierdzone na zywych danych]
  6112 = Podatek dochodowy od osob fizycznych (PIT)
  6113 = Podatek dochodowy od osob prawnych (CIT)
  6111 = Podatek akcyzowy (AKCYZA)
  Tryb kalibracji wypisuje opisy symboli z pobranych dokumentow — pierwsze
  uruchomienie zweryfikuje te mape na zywych danych.
"""

import re
import time
import requests
from bs4 import BeautifulSoup

BAZA_URL   = "https://orzeczenia.nsa.gov.pl"
QUERY_URL  = f"{BAZA_URL}/cbo/query"
FIND_URL   = f"{BAZA_URL}/cbo/find"
DOC_URL    = f"{BAZA_URL}/doc/{{id}}"

USER_AGENT = ("PickPivot-Archiwum/1.0 (prywatne archiwum doradcy podatkowego; "
              "kontakt przez repozytorium)")

PAUZA_S            = 1.5   # miedzy zadaniami HTTP — celowo wolno
TIMEOUT_S          = 30
MAKS_PROB_HTTP     = 3
PAUZA_PO_BLEDZIE_S = 20
MAKS_STRON         = 400   # bezpiecznik na wypadek petli paginacji

SYMBOLE_PODATKOW = {
    "VAT":    "6110",
    "AKCYZA": "6111",
    "PIT":    "6112",
    "CIT":    "6113",
}


class BladCBOSA(Exception):
    pass


# ---------------------------------------------------------------------------
# HTTP z ponowieniami
# ---------------------------------------------------------------------------
def _zadanie(sesja: requests.Session, metoda: str, url: str, log_fn=None, **kw):
    for proba in range(1, MAKS_PROB_HTTP + 1):
        try:
            time.sleep(PAUZA_S)
            r = sesja.request(metoda, url, timeout=TIMEOUT_S, **kw)
            if r.status_code == 200:
                return r
            if log_fn:
                log_fn(f"    HTTP {r.status_code} dla {url} (proba {proba})")
        except requests.RequestException as e:
            if log_fn:
                log_fn(f"    Blad polaczenia: {e} (proba {proba})")
        if proba < MAKS_PROB_HTTP:
            time.sleep(PAUZA_PO_BLEDZIE_S * proba)
    raise BladCBOSA(f"Nie udalo sie pobrac {url} po {MAKS_PROB_HTTP} probach")


def nowa_sesja() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


# ---------------------------------------------------------------------------
# DYNAMICZNE ODKRYWANIE POL FORMULARZA
# ---------------------------------------------------------------------------
def poznaj_formularz(sesja: requests.Session, log_fn=None) -> dict:
    """
    Pobiera strone formularza i buduje mape:
      {'akcja': url, 'domyslne': {nazwa: wartosc, ...},
       'pola': {klucz_pomocy: [ {name, type, value}, ... ]}}

    Kazde pole formularza CBOSA ma obok link pomocy p3Help('klucz') —
    szukamy inputow/selectow w tym samym wierszu tabeli co link.
    """
    r = _zadanie(sesja, "GET", QUERY_URL, log_fn=log_fn)
    soup = BeautifulSoup(r.text, "lxml")

    form = soup.find("form")
    if form is None:
        raise BladCBOSA("Nie znaleziono formularza na stronie /cbo/query")

    akcja = form.get("action") or "/cbo/query"
    if akcja.startswith("/"):
        akcja = BAZA_URL + akcja

    # Wartosci domyslne wszystkich pol (hidden itd.) — wysylamy je z powrotem,
    # zeby nie zgubic pol technicznych aplikacji.
    domyslne = {}
    for inp in form.find_all("input"):
        nazwa = inp.get("name")
        if not nazwa:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                domyslne[nazwa] = inp.get("value", "on")
        elif typ not in ("submit", "button", "image"):
            domyslne[nazwa] = inp.get("value", "")
    for sel in form.find_all("select"):
        nazwa = sel.get("name")
        if not nazwa:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        domyslne[nazwa] = opt.get("value", "") if opt else ""

    # Mapa klucz_pomocy -> pola w tym samym wierszu
    pola = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"p3Help\('(\w+)'\)", a["href"])
        if not m:
            continue
        klucz = m.group(1)
        wiersz = a.find_parent("tr") or a.find_parent("li") or a.parent
        if wiersz is None:
            continue
        lista = []
        for inp in wiersz.find_all(["input", "select"]):
            nazwa = inp.get("name")
            if not nazwa:
                continue
            lista.append({
                "name":  nazwa,
                "type":  (inp.get("type") or inp.name or "text").lower(),
                "value": inp.get("value", ""),
            })
        if lista and klucz not in pola:
            pola[klucz] = lista

    if log_fn:
        log_fn(f"  Formularz rozpoznany: akcja={akcja}, pol z kluczami pomocy: {len(pola)}")
    return {"akcja": akcja, "domyslne": domyslne, "pola": pola}


def _pole_tekstowe(formularz, klucz, ktore=0):
    lista = [p for p in formularz["pola"].get(klucz, []) if p["type"] in ("text", "")]
    if len(lista) > ktore:
        return lista[ktore]["name"]
    return None


def _radio_tak(formularz, klucz):
    """
    Dla wierszy statusu (Tak/Nie) zwraca (nazwa, wartosc) PIERWSZEGO radia
    w wierszu — w formularzu CBOSA kolejnosc to zawsze Tak, potem Nie.
    """
    radia = [p for p in formularz["pola"].get(klucz, []) if p["type"] == "radio"]
    if radia:
        return radia[0]["name"], radia[0]["value"] or "on"
    # fallback: checkbox
    chk = [p for p in formularz["pola"].get(klucz, []) if p["type"] == "checkbox"]
    if chk:
        return chk[0]["name"], chk[0]["value"] or "on"
    return None, None


def zbuduj_zapytanie(formularz: dict, symbol: str, data_od: str, data_do: str,
                     tylko_z_uzasadnieniem: bool = False,
                     tylko_prawomocne: bool = False) -> dict:
    """Sklada slownik danych POST dla zapytania o dany symbol i okno dat."""
    dane = dict(formularz["domyslne"])

    n_symbol = _pole_tekstowe(formularz, "symbole")
    if not n_symbol:
        raise BladCBOSA("Nie rozpoznano pola 'symbole' w formularzu")
    dane[n_symbol] = symbol

    n_od = _pole_tekstowe(formularz, "data_orzeczenia", 0)
    n_do = _pole_tekstowe(formularz, "data_orzeczenia", 1)
    if not n_od or not n_do:
        raise BladCBOSA("Nie rozpoznano pol dat w formularzu")
    dane[n_od] = data_od
    dane[n_do] = data_do

    if tylko_z_uzasadnieniem:
        n, v = _radio_tak(formularz, "z_uzasadnieniem")
        if n:
            dane[n] = v
    if tylko_prawomocne:
        n, v = _radio_tak(formularz, "s_prawomocne")
        if n:
            dane[n] = v
    return dane


# ---------------------------------------------------------------------------
# WYSZUKIWANIE + PAGINACJA
# ---------------------------------------------------------------------------
_RE_DOC = re.compile(r"/doc/([0-9A-Fa-f]{6,})")
_RE_LICZBA = [
    re.compile(r"Znaleziono[:\s]+([\d\s\u00a0]+)\s*orzecze", re.I),
    re.compile(r"znalezion\w+\s+dokument\w*[:\s]+([\d\s\u00a0]+)", re.I),
    re.compile(r"Ilo\w+\s+znalezionych[^\d]*([\d\s\u00a0]+)", re.I),
]


def _parsuj_liste(html: str):
    """Z listy wynikow wyciaga [(id, tytul), ...] oraz laczna liczbe (lub None)."""
    soup = BeautifulSoup(html, "lxml")
    wyniki, widziane = [], set()
    for a in soup.find_all("a", href=True):
        m = _RE_DOC.search(a["href"])
        if not m:
            continue
        did = m.group(1).upper()
        if did in widziane:
            continue
        widziane.add(did)
        wyniki.append((did, a.get_text(" ", strip=True)))

    tekst = soup.get_text(" ", strip=True)
    total = None
    for rx in _RE_LICZBA:
        m = rx.search(tekst)
        if m:
            try:
                total = int(re.sub(r"[\s\u00a0]", "", m.group(1)))
                break
            except ValueError:
                pass
    return wyniki, total


def szukaj(sesja: requests.Session, formularz: dict, symbol: str,
           data_od: str, data_do: str, tylko_z_uzasadnieniem=False,
           tylko_prawomocne=False, log_fn=None):
    """
    Wykonuje zapytanie i przewija WSZYSTKIE strony wynikow.
    Zwraca (lista_[(id, tytul)], total_hits_lub_None).
    """
    dane = zbuduj_zapytanie(formularz, symbol, data_od, data_do,
                            tylko_z_uzasadnieniem, tylko_prawomocne)
    r = _zadanie(sesja, "POST", formularz["akcja"], log_fn=log_fn, data=dane)
    wyniki, total = _parsuj_liste(r.text)
    if log_fn:
        t = f"/{total}" if total is not None else ""
        log_fn(f"    [strona 1] {len(wyniki)} pozycji{(' (lacznie ' + str(len(wyniki)) + t + ')') if total else ''}")

    wszystkie = list(wyniki)
    widziane = {i for i, _ in wszystkie}
    strona = 2
    while strona <= MAKS_STRON:
        if total is not None and len(wszystkie) >= total:
            break
        if not wyniki:            # pusta strona = koniec
            break
        r = _zadanie(sesja, "GET", f"{FIND_URL}?p={strona}", log_fn=log_fn)
        wyniki, _ = _parsuj_liste(r.text)
        nowe = [(i, t) for i, t in wyniki if i not in widziane]
        if not nowe:              # strona bez nowych pozycji = koniec/petla
            break
        for i, t in nowe:
            widziane.add(i)
        wszystkie.extend(nowe)
        if log_fn:
            log_fn(f"    [strona {strona}] +{len(nowe)} (lacznie {len(wszystkie)}"
                   + (f"/{total}" if total is not None else "") + ")")
        strona += 1

    return wszystkie, total


# ---------------------------------------------------------------------------
# PARSER STRONY SZCZEGOLOW /doc/<ID>
# ---------------------------------------------------------------------------
_ETYKIETY = ["Data orzeczenia", "Data wpływu", "Sąd", "Sędziowie",
             "Symbol z opisem", "Hasła tematyczne", "Sygn. powiązane",
             "Skarżony organ", "Treść wyniku", "Powołane przepisy"]


def _tekst_po_etykiecie(soup, etykieta):
    """Znajduje komorke z dokladna etykieta i zwraca tekst sasiedniej tresci."""
    for tag in soup.find_all(["td", "th", "span", "b", "strong"]):
        if tag.get_text(strip=True) == etykieta:
            wiersz = tag.find_parent("tr")
            if wiersz:
                kom = wiersz.find_all("td")
                if len(kom) >= 2:
                    return kom[-1].get_text("\n", strip=True)
            nastepny = tag.find_next_sibling()
            if nastepny:
                return nastepny.get_text("\n", strip=True)
    return ""


def _sekcja_tresci(soup, naglowek):
    """Sekcje 'Sentencja'/'Uzasadnienie' — etykieta i tresc w jednym bloku."""
    for tag in soup.find_all(["td", "span", "b", "strong", "div"]):
        t = tag.get_text(strip=True)
        if t == naglowek:
            wiersz = tag.find_parent("tr") or tag.parent
            if wiersz:
                tekst = wiersz.get_text("\n", strip=True)
                if tekst.startswith(naglowek):
                    tekst = tekst[len(naglowek):].strip()
                if len(tekst) > 20:
                    return tekst
                # tresc moze byc w nastepnym wierszu
                nast = wiersz.find_next_sibling("tr")
                if nast:
                    return nast.get_text("\n", strip=True)
    return ""


def mapuj_symbol_na_podatek(symbole_tekst: str) -> str:
    for podatek, sym in SYMBOLE_PODATKOW.items():
        if sym in (symbole_tekst or ""):
            return podatek
    return ""


def pobierz_szczegoly(sesja: requests.Session, doc_id: str, log_fn=None) -> dict:
    """Pobiera i parsuje strone /doc/<ID>. Zwraca slownik gotowy do zapisu w bazie."""
    r = _zadanie(sesja, "GET", DOC_URL.format(id=doc_id), log_fn=log_fn)
    soup = BeautifulSoup(r.text, "lxml")

    # Tytul: "I FSK 45/21 - Wyrok NSA z 2024-09-25"
    tytul = (soup.title.get_text(strip=True) if soup.title else "")
    sygnatura, rodzaj, sad_krotki, data = "", "", "", ""
    m = re.match(r"(.+?)\s*-\s*(Wyrok|Postanowienie|Uchwa\w+)\s+(.+?)\s+z\s+(\d{4}-\d{2}-\d{2})", tytul)
    if m:
        sygnatura, rodzaj, sad_krotki, data = m.group(1), m.group(2), m.group(3), m.group(4)

    pelny_tekst = soup.get_text(" ", strip=True)
    prawomocny = "orzeczenie prawomocne" in pelny_tekst.lower()

    dane_meta = {et: _tekst_po_etykiecie(soup, et) for et in _ETYKIETY}
    sentencja    = _sekcja_tresci(soup, "Sentencja")
    uzasadnienie = _sekcja_tresci(soup, "Uzasadnienie")

    symbole = dane_meta.get("Symbol z opisem", "")
    data_orz = dane_meta.get("Data orzeczenia", "") or data
    m_data = re.search(r"\d{4}-\d{2}-\d{2}", data_orz)
    data_orz = m_data.group(0) if m_data else data

    return {
        "id":              doc_id.upper(),
        "sygnatura":       sygnatura or dane_meta.get("Sygn. powiązane", "")[:60],
        "rodzaj":          rodzaj,
        "sad":             dane_meta.get("Sąd", "") or sad_krotki,
        "data_orzeczenia": data_orz,
        "podatek":         mapuj_symbol_na_podatek(symbole),
        "symbole":         symbole,
        "hasla":           dane_meta.get("Hasła tematyczne", ""),
        "skarzony_organ":  dane_meta.get("Skarżony organ", ""),
        "tresc_wyniku":    dane_meta.get("Treść wyniku", ""),
        "prawomocny":      prawomocny,
        "sentencja":       sentencja,
        "uzasadnienie":    uzasadnienie,
        "przepisy":        dane_meta.get("Powołane przepisy", ""),
        "sygn_powiazane":  dane_meta.get("Sygn. powiązane", ""),
        "link":            DOC_URL.format(id=doc_id.upper()),
        "status_tresci":   ("KOMPLETNY" if len(uzasadnienie) > 100
                            else "OCZEKUJE_NA_UZASADNIENIE"),
    }
