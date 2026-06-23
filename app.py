import streamlit as st
import requests
import PyPDF2
import pdfplumber
import time
import random
import os
import json
import io
import calendar
from datetime import datetime, date

# --- KONFIGURACJA ŚRODOWISKA CHMUROWEGO ---
FOLDER_DOCELOWY = 'PickPivot_Temp'
PLIK_KONFIGURACJI = f"{FOLDER_DOCELOWY}/konfiguracja_bota.json"

SEARCH_API_URL = "https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=100&page=0&sort=parametryPozycjonowania%2Casc"
PDF_API_URL = "https://eureka.mf.gov.pl/api/public/v1/informacje/{id}/eksport/pdf"

FRAZY_KLUCZOWE = [
    "sieć ciepłownicza", "przebudowa sieci", "przyłącze", "węzeł cieplny", 
    "taryfa dla ciepła", "wodociąg", "sieć wodociągowa", "kanalizacja", 
    "sieć kanalizacyjna", "oczyszczalnia ścieków", "stacja uzdatniania wody", 
    "spółka komunalna"
]

if not os.path.exists(FOLDER_DOCELOWY):
    os.makedirs(FOLDER_DOCELOWY)

def wczytaj_konfiguracje():
    if os.path.exists(PLIK_KONFIGURACJI):
        with open(PLIK_KONFIGURACJI, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"przetworzone_id": []}

def zapisz_konfiguracje(konfiguracja):
    with open(PLIK_KONFIGURACJI, 'w', encoding='utf-8') as f:
        json.dump(konfiguracja, f, ensure_ascii=False, indent=4)

def pobierz_id_cit_dla_dnia(data_str, sesja):
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
                return [str(d.get('id') or d.get('ID_INFORMACJI')) for d in wyniki if '.4010.' in str(d.get('SYG', '')).upper() and (d.get('id') or d.get('ID_INFORMACJI'))]
        except:
            time.sleep(2)
    return []

def skanuj_i_zapisz_jesli_pasuje(id_dokumentu):
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
                    sciezka_pdf = f"{FOLDER_DOCELOWY}/CIT_{id_dokumentu}.pdf"
                    with open(sciezka_pdf, 'wb') as f:
                        f.write(response.content)
                    return True, sciezka_pdf
                else:
                    return True, None 
            elif response.status_code in [404, 400]:
                return True, None
        except:
            time.sleep(2)
    return False, None

# --- INTERFEJS UŻYTKOWNIKA ---
st.set_page_config(page_title="PickPivot Radar", page_icon="🎯", layout="centered")
st.title("🎯 PickPivot: Radar Branżowy")
st.markdown("Automatyczny agregator orzecznictwa CIT dla branży Ciepłowniczej i Wod-Kan.")

konfiguracja = wczytaj_konfiguracje()
przetworzone_id = set(konfiguracja.get("przetworzone_id", []))

col1, col2 = st.columns(2)
with col1:
    wybrane_lata = st.multiselect("Wybierz lata:", [2024, 2025, 2026], default=[2026])
with col2:
    wybrane_miesiace = st.multiselect("Wybierz miesiące:", list(range(1, 13)), default=[1, 2, 3])

if st.button("🚀 Uruchom skanowanie bazy", use_container_width=True):
    if not wybrane_lata or not wybrane_miesiace:
        st.error("Proszę wybrać co najmniej jeden rok i jeden miesiąc.")
        st.stop()

    sciezki_do_scalenia = []
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
                    status_tekst.info(f"Skanowanie dnia: {data_str}...")
                    
                    lista_id = pobierz_id_cit_dla_dnia(data_str, sesja_bazy)
                    
                    if lista_id:
                        for doc_id in lista_id:
                            sciezka_docelowa = f"{FOLDER_DOCELOWY}/CIT_{doc_id}.pdf"
                            
                            if doc_id in przetworzone_id:
                                if os.path.exists(sciezka_docelowa):
                                    sciezki_do_scalenia.append(sciezka_docelowa)
                                continue
                                
                            sukces, sciezka_pdf = skanuj_i_zapisz_jesli_pasuje(doc_id)
                            
                            if sukces:
                                przetworzone_id.add(doc_id)
                                konfiguracja["przetworzone_id"].append(doc_id)
                                if sciezka_pdf:
                                    sciezki_do_scalenia.append(sciezka_pdf)
                                    with okno_logow:
                                        st.success(f"Znaleziono dokument branżowy! (ID: {doc_id})")
                                        
                            time.sleep(random.uniform(0.5, 1.5))
                    
                    zapisz_konfiguracje(konfiguracja)
                    
                    dni_przetworzone += 1
                    postep = min(1.0, dni_przetworzone / calkowita_liczba_dni)
                    pasek_postepu.progress(postep)

    status_tekst.success("Proces skanowania i filtrowania zakończony!")
    pasek_postepu.progress(1.0)
    
    if sciezki_do_scalenia:
        st.info(f"Trwa scalanie {len(sciezki_do_scalenia)} plików PDF w jeden raport...")
        merger = PyPDF2.PdfMerger()
        for sciezka in sciezki_do_scalenia:
            try:
                merger.append(sciezka)
            except Exception as e:
                st.error(f"Błąd pliku {sciezka}: {e}")
                
        znacznik = datetime.now().strftime('%Y%m%d_%H%M')
        nazwa_rap = f"{FOLDER_DOCELOWY}/Raport_PickPivot_{znacznik}.pdf"
        
        merger.write(nazwa_rap)
        merger.close()
        st.success(f"Raport PDF jest gotowy!")
        
        with open(nazwa_rap, "rb") as gotowy_pdf:
            st.download_button(
                label="📥 Pobierz gotowy Raport PDF na komputer",
                data=gotowy_pdf,
                file_name=f"PickPivot_Wodkan_Cieplo_{znacznik}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )
    else:
        st.warning("W wybranych okresach nie opublikowano żadnych interpretacji z Twoimi słowami kluczowymi.")
