import streamlit as st
import time
import random
import io
import requests
import calendar
from docx import Document
import utils

def run_module():
    st.title("📦 Ściągacz Interpretacji (Zbiorcze)")
    
    # 1. Anty-ban blokada
    if st.session_state.get('lockout_active_m2', False):
        st.error("🔒 OCHRONA ANTY-BAN: Czekaj 5 minut.")
        el = time.time() - st.session_state.get('lockout_start_m2', 0)
        if el < 300:
            st.info("Trwa pauza ochronna...")
            time.sleep(1)
            st.rerun()
        else:
            if st.button("▶️ WZNÓW PRZERWANE", type="primary"):
                st.session_state['lockout_active_m2'] = False
                st.session_state['auto_resume_m2'] = True
                st.rerun()
            st.stop()

    # 2. Wczytanie bazy
    konfig = utils.wczytaj_historie(utils.PLIK_KONFIGURACJI_M2)
    przetworzone_id = set(konfig.get("przetworzone_id", []))
    uszkodzone_id = set(konfig.get("uszkodzone_id", []))
    pelne_tresci = utils.wczytaj_pelne_tresci(utils.PLIK_REKORDOW_M2)

    if pelne_tresci or uszkodzone_id:
        st.success(f"💾 Pamięć podręczna: Zabezpieczono {len(pelne_tresci)} poprawnych dokumentów.")
        if pelne_tresci and st.button("📄 GENERUJ ARCHIWUM WORD", type="primary"):
            doc = Document()
            for r in pelne_tresci:
                doc.add_heading(f"Sygnatura: {r['Sygnatura']}", 1)
                doc.add_paragraph(f"Data: {r['Data']} | Podatek: {r['Podatek']}")
                doc.add_paragraph(f"Link: {r['Link']}")
                doc.add_paragraph(utils.wyczysc_tekst_dla_worda(r['Tekst']))
                doc.add_page_break()
            out = io.BytesIO()
            doc.save(out)
            st.download_button("📥 Pobierz Archiwum", out.getvalue(), "Zrzut.docx")
        st.markdown("---")

    # 3. Interfejs parametrów
    col1, col2, col3 = st.columns(3)
    with col1: wybrane_lata = st.multiselect("Lata:", [2024, 2025, 2026], key="l2")
    with col2: wybrane_miesiace_ui = st.multiselect("Miesiące:", utils.MIESIACE_PL, key="m2")
    with col3: wybrane_podatki = st.multiselect("Podatki:", ["PIT", "CIT", "VAT", "AKCYZA"], default=["VAT"], key="p2")

    c1, c2 = st.columns(2)
    with c1: btn_wznow = st.button("▶️ Wznów pobieranie")
    with c2: btn_od_nowa = st.button("🔄 Pobierz od nowa")

    if btn_wznow or btn_od_nowa or st.session_state.get('auto_resume_m2', False):
        if btn_od_nowa:
            utils.wyczysc_dane_serwera(utils.PLIK_KONFIGURACJI_M2, utils.PLIK_REKORDOW_M2)
            konfig = {"przetworzone_id": [], "ukonczone_kombinacje": [], "uszkodzone_id": []}
            przetworzone_id, uszkodzone_id, pelne_tresci = set(), set(), []
            st.session_state.pop('queue_m2', None)

        if st.session_state.get('auto_resume_m2', False) and 'queue_m2' in st.session_state:
            do_pobrania = st.session_state['queue_m2']
        else:
            if not wybrane_lata or not wybrane_miesiace_ui or not wybrane_podatki:
                st.error("Proszę wybrać parametry."); st.stop()
                
            wybrane_miesiace = [utils.MIESIACE_PL.index(m) + 1 for m in wybrane_miesiace_ui]
            do_pobrania = []
            bledy_api = 0
            znalezione_ogolem = 0
            
            status_zliczania = st.empty()
            with requests.Session() as sesja:
                for rok in wybrane_lata:
                    for mies in wybrane_miesiace:
                        _, ost = calendar.monthrange(rok, mies)
                        for pod in wybrane_podatki:
                            status_zliczania.info(f"🔍 Pytam serwer MF o: {mies:02d}/{rok} ({pod})...")
                            lista, status = utils.pobierz_wszystko_z_okresu(
                                f"{rok}-{mies:02d}-01", f"{rok}-{mies:02d}-{ost:02d}", sesja, pod, utils.KODY_PODATKOW[pod]
                            )
                            
                            if status in ["ERROR", "TIMEOUT"]:
                                bledy_api += 1
                            
                            znalezione_ogolem += len(lista)
                            for d in lista:
                                if d["id"] not in przetworzone_id and d["id"] not in uszkodzone_id:
                                    do_pobrania.append(d)

            status_zliczania.empty()
            
            # DIAGNOSTYKA BŁĘDÓW
            if bledy_api > 0:
                st.error(f"❌ Ministerstwo Finansów zablokowało {bledy_api} zapytań (Błąd serwera/API). Prawdopodobnie masz czasową blokadę IP. Odczekaj 15-30 minut.")
                st.stop()

        st.session_state['auto_resume_m2'] = False
        
        if not do_pobrania: 
            if znalezione_ogolem == 0:
                st.warning("⚠️ System urzędowy nie zwrócił ŻADNYCH interpretacji dla tej kombinacji dat i podatku.")
            else:
                st.success("✔️ Wszystkie znalezione dokumenty są już na Twoim dysku (Brak nowych do pobrania).")
            st.stop()

        status, pasek = st.empty(), st.progress(0)
        brakujace = len(do_pobrania)
        
        for idx, dok in enumerate(do_pobrania):
            st.session_state['queue_m2'] = do_pobrania[idx:]
            status.info(f"Pobieranie pliku PDF ({idx+1}/{brakujace}): {dok['sygnatura']}...")
            tekst, st_pobr = utils.pobierz_tekst_pdf(dok["id"])
            
            if tekst:
                pelne_tresci.append({"Data": dok["data"], "Podatek": dok["typ"], "Sygnatura": dok["sygnatura"], "Link": utils.PODGLAD_URL.format(id=dok["id"]), "Tekst": tekst})
                przetworzone_id.add(dok["id"])
                konfig["przetworzone_id"].append(dok["id"])
            else:
                if st_pobr in ["BRAK_PLIKU", "BŁĄD_CZYTANIA"]:
                    uszkodzone_id.add(dok["id"])
                    konfig["uszkodzone_id"].append(dok["id"])
                else:
                    st.session_state['lockout_active_m2'] = True
                    st.session_state['lockout_start_m2'] = time.time()
                    utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M2, pelne_tresci)
                    utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M2, konfig)
                    st.rerun()
                    
            utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M2, pelne_tresci)
            utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M2, konfig)
            pasek.progress((idx + 1) / brakujace)
            time.sleep(random.uniform(1.5, 2.5))
            
        st.session_state.pop('queue_m2', None)
        status.success("🎉 Koniec! Pliki zostały pobrane."); st.rerun()
