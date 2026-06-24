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
from datetime import datetime, date
from docx import Document

# --- 1. USTAWIENIA STRONY ---
st.set_page_config(page_title="PickPivot Platform", page_icon="⚡", layout="wide")

# --- 2. KONFIGURACJA ŚRODOWISKA BOTA ---
FOLDER_DOCELOWY = 'PickPivot_Data'
PLIK_KONFIGURACJI = f"{FOLDER_DOCELOWY}/historia_skanowania.json"
PLIK_REKORDOW_JSON = f"{FOLDER_DOCELOWY}/baza_tresci_cache.json"

SEARCH_API_URL = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page=0&sort=parametryPozycjonowania%2Casc"
PDF_API_URL = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"
PODGLAD_URL = "https://eureka.mf.gov.pl/informacje/podglad/{id}"

# EKSPERCKA LISTA FRAZ: Pełne, najczęściej występujące odmiany gramatyczne.
# Dzięki "searchInFullPhrase: True" serwer MF filtruje je błyskawicznie bez pobierania PDF-ów.
FRAZY_KLUCZOWE = [
    "sieć ciepłownicza", "sieci ciepłowniczej", "sieci ciepłownicze", "sieciami ciepłowniczymi",
    "przebudowa sieci", "przebudowy sieci", "przebudowie sieci",
    "przyłącze", "przyłącza", "przyłączy", "przyłączem", "przyłączu",
    "węzeł cieplny", "węzła cieplnego", "węzły cieplne", "węzłach cieplnych",
    "taryfa dla ciepła", "taryfy dla ciepła", "taryf dla ciepła",
    "wodociąg", "wodociągu", "wodociągi", "wodociągów", "wodociągowa", "wodociągowej", "wodociągowych",
    "kanalizacja", "kanalizacji", "kanalizacyjna", "kanalizacyjnej", "kanalizacyjnych",
    "oczyszczalnia ścieków", "oczyszczalni ścieków", "ścieki", "ścieków",
    "stacja uzdatniania", "stacji uzdatniania",
    "spółka komunalna", "spółki komunalnej", "spółek komunalnych", "spółkom komunalnym"
]

KODY_PODATKOW = {
    "CIT": ".4010.",
    "VAT": ".4012.",
    "AKCYZA": ".4013."
}

if not os.path.exists(FOLDER_DOCELOWY):
    os.makedirs(FOLDER_DOCELOWY)

# --- 3. PAMIĘĆ CACHE I UTILS ---
def wczytaj_historie():
    if os.path.exists(PLIK_KONFIGURACJI):
        with open(PLIK_KONFIGURACJI, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"przetworzone_id": [], "ukonczone_kombinacje": []}

def zapisz_historie(konfiguracja):
    with open(PLIK_KONFIGURACJI, 'w', encoding='utf-8') as f:
        json.dump(konfiguracja, f, ensure_ascii=False, indent=4)

def wczytaj_pelne_tresci():
    if os.path.exists(PLIK_REKORDOW_JSON):
        with open(PLIK_REKORDOW_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def zapisz_pelne_tresci(lista_rekordow):
    with open(PLIK_REKORDOW_JSON, 'w', encoding='utf-8') as f:
        json.dump(lista_rekordow, f, ensure_ascii=False, indent=4)

def wyczysc_dane_serwera():
    if os.path.exists(PLIK_KONFIGURACJI):
        os.remove(PLIK_KONFIGURACJI)
    if os.path.exists(PLIK_REKORDOW_JSON):
        os.remove(PLIK_REKORDOW_JSON)

def wyczysc_tekst_dla_worda(tekst):
    if not tekst: return ""
    return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\u10000-\u10FFFF]', '', tekst)

# --- 4. FUNKCJE API ---
def szukaj_w_api_mf_strikt(data_start_str, data_koniec_str, fraza, sesja, nazwa_podatku, kod_sygnatury):
    payload = {
        "query": fraza,
        "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
        "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
        "searchInFullPhrase": True, # KLUCZ DO SZYBKOŚCI: Szukamy tylko dokładnych dopasowań form gramatycznych
        "searchInContent": True,    
        "searchInSynonyms": False,
        "warunkiDodatkowe": []
    }
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Origin": "https://eureka.mf.gov.pl"}
    
    for _ in range(3):
        try:
            response = sesja.post(SEARCH_API_URL, json=payload, headers=headers, timeout=30)
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
                return dokumenty_podatkowe
        except:
            time.sleep(1)
    return []

def pobierz_pelny_tekst_pypdf(id_dokumentu):
    url = PDF_API_URL.format(id=id_dokumentu)
    headers_pdf = {"User-Agent": "Mozilla/5.0", "Referer": "https://eureka.mf.gov.pl/"}
    
    for _ in range(3):
        try:
            # Dodano rygorystyczny timeout=15, aby uszkodzony plik lub zawieszony serwer nie zatrzymał aplikacji
            response = requests.get(url, headers=headers_pdf, timeout=15)
            if response.status_code == 200:
                plik_w_pamieci = io.BytesIO(response.content)
                tekst_dokumentu = ""
                try:
                    reader = PyPDF2.PdfReader(plik_w_pamieci)
                    for strona in reader.pages:
                        wyc = strona.extract_text()
                        if wyc: tekst_dokumentu += wyc + "\n"
                    return tekst_dokumentu
                except:
                    return None
            elif response.status_code in [404, 400]:
                return None
        except:
            time.sleep(1)
    return None

# --- 5. LEWY PANEL NAWIGACYJNY ---
st.sidebar.title("📌 Menu PickPivot")
st.sidebar.markdown("---")
aktywna_zakladka = st.sidebar.radio("Wybierz moduł platformy:", ["1", "2", "3", "4", "5", "6"])
st.sidebar.markdown("---")
st.sidebar.caption("© 2026 PickPivot v6.1")

# --- 6. GŁÓWNY EKRAN ---
if aktywna_zakladka == "1":
    st.title("⚡ PickPivot: Niezawodny Radar Orzecznictwa")
    st.markdown("Wersja zoptymalizowana sieciowo. Dokonuje błyskawicznej selekcji po stronie MF i pobiera wyłącznie pewne dokumenty.")

    konfiguracja = wczytaj_historie()
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))
    ukonczone_kombinacje = set(konfiguracja.get("ukonczone_kombinacje", []))
    pelne_tresci_cache = wczytaj_pelne_tresci()

    if pelne_tresci_cache:
        st.success(f"💾 BAZA DANYCH: W pamięci podręcznej zabezpieczono {len(pelne_tresci_cache)} unikalnych orzeczeń.")
        colA, colB = st.columns(2)
        with colA:
            if st.button("📄 GENERUJ I POBIERZ RAPORT WORD (.docx)", use_container_width=True, type="primary"):
                with st.spinner("Trwa błyskawiczne składanie dokumentu tekstowego..."):
                    doc = Document()
                    doc.add_heading('Baza Orzecznictwa PickPivot', 0)
                    
                    for rekord in pelne_tresci_cache:
                        doc.add_heading(f"Sygnatura: {rekord['Sygnatura']}", level=1)
                        doc.add_paragraph(f"Data wydania: {rekord['Data']}")
                        doc.add_paragraph(f"Podatek: {rekord['Podatek']}")
                        
                        p_fraza = doc.add_paragraph()
                        p_fraza.add_run("Wykryte słowo kluczowe: ").bold = True
                        p_fraza.add_run(f"{rekord['Słowo kluczowe']}")
                        
                        doc.add_paragraph(f"Link źródłowy: {rekord['Link']}")
                        doc.add_heading("Pełna treść interpretacji:", level=2)
                        
                        czysty_tekst = wyczysc_tekst_dla_worda(rekord['Tekst'])
                        doc.add_paragraph(czysty_tekst)
                        doc.add_page_break()
                    
                    output = io.BytesIO()
                    doc.save(output)
                    dane_docx = output.getvalue()
                    
                    st.download_button(
                        label="📥 Kliknij tutaj, aby zapisać plik na komputerze",
                        data=dane_docx,
                        file_name=f"PickPivot_Raport_{datetime.now().strftime('%Y%m%d_%H%M')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True
                    )
        with colB:
            if st.button("🗑️ Resetuj bazę danych (Nowy projekt)", use_container_width=True):
                wyczysc_dane_serwera()
                st.rerun()
        st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        wybrane_lata = st.multiselect("Wybierz lata:", [2024, 2025, 2026])
    with col2:
        wybrane_miesiace = st.multiselect("Wybierz miesiące:", list(range(1, 13)))
    with col3:
        wybrane_podatki_ui = st.multiselect("Rodzaj podatku:", ["CIT", "VAT", "AKCYZA"])

    if st.button("🚀 Uruchom szybkie skanowanie sekwencyjne", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace or not wybrane_podatki_ui:
            st.error("Proszę wybrać komplet parametrów.")
            st.stop()

        dzisiaj = date.today()
        pasek_postepu = st.progress(0)
        
        st.markdown("### 🖥️ Konsola Statusu")
        status_tekst = st.empty()
        
        with st.expander("Szczegółowy log operacji", expanded=True):
            okno_logow = st.container()
        
        licznik_trafien = 0
        calkowita_liczba_zapytan = len(wybrane_lata) * len(wybrane_miesiace) * len(FRAZY_KLUCZOWE) * len(wybrane_podatki_ui)
        zapytania_wykonane = 0

        with requests.Session() as sesja_bazy:
            for rok in wybrane_lata:
                for miesiac in wybrane_miesiace:
                    _, ost_dzien = calendar.monthrange(rok, miesiac)
                    data_start_str = f"{rok}-{miesiac:02d}-01"
                    data_koniec_str = f"{rok}-{miesiac:02d}-{ost_dzien:02d}"
                    
                    for fraza in FRAZY_KLUCZOWE:
                        for podatek in wybrane_podatki_ui:
                            
                            # Logowanie na żywo na ekranie
                            status_tekst.info(f"🔍 [{miesiac:02d}/{rok}] Odpytuję bazę o frazę: '{fraza}' ({podatek})...")
                            
                            klucz_kombinacji = f"{rok}_{miesiac}_{fraza}_{podatek}"
                            if klucz_kombinacji in ukonczone_kombinacje:
                                zapytania_wykonane += 1
                                postep = min(1.0, zapytania_wykonane / calkowita_liczba_zapytan)
                                pasek_postepu.progress(postep)
                                continue
                            
                            lista_trafien = szukaj_w_api_mf_strikt(data_start_str, data_koniec_str, fraza, sesja_bazy, podatek, KODY_PODATKOW[podatek])
                            
                            if lista_trafien:
                                aktualne_tresci = wczytaj_pelne_tresci()
                                
                                for dok in lista_trafien:
                                    doc_id = dok["id"]
                                    if doc_id in przetworzone_id:
                                        continue
                                        
                                    status_tekst.warning(f"⏳ Wykryto celne trafienie! Pobieram treść: {dok['sygnatura']}...")
                                    tekst_dokumentu = pobierz_pelny_tekst_pypdf(doc_id)
                                    
                                    if tekst_dokumentu:
                                        nowy_rekord = {
                                            "Data": dok["data"],
                                            "Podatek": dok["typ"],
                                            "Sygnatura": dok["sygnatura"],
                                            "Słowo kluczowe": fraza.upper(),
                                            "Link": PODGLAD_URL.format(id=doc_id),
                                            "Tekst": tekst_dokumentu
                                        }
                                        aktualne_tresci.append(nowy_rekord)
                                        przetworzone_id.add(doc_id)
                                        konfiguracja["przetworzone_id"].append(doc_id)
                                        licznik_trafien += 1
                                        
                                        with okno_logow:
                                            st.success(f"✔️ Dodano do raportu: [{fraza.upper()}] -> {dok['sygnatura']}")
                                
                                zapisz_pelne_tresci(aktualne_tresci)
                            
                            ukonczone_kombinacje.add(klucz_kombinacji)
                            konfiguracja["ukonczone_kombinacje"].append(klucz_kombinacji)
                            zapisz_historie(konfiguracja)
                            
                            zapytania_wykonane += 1
                            postep = min(1.0, zapytania_wykonane / calkowita_liczba_zapytan)
                            pasek_postepu.progress(postep)
                            
                            # Bardzo krótka przerwa dla stabilności połączenia
                            time.sleep(random.uniform(0.1, 0.2))

        status_tekst.success(f"🎉 WSZYSTKIE WYSZUKIWANIA ZOSTAŁY ZAKOŃCZONE! Zebrano łącznie {licznik_trafien} unikalnych interpretacji.")
        st.balloons()
        time.sleep(3)
        st.rerun()

else:
    st.title(f"🛠️ Moduł {aktywna_zakladka}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
