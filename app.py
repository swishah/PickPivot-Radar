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

# BAZOWY ADRES URL Z PAGINACJĄ (Pobieranie stron)
SEARCH_API_URL_BASE = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page={page}&sort=parametryPozycjonowania%2Casc"
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

# --- 4. FUNKCJE API Z PAGINACJĄ (BEZ LIMITU 100 WYNIKÓW) ---
def szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja, nazwa_podatku, kod_sygnatury):
    """Zoptymalizowane wyszukiwanie dla Radaru (Moduł 1) - pobiera całe miesiące z obsługą paginacji"""
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
                
                # Jeśli serwer przesłał puste wyniki w innej strukturze
                if not wyniki:
                    for k, v in dane.items():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                            if 'id' in v[0] or 'ID_INFORMACJI' in v[0]:
                                wyniki = v
                                break
                
                for d in wyniki:
                    sygnatura = str(d.get('SYG', '')).upper()
                    data_wydania = str(d.get('DT_WYD', '')).split('T')[0]
                    if kod_sygnatury in sygnatura:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id:
                            dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": data_wydania})
                
                # Przerywamy pętlę, jeśli na tej stronie było mniej niż 100 wyników (koniec danych)
                if len(wyniki) < 100:
                    break
                page += 1
                time.sleep(0.2) # Krótka przerwa między stronami wewnątrz MF
            else:
                return dokumenty_podatkowe, "ERROR"
        except requests.exceptions.Timeout:
            return dokumenty_podatkowe, "TIMEOUT"
        except:
            return dokumenty_podatkowe, "ERROR"
            
    return dokumenty_podatkowe, "OK"


def pobierz_wszystko_z_okresu(data_start_str, data_koniec_str, sesja, nazwa_podatku, kod_sygnatury):
    """Kompleksowe wyszukiwanie dla Ściągacza (Moduł 2) - cały miesiąc na raz z przewijaniem stron"""
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
                                wyniki = v
                                break
                                
                for d in wyniki:
                    sygnatura = str(d.get('SYG', '')).upper()
                    data_wydania = str(d.get('DT_WYD', '')).split('T')[0]
                    if kod_sygnatury in sygnatura:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id:
                            dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": data_wydania})
                
                # Automatyczna paginacja
                if len(wyniki) < 100:
                    break
                page += 1
                time.sleep(0.2)
            else:
                return dokumenty_podatkowe, "ERROR"
        except requests.exceptions.Timeout:
            return dokumenty_podatkowe, "TIMEOUT"
        except:
            return dokumenty_podatkowe, "ERROR"
            
    return dokumenty_podatkowe, "OK"


# --- 5. LEWY PANEL NAWIGACYJNY ---
st.sidebar.title("📌 Menu PickPivot")
st.sidebar.markdown("---")

aktywna_zakladka = st.sidebar.radio(
    "Wybierz moduł platformy:",
    [
        "1. Radar Orzecznictwa",
        "2. Ściągacz Interpretacji",
        "3. Generator Pism (W przyszłości)",
        "4. Panel Analityczny (W przyszłości)",
        "5. Historia Pobierania (W przyszłości)",
        "6. Ustawienia Systemu (W przyszłości)"
    ]
)

st.sidebar.markdown("---")
st.sidebar.caption("© 2026 PickPivot v9.0 (Dynamic Pagination)")

# --- 6. LOGIKA MODUŁÓW ---

if aktywna_zakladka.startswith("1."):
    # ==========================================
    # MODUŁ 1: RADAR ORZECZNICTWA
    # ==========================================
    st.title("⚡ PickPivot: Radar Orzecznictwa")
    st.markdown("Wyszukuje interpretacje podatkowe na podstawie zdefiniowanych słów kluczowych oraz synonimów.")

    konfiguracja = wczytaj_historie(PLIK_KONFIGURACJI_M1)
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))
    ukonczone_kombinacje = set(konfiguracja.get("ukonczone_kombinacje", []))
    pelne_tresci_cache = wczytaj_pelne_tresci(PLIK_REKORDOW_M1)

    if pelne_tresci_cache:
        st.success(f"💾 BAZA DANYCH RADARU: Zabezpieczono {len(pelne_tresci_cache)} orzeczeń o pełnej treści.")
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
    with col1: wybrane_lata = st.multiselect("Wybierz lata:", [2024, 2025, 2026])
    with col2: wybrane_miesiace = st.multiselect("Wybierz miesiące:", list(range(1, 13)))
    with col3: wybrane_podatki_ui = st.multiselect("Rodzaj podatku:", ["CIT", "VAT", "AKCYZA"])

    if st.button("🚀 Uruchom skanowanie słów kluczowych", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace or not wybrane_podatki_ui:
            st.error("Proszę wybrać parametry.")
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
                            
                            status_tekst.info(f"Radar odpytuje przedział {data_start_str} - {data_koniec_str}: {fraza} ({podatek})...")
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
                            
        status_tekst.success(f"🎉 Zakończono! Zebrano {licznik_trafien} dokumentów.")
        st.balloons()
        time.sleep(3)
        st.rerun()

elif aktywna_zakladka.startswith("2."):
    # ==========================================
    # MODUŁ 2: ŚCIĄGACZ INTERPRETACJI (BULK)
    # ==========================================
    st.title("📦 Ściągacz Interpretacji (Pobieranie Zbiorcze)")
    st.markdown("Pobiera **wszystkie** interpretacje indywidualne z wybranego okresu i łączy je w jeden plik Word.")

    konfiguracja_m2 = wczytaj_historie(PLIK_KONFIGURACJI_M2)
    przetworzone_id_m2 = set(konfiguracja_m2.get("przetworzone_id", []))
    pelne_tresci_m2 = wczytaj_pelne_tresci(PLIK_REKORDOW_M2)

    if pelne_tresci_m2:
        st.success(f"💾 BAZA ŚCIĄGACZA: W pamięci podręcznej serwera znajduje się obecnie {len(pelne_tresci_m2)} zabezpieczonych dokumentów.")
        if st.button("📄 GENERUJ ARCHIWUM WORD (.docx)", use_container_width=True, type="primary"):
            with st.spinner("Składanie dokumentu... Może to chwilę potrwać przy dużych zbiorach danych..."):
                doc = Document()
                doc.add_heading('Kompleksowe Archiwum Orzecznictwa', 0)
                for rekord in pelne_tresci_m2:
                    doc.add_heading(f"Sygnatura: {rekord['Sygnatura']}", level=1)
                    doc.add_paragraph(f"Data: {rekord['Data']} | Podatek: {rekord['Podatek']}")
                    doc.add_paragraph(f"Link źródłowy: {rekord['Link']}")
                    doc.add_heading("Pełna treść interpretacji:", level=2)
                    doc.add_paragraph(wyczysc_tekst_dla_worda(rekord['Tekst']))
                    doc.add_page_break()
                output = io.BytesIO()
                doc.save(output)
                st.download_button("📥 Pobierz Archiwum", data=output.getvalue(), file_name=f"Archiwum_Zrzut_{datetime.now().strftime('%Y%m%d')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
        st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata_m2 = st.multiselect("Wybierz lata:", [2024, 2025, 2026], key="latam2")
    with col2: wybrane_miesiace_m2 = st.multiselect("Wybierz miesiące:", list(range(1, 13)), key="miesm2")
    with col3: wybrane_podatki_ui_m2 = st.multiselect("Rodzaj podatku:", ["CIT", "VAT", "AKCYZA"], key="podm2")

    st.markdown("### Opcje pobierania")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        btn_wznow = st.button("▶️ Wznów pobieranie (Dokończ brakujące)", use_container_width=True)
    with col_btn2:
        btn_od_nowa = st.button("🔄 Pobierz całkowicie od nowa (Wyczyść pamięć)", use_container_width=True)

    if btn_wznow or btn_od_nowa:
        if not wybrane_lata_m2 or not wybrane_miesiace_m2 or not wybrane_podatki_ui_m2:
            st.error("Proszę wybrać parametry wejściowe.")
            st.stop()

        # Czyszczenie na życzenie użytkownika
        if btn_od_nowa:
            wyczysc_dane_serwera(PLIK_KONFIGURACJI_M2, PLIK_REKORDOW_M2)
            konfiguracja_m2 = {"przetworzone_id": [], "ukonczone_kombinacje": []}
            przetworzone_id_m2 = set()
            pelne_tresci_m2 = []
            st.toast("🧹 Pamięć wyczyszczona. Rozpoczynam od zera.")

        status_tekst = st.empty()
        log_szczegolowy = st.empty()

        # --- FAZA 1: SZYBKI SKAN METADANYCH ORAZ ZLICZANIE (MIESIĄCAMI) ---
        status_tekst.info("🔍 Krok 1/2: Analizuję całe miesiące. Pobieram indeks i zliczam oficjalną pulę Ministerstwa Finansów...")
        
        wszystkie_orzeczenia_w_mf = []
        do_pobrania_teraz = []

        with requests.Session() as sesja_bazy:
            for rok in wybrane_lata_m2:
                for miesiac in wybrane_miesiace_m2:
                    _, ost_dzien = calendar.monthrange(rok, miesiac)
                    data_start_str = f"{rok}-{miesiac:02d}-01"
                    data_koniec_str = f"{rok}-{miesiac:02d}-{ost_dzien:02d}"
                    
                    for podatek in wybrane_podatki_ui_m2:
                        log_szczegolowy.text(f"Liczenie bazy z całego przedziału: {data_start_str} - {data_koniec_str} ({podatek})...")
                        
                        # Pobiera całe pule dla danego podatku i całego miesiąca naraz z obsługą paginacji
                        lista_trafien, _ = pobierz_wszystko_z_okresu(data_start_str, data_koniec_str, sesja_bazy, podatek, KODY_PODATKOW[podatek])
                        
                        for dok in lista_trafien:
                            wszystkie_orzeczenia_w_mf.append(dok)
                            if dok["id"] not in przetworzone_id_m2:
                                do_pobrania_teraz.append(dok)

        laczna_liczba_orzeczen = len(wszystkie_orzeczenia_w_mf)
        liczba_brakujacych = len(do_pobrania_teraz)

        st.markdown(f"### 📊 Raport przedstartowy:")
        st.info(f"Odnaleziono łącznie: **{laczna_liczba_orzeczen}** wystawionych interpretacji dla wskazanego przedziału czasowego i podatków.")
        st.write(f"Do pobrania i scalenia pozostało: **{liczba_brakujacych}** dokumentów.")
        
        if liczba_brakujacych == 0:
            status_tekst.success("✔️ Wszystkie dokumenty z tego okresu są już bezpiecznie zapisane. Kliknij przycisk Generuj u góry!")
            log_szczegolowy.empty()
            st.stop()

        time.sleep(3)

        # --- FAZA 2: FAKTYCZNE POBIERANIE DOKUMENTÓW (PDF -> TEXT) ---
        status_tekst.info(f"⏳ Krok 2/2: Pobieranie fizycznych plików i czytanie treści (0 / {liczba_brakujacych})...")
        pasek_postepu = st.progress(0)
        
        licznik_pobranych_w_sesji = 0
        aktualne_tresci_m2 = wczytaj_pelne_tresci(PLIK_REKORDOW_M2)

        for idx, dok in enumerate(do_pobrania_teraz):
            log_szczegolowy.text(f"Pobieram plik ({idx+1}/{liczba_brakujacych}): {dok['sygnatura']}...")
            
            tekst = pobierz_tekst_pdf(dok["id"])
            if tekst:
                aktualne_tresci_m2.append({
                    "Data": dok["data"],
                    "Podatek": dok["typ"],
                    "Sygnatura": dok["sygnatura"],
                    "Link": PODGLAD_URL.format(id=dok["id"]),
                    "Tekst": tekst
                })
                przetworzone_id_m2.add(dok["id"])
                konfiguracja_m2["przetworzone_id"].append(dok["id"])
                licznik_pobranych_w_sesji += 1
                
                zapisz_pelne_tresci(PLIK_REKORDOW_M2, aktualne_tresci_m2)
                zapisz_historie(PLIK_KONFIGURACJI_M2, konfiguracja_m2)

            status_tekst.info(f"⏳ Pobrano i scalono {licznik_pobranych_w_sesji} z {liczba_brakujacych} dokumentów (Łączna pula to obecnie: {len(aktualne_tresci_m2)} z {laczna_liczba_orzeczen}).")
            pasek_postepu.progress((idx + 1) / liczba_brakujacych)
            time.sleep(random.uniform(0.1, 0.2))

        status_tekst.success(f"🎉 SUKCES! Zweryfikowana, oficjalna łączna liczba to {laczna_liczba_orzeczen} interpretacji. System posiada wyizolowaną i połączoną treść z {len(aktualne_tresci_m2)} z nich.")
        log_szczegolowy.empty()
        st.balloons()
        time.sleep(4)
        st.rerun()

else:
    # ==========================================
    # MODUŁY PUSTE
    # ==========================================
    st.title(f"🛠️ {aktywna_zakladka}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
