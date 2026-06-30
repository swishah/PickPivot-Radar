import os
import json
import re
import time
import requests
import io
import concurrent.futures
import threading
import PyPDF2
from datetime import datetime

# ---------------------------------------------------------------------------
# ŚCIEŻKI I STAŁE
# ---------------------------------------------------------------------------
FOLDER_DOCELOWY = 'PickPivot_Data'
if not os.path.exists(FOLDER_DOCELOWY):
    os.makedirs(FOLDER_DOCELOWY)

PLIK_KONFIGURACJI_M1 = f"{FOLDER_DOCELOWY}/historia_m1.json"
PLIK_REKORDOW_M1     = f"{FOLDER_DOCELOWY}/baza_tresci_m1.json"
PLIK_KONFIGURACJI_M2 = f"{FOLDER_DOCELOWY}/historia_m2.json"
PLIK_REKORDOW_M2     = f"{FOLDER_DOCELOWY}/baza_tresci_m2.json"

SEARCH_API_URL_BASE = (
    "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/"
    "?size=25&page={page}&sort=parametryPozycjonowania%2Casc"
)
PDF_API_URL   = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"
# ► NOWE: endpoint HTML (lżejszy niż PDF, bez renderowania)
HTML_API_URL  = "https://eureka.mf.gov.pl/informacje/podglad/{id}"
PODGLAD_URL   = "https://eureka.mf.gov.pl/informacje/podglad/{id}"

FRAZY_KLUCZOWE = [
    "sieć ciepłownicza", "przebudowa sieci", "przyłącze", "węzeł cieplny",
    "taryfa dla ciepła", "wodociąg", "kanalizacja", "oczyszczalnia ścieków",
    "stacja uzdatniania", "spółka komunalna"
]

# UWAGA: ten slownik byl wczesniej (blednie) uzywany jako filtr API przez
# sprawdzanie podciagu w polu SYG. NIE jest to prawdziwy mechanizm filtrowania
# uzywany przez API MF - prawdziwy filtr to KODY_PRZEPISOW ponizej. Ten slownik
# zostaje tylko jako pomocniczy (np. do wyswietlania prefiksu w UI), ale nigdy
# nie powinien byc przekazywany do pobierz_wszystko_z_okresu / szukaj_w_api_mf.
KODY_PODATKOW = {"PIT": ".4011.", "CIT": ".4010.", "VAT": ".4012.", "AKCYZA": ".4013."}

# ► KLUCZOWE: prawdziwy filtr uzywany przez strone eureka.mf.gov.pl do
# zawezania wynikow do konkretnego podatku. To NIE jest filtrowanie po
# sygnaturze (jak bylo wczesniej) tylko po wewnetrznym ID aktu prawnego
# w slowniku MF. Wartosci znalezione przez podsluchanie zapytan sieciowych
# prawdziwej wyszukiwarki (DevTools -> Network -> Payload -> filter.PRZEPISY).
KODY_PRZEPISOW = {
    "PIT":    29903,
    "VAT":    29955,
    "CIT":    29985,
    "AKCYZA": 38830,
}

MIESIACE_PL   = [
    "Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
    "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"
]

# ---------------------------------------------------------------------------
# USTAWIENIA SZYBKOŚCI
# ---------------------------------------------------------------------------
# Liczba równoległych wątków pobierających PDF/HTML
WORKERS_POBIERANIA = 5          # ► NOWE: równoległość zamiast seq.
OPOZNIENIE_MIN     = 0.6        # ► ZMNIEJSZONE z 1.5 s
OPOZNIENIE_MAX     = 1.2        # ► ZMNIEJSZONE z 2.5 s
TIMEOUT_PDF        = 15         # sekund (było 20)
TIMEOUT_HTML       = 10

# Blokada do bezpiecznego dostępu do listy wyników z wątków
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# FUNKCJE BAZODANOWE
# ---------------------------------------------------------------------------
def wczytaj_historie(plik):
    if os.path.exists(plik):
        with open(plik, 'r', encoding='utf-8') as f:
            dane = json.load(f)
            dane.setdefault("uszkodzone_id", [])
            dane.setdefault("ukonczone_kombinacje", [])
            return dane
    return {"przetworzone_id": [], "ukonczone_kombinacje": [], "uszkodzone_id": []}

def zapisz_historie(plik, konfiguracja):
    with open(plik, 'w', encoding='utf-8') as f:
        json.dump(konfiguracja, f, ensure_ascii=False, indent=4)

def wczytaj_pelne_tresci(plik):
    if os.path.exists(plik):
        with open(plik, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def zapisz_pelne_tresci(plik, lista_rekordow):
    with open(plik, 'w', encoding='utf-8') as f:
        json.dump(lista_rekordow, f, ensure_ascii=False, indent=4)

def wyczysc_dane_serwera(plik_konf, plik_rekordow):
    if os.path.exists(plik_konf):    os.remove(plik_konf)
    if os.path.exists(plik_rekordow): os.remove(plik_rekordow)

def wyczysc_tekst_dla_worda(tekst):
    if not tekst: return ""
    # Usuwa znaki niedozwolone w XML/docx
    return re.sub(
        r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]',
        '', tekst
    )

# ---------------------------------------------------------------------------
# ► NOWE: EKSTRAKCJA TEKSTU Z HTML (szybsza niż PDF)
# ---------------------------------------------------------------------------
def _pobierz_tekst_html(id_dokumentu, sesja=None):
    """
    Pobiera stronę HTML podglądu i wyciąga czysty tekst.
    Zwraca (tekst, status).  Status: "OK" | "BRAK_PLIKU" | "BLOKADA"
    """
    from bs4 import BeautifulSoup
    url     = HTML_API_URL.format(id=id_dokumentu)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
    caller  = sesja if sesja else requests
    try:
        r = caller.get(url, headers=headers, timeout=TIMEOUT_HTML)
        if r.status_code == 200:
            soup  = BeautifulSoup(r.text, 'lxml')
            # Usuwa nagłówki nawigacyjne, zostawia treść dokumentu
            for tag in soup.select("nav, header, footer, script, style, .breadcrumb"):
                tag.decompose()
            tekst = soup.get_text(separator="\n", strip=True)
            # Jeśli tekst jest bardzo krótki — strona błędu
            if len(tekst) < 200:
                return None, "BRAK_PLIKU"
            return tekst, "OK"
        elif r.status_code in (404, 400):
            return None, "BRAK_PLIKU"
        elif r.status_code == 429:
            return None, "BLOKADA"
        else:
            return None, "BLOKADA"
    except Exception:
        return None, "BLOKADA"

# ---------------------------------------------------------------------------
# EKSTRAKCJA TEKSTU Z PDF (ulepszona — fallback po HTML)
# ---------------------------------------------------------------------------
def pobierz_tekst_pdf(id_dokumentu, sesja=None):
    """
    Próbuje pobrać dokument w kolejności:
      1. HTML (szybciej, mniej obciążeń serwera)
      2. PDF (pełna wierność, jako fallback)
    Zwraca (tekst, status).
    """
    # --- próba HTML ---
    tekst_html, status_html = _pobierz_tekst_html(id_dokumentu, sesja)
    if tekst_html:
        return tekst_html, "OK"
    if status_html == "BLOKADA":
        return None, "BLOKADA"     # nie próbujemy PDF jeśli IP zablokowane

    # --- fallback: PDF ---
    url     = PDF_API_URL.format(id=id_dokumentu)
    headers = {"User-Agent": "Mozilla/5.0"}
    caller  = sesja if sesja else requests
    for proba in range(3):
        try:
            r = caller.get(url, headers=headers, timeout=TIMEOUT_PDF)
            if r.status_code == 200:
                plik   = io.BytesIO(r.content)
                reader = PyPDF2.PdfReader(plik)
                tekst  = "\n".join(
                    p.extract_text() or "" for p in reader.pages
                ).strip()
                if tekst:
                    return tekst, "OK"
                return None, "BRAK_PLIKU"   # PDF bez tekstu (skany)
            elif r.status_code in (404, 400):
                return None, "BRAK_PLIKU"
            elif r.status_code == 429:
                time.sleep(5 + proba * 3)
            else:
                time.sleep(2)
        except Exception:
            time.sleep(3)
    return None, "BLOKADA"

# ---------------------------------------------------------------------------
# ► NOWE: RÓWNOLEGŁE POBIERANIE DOKUMENTÓW
# ---------------------------------------------------------------------------
def pobierz_dokumenty_rownolegle(
    lista_dokumentow: list,
    przetworzone_id: set,
    uszkodzone_id: set,
    callback_postep=None,   # fn(idx, total, sygnatura, status)
    workers: int = WORKERS_POBIERANIA
) -> tuple[list, list, list]:
    """
    Pobiera dokumenty równolegle w `workers` wątkach.

    Zwraca:
        (pelne_tresci, nowe_przetworzone_id, nowe_uszkodzone_id)
        — tylko NOWE rekordy z tego wywołania, do scalenia przez dzwoniącego.

    callback_postep(idx, total, sygnatura, status_str) jest wołany po każdym dokumencie,
    dzięki czemu UI może aktualizować pasek postępu.
    """
    pelne_tresci       = []
    nowe_przetworzone  = []
    nowe_uszkodzone    = []
    blokada_wykryta    = threading.Event()
    total              = len(lista_dokumentow)

    def _pobierz_jeden(args):
        idx, dok = args
        if blokada_wykryta.is_set():
            return idx, dok, None, "POMINIETY"
        # Każdy wątek tworzy własną sesję HTTP (bardziej przyjazne dla serwera)
        with requests.Session() as s:
            time.sleep((idx % workers) * 0.15)   # rozłożenie startów w czasie
            tekst, status = pobierz_tekst_pdf(dok["id"], sesja=s)
        return idx, dok, tekst, status

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_pobierz_jeden, (i, d)): (i, d)
            for i, d in enumerate(lista_dokumentow)
        }
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            idx, dok, tekst, status = future.result()
            completed += 1

            with _lock:
                if tekst:
                    rekord = {
                        "Data":      dok["data"],
                        "Podatek":   dok["typ"],
                        "Sygnatura": dok["sygnatura"],
                        "Link":      PODGLAD_URL.format(id=dok["id"]),
                        "Tekst":     tekst,
                        "Format":    "HTML+PDF",
                        "Pobrano":   datetime.now().isoformat(timespec='seconds')
                    }
                    pelne_tresci.append(rekord)
                    nowe_przetworzone.append(dok["id"])
                elif status == "BLOKADA":
                    blokada_wykryta.set()
                    nowe_uszkodzone.append(dok["id"])   # tymczasowo jako uszkodzone
                elif status in ("BRAK_PLIKU", "BŁĄD_CZYTANIA"):
                    nowe_uszkodzone.append(dok["id"])

            if callback_postep:
                callback_postep(completed, total, dok["sygnatura"], status)

    # Sortuj wyniki wg oryginalnej kolejności (as_completed miesza kolejność)
    id_order = {d["id"]: i for i, d in enumerate(lista_dokumentow)}
    pelne_tresci.sort(key=lambda r: id_order.get(
        next((d["id"] for d in lista_dokumentow if PODGLAD_URL.format(id=d["id"]) == r["Link"]), ""),
        0
    ))

    return pelne_tresci, nowe_przetworzone, nowe_uszkodzone, blokada_wykryta.is_set()

# ---------------------------------------------------------------------------
# POBIERANIE LISTY DOKUMENTÓW Z API MF (bez zmian w logice, tylko cleanup)
# ---------------------------------------------------------------------------
# Rotacja User-Agent - mniejsze ryzyko blokady
_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]
_ua_idx = 0

def _wykonaj_zapytanie_api(sesja, url, payload, timeout=15):
    """
    Wspólna logika zapytania POST do Eureka API z retry i rotacja UA.
    Zwraca (wyniki: list, status: str, total_hits: int|None).
    total_hits pochodzi z pola "totalHits" w odpowiedzi API - pozwala
    precyzyjnie wiedziec ile lacznie dokumentow jest dla danego zapytania,
    zamiast zgadywac po dlugosci strony.
    """
    global _ua_idx
    MAKS_PROB = 3

    for proba in range(MAKS_PROB):
        try:
            ua = _UA_LIST[_ua_idx % len(_UA_LIST)]
            _ua_idx += 1
            r = sesja.post(
                url, json=payload,
                headers={
                    "User-Agent": ua,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
                    "Origin": "https://eureka.mf.gov.pl",
                    "Referer": "https://eureka.mf.gov.pl/",
                },
                timeout=timeout
            )

            if r.status_code == 200:
                dane   = r.json()
                wyniki = dane.get("content") or dane.get("items") or []
                if not wyniki:
                    for v in dane.values():
                        if (isinstance(v, list) and v
                                and isinstance(v[0], dict)
                                and ("id" in v[0] or "ID_INFORMACJI" in v[0])):
                            wyniki = v
                            break

                total_hits = (
                    dane.get("totalHits")
                    or dane.get("totalElements")
                    or dane.get("total")
                )

                return wyniki, "OK", total_hits

            elif r.status_code == 429:
                # Rate limit - czekaj coraz dluzej
                czas = 10 * (proba + 1)
                time.sleep(czas)
                continue

            elif r.status_code in (403, 503):
                # Potencjalna blokada - dluzsze czekanie
                time.sleep(15 * (proba + 1))
                continue

            else:
                return [], f"ERROR_{r.status_code}", None

        except requests.exceptions.Timeout:
            time.sleep(5)
            continue
        except Exception:
            time.sleep(3)
            continue

    return [], "ERROR", None

def _mapuj_dokument(d, nazwa_podatku):
    """
    Mapuje surowy rekord z API na nasz format.
    UWAGA: filtrowanie po podatku NIE odbywa sie juz tutaj (po sygnaturze) -
    odbywa sie po stronie API przez filtr PRZEPISY w samym zapytaniu.
    Ta funkcja tylko przeksztalca format, nie odrzuca dokumentow.
    """
    sygnatura = str(d.get('SYG', '')).upper()
    doc_id = str(d.get('id') or d.get('ID_INFORMACJI') or '')
    if not doc_id:
        return None
    return {
        "id":        doc_id,
        "sygnatura": sygnatura,
        "typ":       nazwa_podatku,
        "data":      str(d.get('DT_WYD', '')).split('T')[0]
    }

def pobierz_wszystko_z_okresu(data_start_str, data_koniec_str, sesja, nazwa_podatku, kod_przepisu):
    """
    kod_przepisu: numeryczny ID aktu prawnego z KODY_PRZEPISOW (np. 29903 dla PIT).
    To jest PRAWDZIWY filtr uzywany przez API - identyczny z tym, czego
    uzywa strona eureka.mf.gov.pl przy wyszukiwaniu.
    """
    dokumenty  = []
    page       = 0
    total_hits = None

    while True:
        url     = SEARCH_API_URL_BASE.format(page=page)
        payload = {
            "query": "",
            "filter": {
                "KATEGORIA_INFORMACJI": [1],
                "PRZEPISY":     [kod_przepisu],
                "DT_WYD_start": data_start_str,
                "DT_WYD_end":   data_koniec_str
            },
            "columns": ["SYG", "ID_INFORMACJI", "DT_WYD", "KATEGORIA_INFORMACJI"],
            "searchInFullPhrase": True,
            "searchInContent":    False,
            "searchInSynonyms":   False,
            "warunkiDodatkowe":   []
        }
        wyniki, status, total_hits_strona = _wykonaj_zapytanie_api(sesja, url, payload, timeout=15)
        if status == "ERROR":
            return dokumenty, "ERROR"

        if total_hits is None and total_hits_strona is not None:
            total_hits = total_hits_strona

        for d in wyniki:
            dok = _mapuj_dokument(d, nazwa_podatku)
            if dok:
                dokumenty.append(dok)

        # Precyzyjny warunek konca paginacji: znamy totalHits z odpowiedzi API
        if total_hits is not None:
            if len(dokumenty) >= total_hits or not wyniki:
                break
        else:
            # Fallback gdyby API nie zwrocilo totalHits w tej odpowiedzi
            if len(wyniki) < 25:
                break

        page += 1
        time.sleep(0.2)

    if total_hits is not None and len(dokumenty) != total_hits:
        # Niezgodnosc miedzy zadeklarowanym total a faktycznie pobranymi -
        # nie ukrywamy tego cicho, zwracamy specjalny status zeby wywolujacy
        # mogl to zalogowac/ostrzec
        return dokumenty, "NIEPELNE_POBRANIE"

    return dokumenty, "OK"

def szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja, nazwa_podatku, kod_przepisu):
    """
    kod_przepisu: numeryczny ID aktu prawnego z KODY_PRZEPISOW (np. 29903 dla PIT).
    """
    dokumenty  = []
    page       = 0
    total_hits = None

    while True:
        url     = SEARCH_API_URL_BASE.format(page=page)
        payload = {
            "query": fraza,
            "filter": {
                "KATEGORIA_INFORMACJI": [1],
                "PRZEPISY":     [kod_przepisu],
                "DT_WYD_start": data_start_str,
                "DT_WYD_end":   data_koniec_str
            },
            "columns": ["SYG", "ID_INFORMACJI", "DT_WYD", "KATEGORIA_INFORMACJI"],
            "searchInFullPhrase": False,
            "searchInContent":    True,
            "searchInSynonyms":   True,
            "warunkiDodatkowe":   []
        }
        wyniki, status, total_hits_strona = _wykonaj_zapytanie_api(sesja, url, payload, timeout=12)
        if status == "ERROR":
            return dokumenty, "ERROR"

        if total_hits is None and total_hits_strona is not None:
            total_hits = total_hits_strona

        for d in wyniki:
            dok = _mapuj_dokument(d, nazwa_podatku)
            if dok:
                dokumenty.append(dok)

        if total_hits is not None:
            if len(dokumenty) >= total_hits or not wyniki:
                break
        else:
            if len(wyniki) < 25:
                break

        page += 1
        time.sleep(0.2)

    return dokumenty, "OK"
