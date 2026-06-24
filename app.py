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

# --- 2. KONFIGURACJA ŚRODOWISKA ---
FOLDER_DOCELOWY = 'PickPivot_Data'
PLIK_KONFIGURACJI = f"{FOLDER_DOCELOWY}/historia_skanowania.json"
PLIK_REKORDOW_JSON = f"{FOLDER_DOCELOWY}/baza_tresci_cache.json"

SEARCH_API_URL = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page=0&sort=parametryPozycjonowania%2Casc"
PDF_API_URL = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"
PODGLAD_URL = "https://eureka.mf.gov.pl/informacje/podglad/{id}"

# Słownik Weryfikacji: KLUCZ to zapytanie do MF, WARTOŚĆ to rdzeń, który MUSI być w PDF.
SLOWNIK_WERYFIKACJI = {
    "sieć ciepłownicza": "ciepłownicz",
    "przebudowa sieci": "przebudow",
    "przyłącze": "przyłącz",
    "węzeł cieplny": "ciepln",
    "taryfa dla ciepła": "taryf",
    "wodociąg": "wodociąg",
    "kanalizacja": "kanalizac",
    "oczyszczalnia ścieków": "ściek",
    "stacja uzdatniania": "uzdatnian",
    "spółka komunalna": "komunaln"
}

FRAZY_KLUCZOWE = list(SLOWNIK_WERYFIKACJI.keys())

KODY_PODATKOW = {
    "CIT": ".4010.",
    "VAT": ".4012.",
    "AKCYZA": ".4013."
}

if not os.path.exists(FOLDER_DOCELOWY):
    os.makedirs(FOLDER_DOCELOWY)

# --- 3. BAZA DANYCH ---
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

# --- 4. FUNKCJE API Z ŻELAZNĄ WERYFIKACJĄ ---
def szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja, nazwa_podatku, kod_sygnatury):
    payload = {
        "query": fraza,
        "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
        "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
        "searchInFullPhrase": False, 
        "searchInContent": False,    
        "searchInSynonyms": True,    
        "warunkiDodatkowe": []
    }
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Origin": "https://eureka.mf.gov.pl"}
    
    try:
        response = sesja.post(SEARCH_API_URL, json=payload, headers=headers, timeout=10)
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

def pobierz_i_zweryfikuj_tekst(id_dokumentu, rdzen_weryfikacyjny):
    """Pobiera PDF i weryfikuje czy słowo NAPRAWDĘ znajduje się w tekście"""
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
            
            # WERYFIKACJA LOKALNA (CENZOR)
            tekst_do_sprawdzenia = tekst_dokumentu.lower()
            if rdzen_weryfikacyjny in tekst_do_sprawdzenia:
                return tekst_dokumentu # Sukces, słowo faktycznie tam jest
            else:
                return "ODRZUCONE" # MF kłamało, słowa nie ma w tekście
    except:
        return None
    return None

# --- 5. INTERFEJS ---
st.sidebar.title("📌 Menu PickPivot")
st.sidebar.markdown("---")
aktywna_zakladka = st.sidebar.radio("Wybierz moduł platformy:", ["1", "2", "3", "4", "5", "6"])
st.sidebar.markdown("---")
st.sidebar.caption("© 2026 PickPivot v7.0 (Weryfikacja Lokalna)")

# --- 6. GŁÓWNA LOGIKA ---
if aktywna_zakladka == "1":
    st.title("⚡ PickPivot: Niezawodny Radar Orzecznictwa")
    st.markdown("Wersja z **Żelazną Weryfikacją**. Skrypt pobiera dokumenty z MF, ale odrzuca je, jeśli nie znajdzie faktycznego słowa w treści.")

    konfiguracja = wczytaj_historie()
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))
    ukonczone_kombinacje = set(konfiguracja.get("ukonczone_kombinacje", []))
    pelne_tresci_cache = wczytaj_pelne_tresci()

    if pelne_tresci_cache:
        st.success(f"💾 BAZA DANYCH: W pamięci zabezpieczono {len(pelne_tresci_cache)} poprawnie zweryfikowanych orzeczeń.")
        colA, colB = st.columns(2)
        with colA:
            if st.button("📄 GENERUJ I POBIERZ RAPORT WORD (.docx)", use_container_width=True, type="primary"):
                with st.spinner("Składanie raportu..."):
                    doc = Document()
                    doc.add_heading('Baza Orzecznictwa PickPivot', 0)
                    
                    for rekord in pelne_tresci_cache:
                        doc.add_heading(f"Sygnatura: {rekord['Sygnatura']}", level=1)
                        doc.add_paragraph(f"Data wydania: {rekord['Data']}")
                        doc.add_paragraph(f"Podatek: {rekord['Podatek']}")
                        
                        p_fraza = doc.add_paragraph()
                        p_fraza.add_run("Potwierdzona fraza kluczowa: ").bold = True
                        p_fraza.add_run(f"{rekord['Słowo kluczowe']}")
                        
                        doc.add_paragraph(f"Link: {rekord['Link']}")
                        doc.add_heading("Treść interpretacji:", level=2)
                        
                        czysty_tekst = wyczysc_tekst_dla_worda(rekord['Tekst'])
                        doc.add_paragraph(czysty_tekst)
                        doc.add_page_break()
                    
                    output = io.BytesIO()
                    doc.save(output)
                    st.download_button(
                        label="📥 Zapisz raport na dysku",
                        data=output.getvalue(),
                        file_name=f"PickPivot_Raport_{datetime.now().strftime('%Y%m%d_%H%M')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True
                    )
        with colB:
            if st.button("🗑️ Resetuj bazę danych", use_container_width=True):
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

    if st.button("🚀 Uruchom inteligentne skanowanie", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace or not wybrane_podatki_ui:
            st.error("Proszę wybrać komplet parametrów.")
            st.stop()

        dzisiaj = date.today()
        pasek_postepu = st.progress(0)
        
        st.markdown("### 📊 Tablica Weryfikacji (Na Żywo)")
        macierz_danych = {"Fraza kluczowa": FRAZY_KLUCZOWE}
        for p in wybrane_podatki_ui:
            macierz_danych[p] = ["⏳ Oczekiwanie"] * len(FRAZY_KLUCZOWE)
            
        df_postepu = pd.DataFrame(macierz_danych)
        widok_tabeli = st.dataframe(df_postepu, use_container_width=True, hide_index=True)
        
        calkowita_liczba_zapytan = len(wybrane_lata) * len(wybrane_miesiace) * len(FRAZY_KLUCZOWE) * len(wybrane_podatki_ui)
        zapytania_wykonane = 0

        with requests.Session() as sesja_bazy:
            for rok in wybrane_lata:
                for miesiac in wybrane_miesiace:
                    _, ost_dzien = calendar.monthrange(rok, miesiac)
                    data_start_str = f"{rok}-{miesiac:02d}-01"
                    data_koniec_str = f"{rok}-{miesiac:02d}-{ost_dzien:02d}"
                    
                    for idx_fraza, fraza in enumerate(FRAZY_KLUCZOWE):
                        rdzen = SLOWNIK_WERYFIKACJI[fraza]
                        
                        for podatek in wybrane_podatki_ui:
                            klucz_kombinacji = f"{rok}_{miesiac}_{fraza}_{podatek}"
                            
                            if klucz_kombinacji in ukonczone_kombinacje:
                                zapytania_wykonane += 1
                                continue
                            
                            df_postepu.at[idx_fraza, podatek] = "🔍 Odpytywanie MF..."
                            widok_tabeli.dataframe(df_postepu, use_container_width=True, hide_index=True)
                            
                            lista_trafien, stan_sieci = szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja_bazy, podatek, KODY_PODATKOW[podatek])
                            
                            if stan_sieci != "OK":
                                df_postepu.at[idx_fraza, podatek] = "⚠️ Błąd serwera"
                                widok_tabeli.dataframe(df_postepu, use_container_width=True, hide_index=True)
                            elif lista_trafien:
                                df_postepu.at[idx_fraza, podatek] = f"📖 Weryfikacja {len(lista_trafien)} dok..."
                                widok_tabeli.dataframe(df_postepu, use_container_width=True, hide_index=True)
                                
                                aktualne_tresci = wczytaj_pelne_tresci()
                                zaakceptowane = 0
                                odrzucone = 0
                                
                                for dok in lista_trafien:
                                    doc_id = dok["id"]
                                    if doc_id in przetworzone_id:
                                        continue
                                        
                                    tekst_wynik = pobierz_i_zweryfikuj_tekst(doc_id, rdzen)
                                    
                                    if tekst_wynik == "ODRZUCONE":
                                        odrzucone += 1
                                        przetworzone_id.add(doc_id)
                                        konfiguracja["przetworzone_id"].append(doc_id)
                                    elif tekst_wynik:
                                        nowy_rekord = {
                                            "Data": dok["data"],
                                            "Podatek": dok["typ"],
                                            "Sygnatura": dok["sygnatura"],
                                            "Słowo kluczowe": fraza.upper(),
                                            "Link": PODGLAD_URL.format(id=doc_id),
                                            "Tekst": tekst_wynik
                                        }
                                        aktualne_tresci.append(nowy_rekord)
                                        przetworzone_id.add(doc_id)
                                        konfiguracja["przetworzone_id"].append(doc_id)
                                        zaakceptowane += 1
                                
                                zapisz_pelne_tresci(aktualne_tresci)
                                df_postepu.at[idx_fraza, podatek] = f"✔️ Trafień: {zaakceptowane} | 🗑️ Odrzucono: {odrzucone}"
                                widok_tabeli.dataframe(df_postepu, use_container_width=True, hide_index=True)
                            else:
                                df_postepu.at[idx_fraza, podatek] = "❌ Brak wyników"
                                widok_tabeli.dataframe(df_postepu, use_container_width=True, hide_index=True)
                            
                            ukonczone_kombinacje.add(klucz_kombinacji)
                            konfiguracja["ukonczone_kombinacje"].append(klucz_kombinacji)
                            zapisz_historie(konfiguracja)
                            
                            zapytania_wykonane += 1
                            postep = min(1.0, zapytania_wykonane / calkowita_liczba_zapytan)
                            pasek_postepu.progress(postep)
                            time.sleep(random.uniform(0.1, 0.3))

        st.success("🎉 ZAKOŃCZONO! System odfiltrował błędne dokumenty z MF i zapisał tylko zweryfikowane.")
        st.balloons()
        time.sleep(4)
        st.rerun()

else:
    st.title(f"🛠️ Moduł {aktywna_zakladka}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
