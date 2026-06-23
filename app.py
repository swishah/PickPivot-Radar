import streamlit as st
import requests
import pdfplumber
import time
import random
import io
import calendar
import pandas as pd
import json
import os
from datetime import datetime, date
from docx import Document

# --- 1. USTAWIENIA STRONY ---
st.set_page_config(page_title="PickPivot Platform", page_icon="⚡", layout="wide")

# --- 2. KONFIGURACJA ŚRODOWISKA BOTA ---
FOLDER_DOCELOWY = 'PickPivot_Data'
PLIK_KONFIGURACJI = f"{FOLDER_DOCELOWY}/historia_skanowania.json"
PLIK_WYNIKOW = f"{FOLDER_DOCELOWY}/pobierane_wyniki_awaryjne.json"

SEARCH_API_URL = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page=0&sort=parametryPozycjonowania%2Casc"
PDF_API_URL = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"
PODGLAD_URL = "https://eureka.mf.gov.pl/informacje/podglad/{id}"

FRAZY_KLUCZOWE = [
    "sieć ciepłownicza", "przebudowa sieci", "przyłącze", "węzeł cieplny", 
    "taryfa dla ciepła", "wodociąg", "sieć wodociągowa", "kanalizacja", 
    "sieć kanalizacyjna", "oczyszczalnia ścieków", "stacja uzdatniania wody", 
    "spółka komunalna"
]

KODY_PODATKOW = {
    "CIT": ".4010.",
    "VAT": ".4012."
}

if not os.path.exists(FOLDER_DOCELOWY):
    os.makedirs(FOLDER_DOCELOWY)

# --- 3. PAMIĘĆ AWARYJNA (NOWOŚĆ) ---
def wczytaj_historie():
    if os.path.exists(PLIK_KONFIGURACJI):
        with open(PLIK_KONFIGURACJI, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"przetworzone_id": []}

def zapisz_historie(konfiguracja):
    with open(PLIK_KONFIGURACJI, 'w', encoding='utf-8') as f:
        json.dump(konfiguracja, f, ensure_ascii=False, indent=4)

def wczytaj_wyniki_z_dysku():
    if os.path.exists(PLIK_WYNIKOW):
        with open(PLIK_WYNIKOW, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def dopisz_wynik_do_dysku(nowy_rekord):
    wyniki = wczytaj_wyniki_z_dysku()
    wyniki.append(nowy_rekord)
    with open(PLIK_WYNIKOW, 'w', encoding='utf-8') as f:
        json.dump(wyniki, f, ensure_ascii=False, indent=4)

def wyczysc_wyniki_z_dysku():
    if os.path.exists(PLIK_WYNIKOW):
        os.remove(PLIK_WYNIKOW)

# --- 4. FUNKCJE RDZENNE BOTA ---
def pobierz_liste_dokumentow(data_str, sesja, wybrane_kody):
    payload = {
        "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_str, "DT_WYD_end": data_str},
        "columns": ["SYG", "ID_INFORMACJI"],
        "searchInFullPhrase": False, "searchInContent": False, "searchInSynonyms": False, "warunkiDodatkowe": []
    }
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Origin": "https://eureka.mf.gov.pl"}
    
    for _ in range(3):
        try:
            response = sesja.post(SEARCH_API_URL, json=payload, headers=headers, timeout=45)
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
                    pasujacy_podatek = None
                    for nazwa_podatku, kod_sygnatury in wybrane_kody.items():
                        if kod_sygnatury in sygnatura:
                            pasujacy_podatek = nazwa_podatku
                            break
                            
                    if pasujacy_podatek:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id:
                            dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": pasujacy_podatek})
                return dokumenty_podatkowe
        except:
            time.sleep(2)
    return []

def czytaj_w_pamieci_i_filtruj(id_dokumentu):
    url = PDF_API_URL.format(id=id_dokumentu)
    headers_pdf = {"User-Agent": "Mozilla/5.0", "Referer": "https://eureka.mf.gov.pl/"}
    
    for _ in range(3):
        try:
            response = requests.get(url, headers=headers_pdf, timeout=60)
            if response.status_code == 200:
                plik_w_pamieci = io.BytesIO(response.content)
                tekst_dokumentu = ""
                try:
                    with pdfplumber.open(plik_w_pamieci) as pdf:
                        for strona in pdf.pages:
                            wyc = strona.extract_text()
                            if wyc: tekst_dokumentu += wyc.lower() + "\n"
                except:
                    return False, None
                    
                trafienia = [fraza for fraza in FRAZY_KLUCZOWE if fraza in tekst_dokumentu]
                
                if trafienia:
                    return True, trafienia[0]
                else:
                    return True, None 
            elif response.status_code in [404, 400]:
                return True, None
        except:
            time.sleep(2)
    return False, None

# --- 5. LEWY PANEL NAWIGACYJNY (SIDEBAR) ---
st.sidebar.title("📌 Menu PickPivot")
st.sidebar.markdown("---")

aktywna_zakladka = st.sidebar.radio(
    "Wybierz moduł platformy:",
    ["1", "2", "3", "4", "5", "6"]
)

st.sidebar.markdown("---")
st.sidebar.caption("© 2026 PickPivot v2.4 (Bezpieczna Pamięć)")

# --- 6. LOGIKA WYŚWIETLANIA GŁÓWNEGO EKRANU ---

if aktywna_zakladka == "1":
    st.title("⚡ PickPivot: Radar Orzecznictwa")
    st.markdown("Szybki radar branżowy. Przeszukuje bazę MF i generuje inteligentny raport Word (.docx).")

    # Sekcja pamięci awaryjnej (widoczna cały czas na górze)
    zapisane_wyniki = wczytaj_wyniki_z_dysku()
    if zapisane_wyniki:
        st.success(f"💾 PAMIĘĆ AWARYJNA: Znajduje się tu {len(zapisane_wyniki)} zachowanych orzeczeń z poprzednich lub przerwanych skanowań.")
        
        kolA, kolB = st.columns(2)
        with kolA:
            # Tworzenie Worda z pamięci awaryjnej
            doc = Document()
            doc.add_heading('Raport Orzecznictwa PickPivot', 0)
            for rekord in zapisane_wyniki:
                doc.add_heading(f"{rekord['Sygnatura']}", level=2)
                doc.add_paragraph(f"Data wydania: {rekord['Data']}")
                doc.add_paragraph(f"Podatek: {rekord['Podatek']}")
                doc.add_paragraph(f"Słowo kluczowe: {rekord['Słowo kluczowe']}")
                doc.add_paragraph(f"Link do dokumentu: {rekord['Link do dokumentu']}")
                doc.add_paragraph("-" * 50)
                
            output = io.BytesIO()
            doc.save(output)
            dane_docx = output.getvalue()
            znacznik = datetime.now().strftime('%Y%m%d_%H%M')
            
            st.download_button(
                label="📄 POBIERZ ZABEZPIECZONY RAPORT (.docx)",
                data=dane_docx,
                file_name=f"PickPivot_Raport_Odzyskany_{znacznik}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                use_container_width=True
            )
        with kolB:
            if st.button("🗑️ Wyczyść pamięć (Zacznij od zera)", use_container_width=True):
                wyczysc_wyniki_z_dysku()
                st.rerun()
                
        st.markdown("---")

    konfiguracja = wczytaj_historie()
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))

    col1, col2, col3 = st.columns(3)
    with col1:
        wybrane_lata = st.multiselect("Wybierz lata:", [2024, 2025, 2026], default=[2026])
    with col2:
        wybrane_miesiace = st.multiselect("Wybierz miesiące:", list(range(1, 13)), default=[5, 6])
    with col3:
        wybrane_podatki_ui = st.multiselect("Rodzaj podatku:", ["CIT", "VAT"], default=["CIT", "VAT"])

    if st.button("🚀 Uruchom skanowanie bazy", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace or not wybrane_podatki_ui:
            st.error("Proszę wybrać co najmniej jeden rok, jeden miesiąc oraz rodzaj podatku.")
            st.stop()

        aktywne_kody = {p: KODY_PODATKOW[p] for p in wybrane_podatki_ui}
        dzisiaj = date.today()
        
        pasek_postepu = st.progress(0)
        status_tekst = st.empty()
        okno_logow = st.container()
        
        with requests.Session() as sesja_bazy:
            calkowita_liczba_dni = len(wybrane_lata) * len(wybrane_miesiace) * 30 
            dni_przetworzone = 0
            
            for rok in wybrane_lata:
                for miesiac in wybrane_miesiace:
                    _, liczba_dni = calendar.monthrange(rok, miesiac)
                    
                    for dzien in range(1, liczba_dni + 1):
                        aktualna_data = date(rok, miesiac, dzien)
                        if aktualna_data > dzisiaj:
                            dni_przetworzone += 1
                            continue
                            
                        data_str = aktualna_data.strftime('%Y-%m-%d')
                        status_tekst.info(f"Optyczny skan dnia: {data_str}...")
                        
                        lista_dokumentow = pobierz_liste_dokumentow(data_str, sesja_bazy, aktywne_kody)
                        
                        if lista_dokumentow:
                            for dok in lista_dokumentow:
                                doc_id = dok["id"]
                                sygnatura = dok["sygnatura"]
                                typ_podatku = dok["typ"]
                                
                                if doc_id in przetworzone_id:
                                    continue
                                    
                                sukces, znaleziona_fraza = czytaj_w_pamieci_i_filtruj(doc_id)
                                
                                if sukces:
                                    przetworzone_id.add(doc_id)
                                    konfiguracja["przetworzone_id"].append(doc_id)
                                    zapisz_historie(konfiguracja)
                                    
                                    if znaleziona_fraza:
                                        nowy_rekord = {
                                            "Data": data_str,
                                            "Podatek": typ_podatku,
                                            "Sygnatura": sygnatura,
                                            "Słowo kluczowe": znaleziona_fraza.capitalize(),
                                            "Link do dokumentu": PODGLAD_URL.format(id=doc_id)
                                        }
                                        # Bezpieczny zapis w locie na dysk serwera
                                        dopisz_wynik_do_dysku(nowy_rekord)
                                        
                                        with okno_logow:
                                            st.success(f"Trafienie ({znaleziona_fraza})! -> {typ_podatku}: {sygnatura}")
                                            
                                time.sleep(random.uniform(0.1, 0.5)) # Przyspieszone pauzy
                        
                        dni_przetworzone += 1
                        postep = min(1.0, dni_przetworzone / calkowita_liczba_dni)
                        pasek_postepu.progress(postep)

        status_tekst.success("Skanowanie zakończone pomyślnie! Strona za chwilę się odświeży, aby wygenerować dokument.")
        time.sleep(2)
        st.rerun()

else:
    st.title(f"🛠️ Moduł {aktywna_zakladka}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
    st.markdown("""
    ---
    ### Planowane funkcje tego modułu:
    * Ścisła integracja z ekosystemem PickPivot.
    * Automatyzacja procesów związanych z danymi.
    * Eksport raportów na żądanie.
    
    *Aby skorzystać z działającego radaru orzecznictwa, wybierz opcję "1" z menu po lewej stronie.*
    """)
