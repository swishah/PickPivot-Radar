import streamlit as st
import time
import calendar
import io
import requests
from datetime import datetime
from docx import Document
from utils import (PLIK_KONFIGURACJI_M1, PLIK_REKORDOW_M1, PODGLAD_URL, FRAZY_KLUCZOWE, KODY_PODATKOW, MIESIACE_PL,
                   wczytaj_historie, zapisz_historie, wczytaj_pelne_tresci, zapisz_pelne_tresci, 
                   wyczysc_dane_serwera, wyczysc_tekst_dla_worda, pobierz_tekst_pdf, szukaj_w_api_mf)

def run_module():
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
    with col2: wybrane_miesiace_ui = st.multiselect("Wybierz miesiące:", MIESIACE_PL)
    with col3: wybrane_podatki_ui = st.multiselect("Rodzaj podatku:", ["PIT", "CIT", "VAT", "AKCYZA"], default=["VAT"])

    if st.button("🚀 Uruchom skanowanie słów kluczowych", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace_ui or not wybrane_podatki_ui:
            st.error("Proszę wybrać komplet parametrów.")
            st.stop()
            
        wybrane_miesiace = [MIESIACE_PL.index(m) + 1 for m in wybrane_miesiace_ui]
            
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
                            
                            status_tekst.info(f"Radar odpytuje: {fraza} ({podatek}) dla okresu {miesiac:02d}/{rok}...")
                            lista_trafien, _ = szukaj_w_api_mf(data_start_str, data_koniec_str, fraza, sesja_bazy, podatek, KODY_PODATKOW[podatek])
                            
                            if lista_trafien:
                                aktualne_tresci = wczytaj_pelne_tresci(PLIK_REKORDOW_M1)
                                for dok in lista_trafien:
                                    if dok["id"] not in przetworzone_id:
                                        tekst, status_pobr = pobierz_tekst_pdf(dok["id"])
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
