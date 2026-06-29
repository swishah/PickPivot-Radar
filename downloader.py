"""
downloader.py — Ściągacz Interpretacji v3 (Supabase)

Przepływ dla każdego zapytania:
  1. Sprawdź bazę Supabase → zwróć co już mamy (bez odpytywania MF).
  2. Dla brakujących ID → odpytaj API MF i pobierz równolegle.
  3. Nowe rekordy → zapisz do bazy Supabase.
  4. Generuj Word z danych z bazy + nowo pobranych.
"""

import streamlit as st
import time
import io
import requests
import calendar
from docx import Document
from datetime import datetime

import utils
import archiwum_supabase as archiwum

CZAS_LOCKOUT_S = 300

def _generuj_word(rekordy: list, filtry_opis: str = "") -> bytes:
    doc = Document()
    doc.add_heading("PickPivot – Archiwum Interpretacji Podatkowych", 0)
    doc.add_paragraph(f"Wygenerowano: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if filtry_opis:
        doc.add_paragraph(f"Zakres: {filtry_opis}")
    doc.add_paragraph(f"Liczba dokumentów: {len(rekordy)}")
    doc.add_page_break()
    for r in sorted(rekordy, key=lambda x: x.get("Data", ""), reverse=True):
        doc.add_heading(f"Sygnatura: {r['Sygnatura']}", 1)
        doc.add_paragraph(f"Data: {r['Data']} | Podatek: {r['Podatek']}")
        doc.add_paragraph(f"Link: {r['Link']}")
        doc.add_paragraph(utils.wyczysc_tekst_dla_worda(r['Tekst'][:1500]) + "\n...[Skrócone]")
        doc.add_page_break()
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()

def run_module():
    st.title("📦 Ściągacz Interpretacji (Supabase)")
    
    statystyki = archiwum.pobierz_statystyki()
    if not statystyki["polaczenie"]:
        st.warning("Brak aktywnego połączenia z bazą Supabase. Skrypt zadziała tylko lokalnie.")
    else:
        st.success(f"Baza podłączona | Rekordów: {statystyki['total']} | Ostatni zapis: {statystyki['ostatnie_pobranie']}")

    if st.session_state.get('lockout_active_m2', False):
        st.error("🔒 OCHRONA ANTY-BAN: Trwa pauza (ok. 5 min)")
        el = time.time() - st.session_state.get('lockout_start_m2', 0)
        if el < CZAS_LOCKOUT_S:
            st.info(f"Odczekaj... ({int(CZAS_LOCKOUT_S - el)} s)")
            time.sleep(1)
            st.rerun()
        else:
            if st.button("▶️ WZNÓW PRZERWANE", type="primary"):
                st.session_state['lockout_active_m2'] = False
                st.session_state['auto_resume_m2'] = True
                st.rerun()
            st.stop()

    konfig = utils.wczytaj_historie(utils.PLIK_KONFIGURACJI_M2)
    pelne_tresci_lokalne = utils.wczytaj_pelne_tresci(utils.PLIK_REKORDOW_M2)

    if pelne_tresci_lokalne:
        if st.button("📄 GENERUJ ARCHIWUM WORD (Z PAMIĘCI TYMCZASOWEJ)", type="primary"):
            b = _generuj_word(pelne_tresci_lokalne, "Tymczasowe")
            st.download_button("📥 Pobierz Archiwum", b, "Tymczasowe.docx")
        st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata = st.multiselect("Lata:", [2024, 2025, 2026], key="l2")
    with col2: wybrane_miesiace_ui = st.multiselect("Miesiące:", utils.MIESIACE_PL, key="m2")
    with col3: wybrane_podatki = st.multiselect("Podatki:", ["PIT", "CIT", "VAT", "AKCYZA"], default=["VAT"], key="p2")

    c1, c2 = st.columns(2)
    with c1: btn_wznow = st.button("▶️ Wznów pobieranie")
    with c2: btn_od_nowa = st.button("🔄 Pobierz od nowa")

    if not (btn_wznow or btn_od_nowa or st.session_state.get('auto_resume_m2', False)):
        return

    if btn_od_nowa:
        utils.wyczysc_dane_serwera(utils.PLIK_KONFIGURACJI_M2, utils.PLIK_REKORDOW_M2)
        konfig = {"przetworzone_id": [], "ukonczone_kombinacje": [], "uszkodzone_id": []}
        pelne_tresci_lokalne = []
        st.session_state.pop('queue_m2', None)

    if st.session_state.get('auto_resume_m2', False) and 'queue_m2' in st.session_state:
        do_pobrania = st.session_state['queue_m2']
    else:
        if not wybrane_lata or not wybrane_miesiace_ui or not wybrane_podatki:
            st.error("Proszę wybrać parametry."); st.stop()
            
        wybrane_miesiace = [utils.MIESIACE_PL.index(m) + 1 for m in wybrane_miesiace_ui]
        do_pobrania = []
        bledy_api = 0
        
        status_zliczania = st.empty()
        with requests.Session() as sesja:
            for rok in wybrane_lata:
                for mies in wybrane_miesiace:
                    _, ost = calendar.monthrange(rok, mies)
                    for pod in wybrane_podatki:
                        status_zliczania.info(f"🔍 Wyszukiwanie: {mies:02d}/{rok} ({pod})...")
                        lista, status = utils.pobierz_wszystko_z_okresu(
                            f"{rok}-{mies:02d}-01", f"{rok}-{mies:02d}-{ost:02d}", sesja, pod, utils.KODY_PODATKOW[pod]
                        )
                        if status in ["ERROR", "TIMEOUT"]: bledy_api += 1
                        for d in lista:
                            do_pobrania.append(d)

        status_zliczania.empty()
        if bledy_api > 0:
            st.error("❌ Odrzucono zapytania przez serwer MF. Możliwa blokada.")
            st.stop()
            
        znane_id = set(konfig.get("przetworzone_id", [])) | set(konfig.get("uszkodzone_id", []))
        do_pobrania = [d for d in do_pobrania if d["id"] not in znane_id]

    st.session_state['auto_resume_m2'] = False
    
    if not do_pobrania: 
        st.success("Brak nowych dokumentów do pobrania.")
        st.stop()

    status, pasek = st.empty(), st.progress(0)
    brakujace = len(do_pobrania)
    nowe_tresci = []
    nowe_przetworzone = []
    nowe_uszkodzone = []
    blokada = False
    
    for idx, dok in enumerate(do_pobrania):
        st.session_state['queue_m2'] = do_pobrania[idx:]
        status.info(f"Pobieranie ({idx+1}/{brakujace}): {dok['sygnatura']}...")
        
        tekst, st_pobr = utils.pobierz_tekst_pdf(dok["id"])
        
        if tekst:
            rekord = {"Data": dok["data"], "Podatek": dok["typ"], "Sygnatura": dok["sygnatura"], "Link": utils.PODGLAD_URL.format(id=dok["id"]), "Tekst": tekst, "_id": dok["id"]}
            nowe_tresci.append(rekord)
            nowe_przetworzone.append(dok["id"])
            pelne_tresci_lokalne.append(rekord)
        else:
            if st_pobr in ["BRAK_PLIKU", "BŁĄD_CZYTANIA"]:
                nowe_uszkodzone.append(dok["id"])
            else:
                st.session_state['lockout_active_m2'] = True
                st.session_state['lockout_start_m2'] = time.time()
                blokada = True
                break
                
        pasek.progress((idx + 1) / brakujace)
        time.sleep(1.0)
        
    if nowe_tresci:
        with st.spinner("Zapisywanie w bazie danych Supabase..."):
            nowych = archiwum.zapisz_wiele_do_archiwum(nowe_tresci, "downloader_v3")
        st.info(f"Zapisano **{nowych}** nowych interpretacji w bazie.")

    konfig["przetworzone_id"] = list(set(konfig.get("przetworzone_id",[])) | set(nowe_przetworzone))
    if not blokada:
        konfig["uszkodzone_id"] = list(set(konfig.get("uszkodzone_id",[])) | set(nowe_uszkodzone))
        
    utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M2, konfig)
    utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M2, pelne_tresci_lokalne)

    if blokada:
        st.rerun()
    else:
        st.session_state.pop('queue_m2', None)
        status.success("🎉 Koniec! Pliki zostały pobrane i zarchiwizowane.")
