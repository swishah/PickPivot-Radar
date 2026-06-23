import streamlit as st
import requests
import pdfplumber
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
PLIK_RAPORTU = f"{FOLDER_DOCELOWY}/Raport_Zbiorczy.docx"

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

# --- 3. PAMIĘĆ AWARYJNA I ZAPIS DOCX ---
def wczytaj_historie():
    if os.path.exists(PLIK_KONFIGURACJI):
        with open(PLIK_KONFIGURACJI, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"przetworzone_id": []}

def zapisz_historie(konfiguracja):
    with open(PLIK_KONFIGURACJI, 'w', encoding='utf-8') as f:
        json.dump(konfiguracja, f, ensure_ascii=False, indent=4)

def wyczysc_dane_serwera():
    if os.path.exists(PLIK_KONFIGURACJI):
        os.remove(PLIK_KONFIGURACJI)
    if os.path.exists(PLIK_RAPORTU):
        os.remove(PLIK_RAPORTU)

def wyczysc_tekst_dla_worda(tekst):
    if not tekst: return ""
    return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\u10000-\u10FFFF]', '', tekst)

def dopisz_do_worda(sygnatura, data_str, typ_podatku, znaleziona_fraza, link, tekst_dokumentu):
    if os.path.exists(PLIK_RAPORTU):
        doc = Document(PLIK_RAPORTU)
    else:
        doc = Document()
        doc.add_heading('Baza Orzecznictwa PickPivot', 0)

    doc.add_heading(f"Sygnatura: {sygnatura}", level=1)
    doc.add_paragraph(f"Data wydania: {data_str}")
    doc.add_paragraph(f"Podatek: {typ_podatku}")
    doc.add_paragraph(f"Znalazłem frazę: {znaleziona_fraza.capitalize()}")
    doc.add_paragraph(f"Link źródłowy: {link}")
    
    doc.add_heading("Pełna treść interpretacji:", level=2)
    
    czysty_tekst = wyczysc_tekst_dla_worda(tekst_dokumentu)
    doc.add_paragraph(czysty_tekst)
    doc.add_page_break() 
    doc.save(PLIK_RAPORTU)

# --- 4. ZAAWANSOWANE FUNKCJE API (DELEGACJA NA SERWERY MF) ---
def szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja, wybrane_kody):
    """Zleca wyszukiwanie pełnej frazy serwerom Ministerstwa Finansów"""
    payload = {
        "query": fraza,
        "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
        "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
        "searchInFullPhrase": True, # BEZWZGLĘDNY KLUCZ DO PRĘDKOŚCI
        "searchInContent": True,    # Przeszukuje PDF po stronie MF
        "searchInSynonyms": False,
        "warunkiDodatkowe": []
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
                    data_wydania = str(d.get('DT_WYD', '')).split('T')[0]
                    
                    pasujacy_podatek = None
                    for nazwa_podatku, kod_sygnatury in wybrane_kody.items():
                        if kod_sygnatury in sygnatura:
                            pasujacy_podatek = nazwa_podatku
                            break
                            
                    if pasujacy_podatek:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id:
                            dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": pasujacy_podatek, "data": data_wydania})
                return dokumenty_podatkowe
        except:
            time.sleep(2)
    return []

def pobierz_tylko_tekst(id_dokumentu):
    """Pobiera PDF tylko i wyłącznie wtedy, gdy wiemy na 100%, że to trafienie"""
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
                            if wyc: tekst_dokumentu += wyc + "\n"
                    return tekst_dokumentu
                except:
                    return None
        except:
            time.sleep(2)
    return None

# --- 5. LEWY PANEL NAWIGACYJNY ---
st.sidebar.title("📌 Menu PickPivot")
st.sidebar.markdown("---")
aktywna_zakladka = st.sidebar.radio("Wybierz moduł platformy:", ["1", "2", "3", "4", "5", "6"])
st.sidebar.markdown("---")
st.sidebar.caption("© 2026 PickPivot v4.0 (Ultra-Fast API)")

# --- 6. LOGIKA WYŚWIETLANIA GŁÓWNEGO EKRANU ---
if aktywna_zakladka == "1":
    st.title("⚡ PickPivot: Niezawodny Radar Orzecznictwa")
    st.markdown("Wersja zoptymalizowana. Deleguje wyszukiwanie do serwerów MF, pobierając pliki tylko przy trafieniu i budując dokument iteracyjnie (krok po kroku).")

    konfiguracja = wczytaj_historie()
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))

    if os.path.exists(PLIK_RAPORTU):
        st.success(f"💾 PAMIĘĆ AKTYWNA: Skrypt zapamiętał {len(przetworzone_id)} znalezionych już dokumentów.")
        colA, colB = st.columns(2)
        with colA:
            with open(PLIK_RAPORTU, "rb") as f:
                st.download_button(
                    label="📥 POBIERZ DOTYCHCZASOWY PLIK WORD (.docx)",
                    data=f,
                    file_name=f"PickPivot_Baza_{datetime.now().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="primary",
                    use_container_width=True
                )
        with colB:
            if st.button("🗑️ Resetuj bazę (Rozpocznij nowy, czysty plik)", use_container_width=True):
                wyczysc_dane_serwera()
                st.rerun()
        st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        wybrane_lata = st.multiselect("Wybierz lata:", [2024, 2025, 2026], default=[2026])
    with col2:
        wybrane_miesiace = st.multiselect("Wybierz miesiące:", list(range(1, 13)), default=[5, 6])
    with col3:
        wybrane_podatki_ui = st.multiselect("Rodzaj podatku:", ["CIT", "VAT"], default=["CIT", "VAT"])

    if st.button("🚀 Uruchom błyskawiczne skanowanie API", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace or not wybrane_podatki_ui:
            st.error("Proszę wybrać co najmniej jeden rok, jeden miesiąc oraz rodzaj podatku.")
            st.stop()

        aktywne_kody = {p: KODY_PODATKOW[p] for p in wybrane_podatki_ui}
        
        pasek_postepu = st.progress(0)
        status_tekst = st.empty()
        okno_logow = st.container()
        
        licznik_trafien = 0
        calkowita_liczba_zapytan = len(wybrane_lata) * len(wybrane_miesiace) * len(FRAZY_KLUCZOWE)
        zapytania_wykonane = 0

        with requests.Session() as sesja_bazy:
            for rok in wybrane_lata:
                for miesiac in wybrane_miesiace:
                    _, ost_dzien = calendar.monthrange(rok, miesiac)
                    data_start_str = f"{rok}-{miesiac:02d}-01"
                    data_koniec_str = f"{rok}-{miesiac:02d}-{ost_dzien:02d}"
                    
                    for fraza in FRAZY_KLUCZOWE:
                        status_tekst.info(f"Pytam serwery MF o: '{fraza}' w {miesiac:02d}/{rok}...")
                        
                        lista_trafien = szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja_bazy, aktywne_kody)
                        
                        for dok in lista_trafien:
                            doc_id = dok["id"]
                            
                            # Dzięki temu nie dodamy dwa razy tego samego dokumentu, jeśli ma dwa słowa kluczowe
                            if doc_id in przetworzone_id:
                                continue
                                
                            status_tekst.warning(f"Znalazłem trafienie ({dok['sygnatura']})! Pobieram treść do Worda...")
                            tekst_dokumentu = pobierz_tylko_tekst(doc_id)
                            
                            if tekst_dokumentu:
                                link = PODGLAD_URL.format(id=doc_id)
                                dopisz_do_worda(dok["sygnatura"], dok["data"], dok["typ"], fraza, link, tekst_dokumentu)
                                
                                przetworzone_id.add(doc_id)
                                konfiguracja["przetworzone_id"].append(doc_id)
                                zapisz_historie(konfiguracja)
                                licznik_trafien += 1
                                
                                with okno_logow:
                                    st.success(f"Dodano treść do pliku Word! ({fraza}) -> {dok['typ']}: {dok['sygnatura']}")
                        
                        zapytania_wykonane += 1
                        postep = min(1.0, zapytania_wykonane / calkowita_liczba_zapytan)
                        pasek_postepu.progress(postep)
                        time.sleep(random.uniform(0.2, 0.6))

        status_tekst.success(f"Zakończono ultra-szybkie skanowanie! W tej sesji pobrano i doklejono {licznik_trafien} trafnych interpretacji.")
        pasek_postepu.progress(1.0)
        time.sleep(2)
        st.rerun()

else:
    st.title(f"🛠️ Moduł {aktywna_zakladka}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
