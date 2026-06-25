import os
import json
import re
import time
import requests
import io
import PyPDF2

FOLDER_DOCELOWY = 'PickPivot_Data'
if not os.path.exists(FOLDER_DOCELOWY): os.makedirs(FOLDER_DOCELOWY)

PLIK_KONFIGURACJI_M1 = f"{FOLDER_DOCELOWY}/historia_m1.json"
PLIK_REKORDOW_M1 = f"{FOLDER_DOCELOWY}/baza_tresci_m1.json"
PLIK_KONFIGURACJI_M2 = f"{FOLDER_DOCELOWY}/historia_m2.json"
PLIK_REKORDOW_M2 = f"{FOLDER_DOCELOWY}/baza_tresci_m2.json"

SEARCH_API_URL_BASE = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page={page}&sort=parametryPozycjonowania%2Casc"
PDF_API_URL = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"
PODGLAD_URL = "https://eureka.mf.gov.pl/informacje/podglad/{id}"

FRAZY_KLUCZOWE = [
    "sieć ciepłownicza", "przebudowa sieci", "przyłącze", "węzeł cieplny",
    "taryfa dla ciepła", "wodociąg", "kanalizacja", "oczyszczalnia ścieków",
    "stacja uzdatniania", "spółka komunalna"
]

KODY_PODATKOW = {"PIT": ".4011.", "CIT": ".4010.", "VAT": ".4012.", "AKCYZA": ".4013."}
MIESIACE_PL = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec", "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]

def wczytaj_historie(plik):
    if os.path.exists(plik):
        with open(plik, 'r', encoding='utf-8') as f:
            dane = json.load(f)
            if "uszkodzone_id" not in dane: dane["uszkodzone_id"] = []
            return dane
    return {"przetworzone_id": [], "ukonczone_kombinacje": [], "uszkodzone_id": []}

def zapisz_historie(plik, konfiguracja):
    with open(plik, 'w', encoding='utf-8') as f: json.dump(konfiguracja, f, ensure_ascii=False, indent=4)

def wczytaj_pelne_tresci(plik):
    if os.path.exists(plik):
        with open(plik, 'r', encoding='utf-8') as f: return json.load(f)
    return []

def zapisz_pelne_tresci(plik, lista_rekordow):
    with open(plik, 'w', encoding='utf-8') as f: json.dump(lista_rekordow, f, ensure_ascii=False, indent=4)

def wyczysc_dane_serwera(plik_konf, plik_rekordow):
    if os.path.exists(plik_konf): os.remove(plik_konf)
    if os.path.exists(plik_rekordow): os.remove(plik_rekordow)

def wyczysc_tekst_dla_worda(tekst):
    if not tekst: return ""
    return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\u10000-\u10FFFF]', '', tekst)

def pobierz_tekst_pdf(id_dokumentu):
    url = PDF_API_URL.format(id=id_dokumentu)
    headers_pdf = {"User-Agent": "Mozilla/5.0", "Referer": "https://eureka.mf.gov.pl/"}
    for proba in range(3):
        try:
            response = requests.get(url, headers=headers_pdf, timeout=20)
            if response.status_code == 200:
                plik_w_pamieci = io.BytesIO(response.content)
                tekst_dokumentu = ""
                reader = PyPDF2.PdfReader(plik_w_pamieci)
                for strona in reader.pages:
                    wyc = strona.extract_text()
                    if wyc: tekst_dokumentu += wyc + "\n"
                return tekst_dokumentu, "OK"
            elif response.status_code in [404, 400]: return None, "BRAK_PLIKU"
            elif response.status_code == 429: time.sleep(5)
            else: time.sleep(2)
        except: time.sleep(3)
    return None, "BLOKADA"

def szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja, nazwa_podatku, kod_sygnatury):
    dokumenty_podatkowe = []
    page = 0
    while True:
        url = SEARCH_API_URL_BASE.format(page=page)
        payload = {
            "query": fraza,
            "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
            "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
            "searchInFullPhrase": False, "searchInContent": True, "searchInSynonyms": True, "warunkiDodatkowe": []
        }
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        try:
            response = sesja.post(url, json=payload, headers=headers, timeout=12)
            if response.status_code == 200:
                dane = response.json()
                wyniki = dane.get('content') or dane.get('items') or []
                if not wyniki:
                    for k, v in dane.items():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                            if 'id' in v[0] or 'ID_INFORMACJI' in v[0]:
                                wyniki = v; break
                for d in wyniki:
                    sygnatura = str(d.get('SYG', '')).upper()
                    data_wydania = str(d.get('DT_WYD', '')).split('T')[0]
                    if kod_sygnatury in sygnatura:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id: dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": data_wydania})
                if len(wyniki) < 100: break
                page += 1
                time.sleep(0.2)
            else: return dokumenty_podatkowe, "ERROR"
        except: return dokumenty_podatkowe, "ERROR"
    return dokumenty_podatkowe, "OK"

def pobierz_wszystko_z_okresu(data_start_str, data_koniec_str, sesja, nazwa_podatku, kod_sygnatury):
    dokumenty_podatkowe = []
    page = 0
    while True:
        url = SEARCH_API_URL_BASE.format(page=page)
        payload = {
            "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
            "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
            "searchInFullPhrase": False, "searchInContent": False, "searchInSynonyms": False, "warunkiDodatkowe": []
        }
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        try:
            response = sesja.post(url, json=payload, headers=headers, timeout=15)
            if response.status_code == 200:
                dane = response.json()
                wyniki = dane.get('content') or dane.get('items') or []
                if not wyniki:
                    for k, v in dane.items():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                            if 'id' in v[0] or 'ID_INFORMACJI' in v[0]:
                                wyniki = v; break
                for d in wyniki:
                    sygnatura = str(d.get('SYG', '')).upper()
                    data_wydania = str(d.get('DT_WYD', '')).split('T')[0]
                    if kod_sygnatury in sygnatura:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id: dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": data_wydania})
                if len(wyniki) < 100: break
                page += 1
                time.sleep(0.2)
            else: return dokumenty_podatkowe, "ERROR"
        except: return dokumenty_podatkowe, "ERROR"
    return dokumenty_podatkowe, "OK"
