import streamlit as st
import time
import calendar
import io
import requests
from datetime import datetime
from docx import Document
import utils

def run_module():
    st.title("⚡ PickPivot: Radar Orzecznictwa")
    st.markdown("Wyszukuje interpretacje podatkowe na podstawie słów kluczowych.")

    konfiguracja = utils.wczytaj_historie(utils.PLIK_KONFIGURACJI_M1)
    przetworzone_id = set(konfiguracja.get("przetworzone_id", []))
    ukonczone_kombinacje = set(konfiguracja.get("ukonczone_kombinacje", []))
    pelne_tresci_cache = utils.wczytaj_pelne_tresci(utils.PLIK_REKORDOW_M1)

    if pelne_tresci_cache:
        st.success(f"💾 Zabezpieczono {len(pelne_tresci_cache)} orzeczeń.")
        colA, colB = st.columns(2)
        with colA:
            if st.button("📄 GENERUJ RAPORT WORD", use_container_width=True, type="primary"):
                doc = Document()
                for rekord in pelne_tresci_cache:
                    doc.add_heading(f"Sygnatura: {rekord['Sygnatura']}", level=1)
                    doc.add_paragraph(f"Data: {rekord['Data']} | Podatek: {rekord['Podatek']}")
                    doc.add_paragraph(f"Link: {rekord['Link']}")
                    doc.add_paragraph(utils.wyczysc_tekst_dla_worda(rekord['Tekst']))
                    doc.add_page_break()
                output = io.BytesIO()
                doc.save(output)
                st.download_button("📥 Pobierz plik", data=output.getvalue(), file_name="Radar.docx", use_container_width=True)
        with colB:
            if st.button("🗑️ Resetuj bazę", use_container_width=True):
                utils.wyczysc_dane_serwera(utils.PLIK_KONFIGURACJI_M1, utils.PLIK_REKORDOW_M1)
                st.rerun()

    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata = st.multiselect("Lata:", [2024, 2025, 2026])
    with col2: wybrane_miesiace_ui = st.multiselect("Miesiące:", utils.MIESIACE_PL)
    with col3: wybrane_podatki_ui = st.multiselect("Podatki:", ["PIT", "CIT", "VAT", "AKCYZA"], default=["VAT"])

    if st.button("🚀 Uruchom skanowanie", use_container_width=True):
        if not wybrane_lata or not wybrane_miesiace_ui or not wybrane_podatki_ui:
            st.error("Proszę wybrać parametry."); st.stop()
            
        wybrane_miesiace = [utils.MIESIACE_PL.index(m) + 1 for m in wybrane_miesiace_ui]
        pasek_postepu = st.progress(0)
        status_tekst = st.empty()
        licznik_trafien, zapytania_wykonane = 0, 0
        calkowita_liczba_zapytan = len(wybrane_lata) * len(wybrane_miesiace) * len(utils.FRAZY_KLUCZOWE) * len(wybrane_podatki_ui)

        with requests.Session() as sesja_bazy:
            for rok in wybrane_lata:
                for miesiac in wybrane_miesiace:
                    _, ost_dzien = calendar.monthrange(rok, miesiac)
                    d_start = f"{rok}-{miesiac:02d}-01"
                    d_koniec = f"{rok}-{miesiac:02d}-{ost_dzien:02d}"
                    
                    for fraza in utils.FRAZY_KLUCZOWE:
                        for podatek in wybrane_podatki_ui:
                            klucz = f"M1_{rok}_{miesiac}_{fraza}_{podatek}"
                            if klucz in ukonczone_kombinacje:
                                zapytania_wykonane += 1; continue
                            
                            status_tekst.info(f"Skanowanie: {fraza} ({podatek})...")
                            lista_trafien, _ = utils.szukaj_w_api_mf(d_start, d_koniec, fraza, sesja_bazy, podatek, utils.KODY_PODATKOW[podatek])
                            
                            if lista_trafien:
                                aktualne_tresci = utils.wczytaj_pelne_tresci(utils.PLIK_REKORDOW_M1)
                                for dok in lista_trafien:
                                    if dok["id"] not in przetworzone_id:
                                        tekst, _ = utils.pobierz_tekst_pdf(dok["id"])
                                        if tekst:
                                            aktualne_tresci.append({"Data": dok["data"], "Podatek": dok["typ"], "Sygnatura": dok["sygnatura"], "Słowo kluczowe": fraza.upper(), "Link": utils.PODGLAD_URL.format(id=dok["id"]), "Tekst": tekst})
                                            przetworzone_id.add(dok["id"])
                                            konfiguracja["przetworzone_id"].append(dok["id"])
                                            licznik_trafien += 1
                                utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M1, aktualne_tresci)
                            
                            ukonczone_kombinacje.add(klucz)
                            konfiguracja["ukonczone_kombinacje"].append(klucz)
                            utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M1, konfiguracja)
                            zapytania_wykonane += 1
                            pasek_postepu.progress(min(1.0, zapytania_wykonane / calkowita_liczba_zapytan))
                            
        status_tekst.success(f"Zakończono! Zebrano {licznik_trafien} dokumentów."); st.rerun()
