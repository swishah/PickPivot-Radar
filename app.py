import streamlit as st
import requests
import PyPDF2
import time
import random
import io
import calendar
import json
import os
import re
import pandas as pd
from datetime import datetime, date
from docx import Document

# --- 1. USTAWIENIA STRONY ---
st.set_page_config(page_title="PickPivot Platform", page_icon="⚡", layout="wide")

# --- 2. KONFIGURACJA ŚRODOWISKA BOTA ---
FOLDER_DOCELOWY = 'PickPivot_Data'
if not os.path.exists(FOLDER_DOCELOWY):
    os.makedirs(FOLDER_DOCELOWY)

# Pliki dla Modułu 1 (Radar)
PLIK_KONFIGURACJI_M1 = f"{FOLDER_DOCELOWY}/historia_m1.json"
PLIK_REKORDOW_M1 = f"{FOLDER_DOCELOWY}/baza_tresci_m1.json"

# Pliki dla Modułu 2 (Ściągacz)
PLIK_KONFIGURACJI_M2 = f"{FOLDER_DOCELOWY}/historia_m2.json"
PLIK_REKORDOW_M2 = f"{FOLDER_DOCELOWY}/baza_tresci_m2.json"

SEARCH_API_URL = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page=0&sort=parametryPozycjonowania%2Casc"
PDF_API_URL = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"
PODGLAD_URL = "https://eureka.mf.gov.pl/informacje/podglad/{id}"

FRAZY_KLUCZOWE = [
    "sieć ciepłownicza", "przebudowa sieci", "przyłącze", "węzeł cieplny",
    "taryfa dla ciepła", "wodociąg", "kanalizacja", "oczyszczalnia ścieków",
    "stacja uzdatniania", "spółka komunalna"
]

KODY_PODATKOW = {
    "CIT": ".4010.",
    "VAT": ".4012.",
    "AKCYZA": ".4013."
}

# --- 3. UNIWERSALNE FUNKCJE PAMIĘCI I UTILS ---
def wczytaj_historie(plik):
    if os.path.exists(plik):
        with open(plik, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"przetworzone_id": [], "ukonczone_kombinacje": []}

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
    if os.path.exists(plik_konf):
        os.remove(plik_konf)
    if os.path.exists(plik_rekordow):
        os.remove(plik_rekordow)

def wyczysc_tekst_dla_worda(tekst):
    if not tekst: return ""
    return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\u10000-\u10FFFF]', '', tekst)

def pobierz_tekst_pdf(id_dokumentu):
    url = PDF_API_URL.format(id=id_dokumentu)
    headers_pdf = {"User-Agent": "Mozilla/5.0", "Referer": "https://eureka.mf.gov.pl/"}
    try:
        response = requests.get(url, headers=headers_pdf, timeout=15)
        if response.status_code == 200:
            plik_w_pamieci = io.BytesIO(response.content)
            tekst_dokumentu = ""
            reader = PyPDF2.PdfReader(plik_w_pamieci)
            for strona in reader.pages:
                wyc = strona.extract_text()
                if wyc: tekst_dokumentu += wyc + "\n"
            return tekst_dokumentu
    except:
        return None
    return None

# --- 4. FUNKCJE API (MODUŁ 1: RADAR) ---
def szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja, nazwa_podatku, kod_sygnatury):
    payload = {
        "query": fraza,
        "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
        "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
        "searchInFullPhrase": False, 
        "searchInContent": True,     
        "searchInSynonyms": True,    
        "warunkiDodatkowe": []
    }
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    try:
        response = sesja.post(SEARCH_API_URL, json=payload, headers=headers, timeout=12)
        if response.status_code == 200:
            dane = response.json()
            wyniki = dane.get('content') or dane.get('items') or []
            if not wyniki:
                for k, v in dane.items():
                    if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                        if 'id' in v[0] or 'ID_INFORMACJI' in v[0]:
                            wyniki = v
                            break
            dokumenty_podatkowe = []
            for d in wyniki:
                sygnatura = str(d.get('SYG', '')).upper()
                data_wydania = str(d.get('DT_WYD', '')).split('T')[0]
                if kod_sygnatury in sygnatura:
                    doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                    if doc_id:
                        dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": data_wydania})
            return dokumenty_podatkowe, "OK"
    except requests.exceptions.Timeout:
        return [], "TIMEOUT"
    except:
        return [], "ERROR"
    return [], "OK"

# --- 5. FUNKCJE API (MODUŁ 2: ŚCIĄGACZ) ---
def pobierz_wszystko_z_dnia(data_str, sesja, nazwa_podatku, kod_sygnatury):
    """Pobiera wszystkie interpretacje z konkretnego dnia, bez słów kluczowych"""
    payload = {
        "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_str, "DT_WYD_end": data_str},
        "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
        "searchInFullPhrase": False,
        "searchInContent": False,
        "searchInSynonyms": False,
        "warunkiDodatkowe": []
    }
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    try:
        response = sesja.post(SEARCH_API_URL, json=payload, headers=headers, timeout=12)
        if response.status_code == 200:
            dane = response.json()
            wyniki = dane.get('content') or dane.get('items') or []
            if not wyniki:
                for k, v in dane.items():
                    if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                        if 'id' in v[0] or 'ID_INFORMACJI' in v[0]:
                            wyniki = v
                            break
            dokumenty_podatkowe = []
            for d in wyniki:
                sygnatura = str(d.get('SYG', '')).upper()
                if kod_sygnatury in sygnatura:
                    doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                    if doc_id:
                        dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": data_str})
            return dokumenty_podatkowe, "OK"
    except requests.exceptions.Timeout:
        return [], "TIMEOUT"
    except:
        return [], "ERROR"
    return [], "OK"


# --- 6. PANEL NAWIGACYJNY ---
st.sidebar.title("📌 Menu PickPivot")
st.sidebar.markdown("---")
aktywna_zakladka = st.sidebar.radio("Wybierz moduł platformy:", ["1", "2", "3", "4", "5", "6"])
st.sidebar.markdown("---")
st.sidebar.caption("© 2026 PickPivot v8.0")

# --- 7. LOGIKA MODUŁÓW ---

if aktywna_zakladka == "1":
    # ==========================================
    # MODUŁ 1: RADAR ORZECZNICTWA
    # ==========================================
    st.title("⚡ PickPivot: Radar Orzecznictwa")
    st.markdown("Wersja zoptymalizowana. Wyszukuje interpretacje po słowach kluczowych i synonimach.")

    konfiguracja = wczytaj_historie(PLIK_KONFIGURACJI_M1)
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))
    ukonczone_kombinacje = set(konfiguracja.get("ukonczone_kombinacje", []))
    pelne_tresci_cache = wczytaj_pelne_tresci(PLIK_REKORDOW_M1)

    if pelne_tresci_cache:
        st.success(f"💾 BAZA DANYCH RADARU: Zabezpieczono {len(pelne_tresci_cache)} orzeczeń.")
        colA, colB = st.columns(2)
        with colA:
            if st.button("📄 GENERUJ RAPORT WORD (.docx)", use_container_width=True, type="primary"):
                with st.spinner("Kompilowanie pliku..."):
                    doc = Document()
                    doc.add_heading('Radar PickPivot', 0)
                    for rekord in pelne_tresci_cache:
                        doc.add_heading(f"Sygnatura: {rekord['Sygnatura']}", level=1)
                        doc.add_paragraph(f"Data: {rekord['Data']} | Podatek: {rekord['Podatek']}")
                        doc.add_paragraph(f"Fraza wywołująca: {rekord['Słowo kluczowe']}")
                        doc.add_paragraph(f"Link: {rekord['Link']}")
                        doc.add_heading("Treść:", level=2)
                        doc.add_paragraph(wyczysc_tekst_dla_worda(rekord['Tekst']))
                        doc.add_page_break()
                    output = io.BytesIO()
                    doc.save(output)
                    st.download_button("📥 Pobierz plik", data=output.getvalue(), file_name=f"Radar_{datetime.now().strftime('%Y%m%d')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
        with colB:
            if st.button("🗑️ Resetuj bazę Radaru", use_container_width=True):
                wyczysc_dane_serwera(PLIK_KONFIGURACJI_M1, PLIK_REKORDOW_M1)
                st.rerun()
        st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata = st.multiselect("Lata:", [2024, 2025, 2026])
    with col2: wybrane_miesiace = st.multiselect("Miesiące:", list(range(1, 13)))
    with col3: wybrane_podatki_ui = st.multiselect("Podatki:", ["CIT", "VAT", "AKCYZA"])

    if st.button("🚀 Uruchom skanowanie słów kluczowych", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace or not wybrane_podatki_ui:
            st.error("Wybierz parametry.")
            st.stop()
            
        pasek_postepu = st.progress(0)
        status_tekst = st.empty()
        calkowita_liczba_zapytan = len(wybrane_lata) * len(wybrane_miesiace) * len(FRAZY_KLUCZOWE) * len(wybrane_podatki_ui)
        zapytania_wykonane = 0
        licznik_trafien = 0

        with requests.Session() as sesja_bazy:
            for rok in wybrane_lata:
                for miesiac in wybrane_miesiace:
                    _, ost_dzien = calendar.monthrange(rok, miesiac)
                    data_start_str = f"{rok}-{miesiac:02d}-01"
                    data_koniec_str = f"{rok}-{miesiac:02d}-{ost_dzien:02d}"
                    for fraza in FRAZY_KLUCZOWE:
                        for podatek in wybrane_podatki_ui:
                            klucz_kombinacji = f"M1_{rok}_{miesiac}_{fraza}_{podatek}"
                            if klucz_kombinacji in ukonczone_kombinacje:
                                zapytania_wykonane += 1
                                continue
                            
                            status_tekst.info(f"Radar odpytuje: {fraza} ({podatek}) dla {miesiac:02d}/{rok}...")
                            lista_trafien, _ = szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja_bazy, podatek, KODY_PODATKOW[podatek])
                            
                            if lista_trafien:
                                aktualne_tresci = wczytaj_pelne_tresci(PLIK_REKORDOW_M1)
                                for dok in lista_trafien:
                                    if dok["id"] not in przetworzone_id:
                                        tekst = pobierz_tekst_pdf(dok["id"])
                                        if tekst:
                                            aktualne_tresci.append({
                                                "Data": dok["data"], "Podatek": dok["typ"], "Sygnatura": dok["sygnatura"],
                                                "Słowo kluczowe": fraza.upper(), "Link": PODGLAD_URL.format(id=dok["id"]), "Tekst": tekst
                                            })
                                            przetworzone_id.add(dok["id"])
                                            konfiguracja["przetworzone_id"].append(dok["id"])
                                            licznik_trafien += 1
                                zapisz_pelne_tresci(PLIK_REKORDOW_M1, aktualne_tresci)
                            
                            ukonczone_kombinacje.add(klucz_kombinacji)
                            konfiguracja["ukonczone_kombinacje"].append(klucz_kombinacji)
                            zapisz_historie(PLIK_KONFIGURACJI_M1, konfiguracja)
                            zapytania_wykonane += 1
                            pasek_postepu.progress(min(1.0, zapytania_wykonane / calkowita_liczba_zapytan))
                            time.sleep(0.2)
                            
        status_tekst.success(f"🎉 Zakończono! Zebrano {licznik_trafien} dokumentów.")
        st.balloons()
        time.sleep(3)
        st.rerun()

elif aktywna_zakladka == "2":
    # ==========================================
    # MODUŁ 2: ŚCIĄGACZ INTERPRETACJI (BULK)
    # ==========================================
    st.title("📦 Ściągacz Interpretacji")
    st.markdown("Kompleksowe pobieranie wszystkich wydanych interpretacji z wybranego okresu i podatku (bez filtrowania słów kluczowych). Idealne do budowy własnego archiwum.")

    konfiguracja_m2 = wczytaj_historie(PLIK_KONFIGURACJI_M2)
    przetworzone_id_m2 = set(konfiguracja_m2.get("przetworzone_id", []))
    ukonczone_dni_m2 = set(konfiguracja_m2.get("ukonczone_kombinacje", []))
    pelne_tresci_m2 = wczytaj_pelne_tresci(PLIK_REKORDOW_M2)

    if pelne_tresci_m2:
        st.success(f"💾 BAZA ŚCIĄGACZA: Zgromadzono do tej pory {len(pelne_tresci_m2)} dokumentów.")
        colA, colB = st.columns(2)
        with colA:
            if st.button("📄 GENERUJ ARCHIWUM WORD (.docx)", use_container_width=True, type="primary"):
                with st.spinner("Składanie obszernego dokumentu..."):
                    doc = Document()
                    doc.add_heading('Kompleksowe Archiwum Orzecznictwa', 0)
                    for rekord in pelne_tresci_m2:
                        doc.add_heading(f"Sygnatura: {rekord['Sygnatura']}", level=1)
                        doc.add_paragraph(f"Data: {rekord['Data']} | Podatek: {rekord['Podatek']}")
                        doc.add_paragraph(f"Link: {rekord['Link']}")
                        doc.add_heading("Treść:", level=2)
                        doc.add_paragraph(wyczysc_tekst_dla_worda(rekord['Tekst']))
                        doc.add_page_break()
                    output = io.BytesIO()
                    doc.save(output)
                    st.download_button("📥 Pobierz Archiwum", data=output.getvalue(), file_name=f"Archiwum_Zrzut_{datetime.now().strftime('%Y%m%d')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
        with colB:
            if st.button("🗑️ Wyczyść pamięć Ściągacza", use_container_width=True):
                wyczysc_dane_serwera(PLIK_KONFIGURACJI_M2, PLIK_REKORDOW_M2)
                st.rerun()
        st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata_m2 = st.multiselect("Lata:", [2024, 2025, 2026], key="latam2")
    with col2: wybrane_miesiace_m2 = st.multiselect("Miesiące:", list(range(1, 13)), key="miesm2")
    with col3: wybrane_podatki_ui_m2 = st.multiselect("Podatki:", ["CIT", "VAT", "AKCYZA"], key="podm2")

    if st.button("🚀 Uruchom kompleksowe pobieranie", use_container_width=True):
        if not wybrane_lata_m2 or not wybrane_miesiace_m2 or not wybrane_podatki_ui_m2:
            st.error("Proszę wybrać parametry.")
            st.stop()

        dzisiaj = date.today()
        pasek_postepu = st.progress(0)
        status_tekst = st.empty()
        log_szczegolowy = st.empty()
        
        # Obliczanie dni do sprawdzenia
        lista_dni_do_sprawdzenia = []
        for rok in wybrane_lata_m2:
            for miesiac in wybrane_miesiace_m2:
                _, liczba_dni = calendar.monthrange(rok, miesiac)
                for dzien in range(1, liczba_dni + 1):
                    aktualna_data = date(rok, miesiac, dzien)
                    if aktualna_data <= dzisiaj:
                        lista_dni_do_sprawdzenia.append(aktualna_data.strftime('%Y-%m-%d'))

        calkowita_liczba_krokow = len(lista_dni_do_sprawdzenia) * len(wybrane_podatki_ui_m2)
        kroki_wykonane = 0
        licznik_nowych = 0

        with requests.Session() as sesja_bazy:
            for data_str in lista_dni_do_sprawdzenia:
                for podatek in wybrane_podatki_ui_m2:
                    
                    klucz_kombinacji = f"M2_{data_str}_{podatek}"
                    if klucz_kombinacji in ukonczone_dni_m2:
                        kroki_wykonane += 1
                        continue
                        
                    status_tekst.info(f"Pobieranie puli z dnia: {data_str} (Podatek: {podatek})...")
                    lista_trafien, _ = pobierz_wszystko_z_dnia(data_str, sesja_bazy, podatek, KODY_PODATKOW[podatek])
                    
                    if lista_trafien:
                        aktualne_tresci = wczytaj_pelne_tresci(PLIK_REKORDOW_M2)
                        for dok in lista_trafien:
                            if dok["id"] not in przetworzone_id_m2:
                                log_szczegolowy.text(f"Zapisywanie dokumentu: {dok['sygnatura']}...")
                                tekst = pobierz_tekst_pdf(dok["id"])
                                if tekst:
                                    aktualne_tresci.append({
                                        "Data": dok["data"], "Podatek": dok["typ"], "Sygnatura": dok["sygnatura"],
                                        "Link": PODGLAD_URL.format(id=dok["id"]), "Tekst": tekst
                                    })
                                    przetworzone_id_m2.add(dok["id"])
                                    konfiguracja_m2["przetworzone_id"].append(dok["id"])
                                    licznik_nowych += 1
                        zapisz_pelne_tresci(PLIK_REKORDOW_M2, aktualne_tresci)

                    ukonczone_dni_m2.add(klucz_kombinacji)
                    konfiguracja_m2["ukonczone_kombinacje"].append(klucz_kombinacji)
                    zapisz_historie(PLIK_KONFIGURACJI_M2, konfiguracja_m2)
                    
                    kroki_wykonane += 1
                    pasek_postepu.progress(min(1.0, kroki_wykonane / calkowita_liczba_krokow))
                    time.sleep(0.2)

        status_tekst.success(f"🎉 Zakończono! Pobrano {licznik_nowych} nowych dokumentów.")
        log_szczegolowy.empty()
        st.balloons()
        time.sleep(3)
        st.rerun()

else:
    # ==========================================
    # MODUŁY 3, 4, 5, 6
    # ==========================================
    st.title(f"🛠️ Moduł {aktywna_zakladka}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
