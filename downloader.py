import streamlit as st
import time
import random
import io
import requests
import calendar
from datetime import datetime
from docx import Document
import utils

def run_module():
    st.title("📦 Ściągacz Interpretacji (Pobieranie Zbiorcze)")

    if st.session_state.get('lockout_active_m2', False):
        st.error("🔒 SYSTEM OCHRONY PRZED BANEM AKTYWNY")
        elapsed = time.time() - st.session_state.get('lockout_start_m2', 0)
        if elapsed < 300:
            st.info("Odczekaj 5 minut...")
            time.sleep(1); st.rerun()
        else:
            if st.button("▶️ WZNÓW PRZERWANE POBIERANIE"):
                st.session_state['lockout_active_m2'] = False
                st.session_state['auto_resume_m2'] = True
                st.rerun()
            st.stop()

    konfig = utils.wczytaj_historie(utils.PLIK_KONFIGURACJI_M2)
    przetworzone_id = set(konfig.get("przetworzone_id", []))
    uszkodzone_id = set(konfig.get("uszkodzone_id", []))
    pelne_tresci = utils.wczytaj_pelne_tresci(utils.PLIK_REKORDOW_M2)

    if pelne_tresci or uszkodzone_id:
        st.success(f"💾 Baza: {len(pelne_tresci)} poprawnych, {len(uszkodzone_id)} odrzuconych.")
        if pelne_tresci and st.button("📄 GENERUJ ARCHIWUM WORD"):
            doc = Document()
            for r in pelne_tresci:
                doc.add_heading(f"{r['Sygnatura']}", level=1)
                doc.add_paragraph(r['Tekst'][:1000] + "...") # Skrócony dla przykładu
                doc.add_page_break()
            out = io.BytesIO()
            doc.save(out)
            st.download_button("📥 Pobierz Archiwum", data=out.getvalue(), file_name="Zrzut.docx")

    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata = st.multiselect("Lata:", [2024, 2025, 2026])
    with col2: wybrane_miesiace_ui = st.multiselect("Miesiące:", utils.MIESIACE_PL)
    with col3: wybrane_podatki_ui = st.multiselect("Podatki:", ["PIT", "CIT", "VAT", "AKCYZA"], default=["VAT"])

    c1, c2 = st.columns(2)
    with c1: btn_wznow = st.button("▶️ Wznów pobieranie")
    with c2: btn_od_nowa = st.button("🔄 Pobierz od nowa")

    if btn_wznow or btn_od_nowa or st.session_state.get('auto_resume_m2', False):
        if btn_od_nowa:
            utils.wyczysc_dane_serwera(utils.PLIK_KONFIGURACJI_M2, utils.PLIK_REKORDOW_M2)
            konfig = {"przetworzone_id": [], "ukonczone_kombinacje": [], "uszkodzone_id": []}
            przetworzone_id, uszkodzone_id, pelne_tresci = set(), set(), []
            if 'queue_m2' in st.session_state: del st.session_state['queue_m2']

        if st.session_state.get('auto_resume_m2', False) and 'queue_m2' in st.session_state:
            do_pobrania = st.session_state['queue_m2']
        else:
            do_pobrania = []
            miesiace = [utils.MIESIACE_PL.index(m) + 1 for m in wybrane_miesiace_ui]
            with requests.Session() as sesja:
                for rok in wybrane_lata:
                    for mies w miesiace:
                        _, ost_dzien = calendar.monthrange(rok, mies)
                        for podatek in wybrane_podatki_ui:
                            lista, _ = utils.pobierz_wszystko_z_okresu(f"{rok}-{mies:02d}-01", f"{rok}-{mies:02d}-{ost_dzien:02d}", sesja, podatek, utils.KODY_PODATKOW[podatek])
                            for d in lista:
                                if d["id"] not in przetworzone_id and d["id"] not in uszkodzone_id:
                                    do_pobrania.append(d)

        st.session_state['auto_resume_m2'] = False
        if not do_pobrania: st.success("Wszystko pobrane!"); st.stop()

        status = st.empty()
        pobrane, uszkodzone = 0, 0
        for idx, dok in enumerate(do_pobrania):
            st.session_state['queue_m2'] = do_pobrania[idx:]
            status.info(f"Pobieranie {dok['sygnatura']}...")
            tekst, st_pobr = utils.pobierz_tekst_pdf(dok["id"])
            if tekst:
                pelne_tresci.append({"Data": dok["data"], "Podatek": dok["typ"], "Sygnatura": dok["sygnatura"], "Link": utils.PODGLAD_URL.format(id=dok["id"]), "Tekst": tekst})
                przetworzone_id.add(dok["id"]); konfig["przetworzone_id"].append(dok["id"])
                pobrane += 1
            else:
                if st_pobr in ["BRAK_PLIKU", "BŁĄD_CZYTANIA"]:
                    uszkodzone_id.add(dok["id"]); konfig["uszkodzone_id"].append(dok["id"])
                    uszkodzone += 1
                else:
                    st.session_state['lockout_active_m2'] = True
                    st.session_state['lockout_start_m2'] = time.time()
                    utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M2, pelne_tresci)
                    utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M2, konfig)
                    st.rerun()
            utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M2, pelne_tresci)
            utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M2, konfig)
            time.sleep(random.uniform(1.5, 2.5))

        if 'queue_m2' in st.session_state: del st.session_state['queue_m2']
        st.success("Zakończono pobieranie."); st.rerun()
