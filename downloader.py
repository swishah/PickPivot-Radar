import streamlit as st
import time
import random
import io
import requests
import calendar
import threading
from docx import Document
from datetime import datetime
import utils

# ---------------------------------------------------------------------------
# STAŁA: ile sekund odczekać po wykryciu blokady IP
# ---------------------------------------------------------------------------
CZAS_LOCKOUT_S = 300  # 5 minut

# ---------------------------------------------------------------------------
# POMOCNICZE: generowanie pliku Word z zebranych rekordów
# ---------------------------------------------------------------------------
def _generuj_word(pelne_tresci: list) -> bytes:
    doc = Document()
    doc.add_heading("PickPivot – Archiwum Interpretacji Podatkowych", 0)
    doc.add_paragraph(f"Wygenerowano: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    doc.add_paragraph(f"Liczba dokumentów: {len(pelne_tresci)}")
    doc.add_page_break()

    for r in pelne_tresci:
        doc.add_heading(f"Sygnatura: {r['Sygnatura']}", 1)
        doc.add_paragraph(f"Data:    {r['Data']}")
        doc.add_paragraph(f"Podatek: {r['Podatek']}")
        doc.add_paragraph(f"Link:    {r['Link']}")
        if r.get("Format"):
            doc.add_paragraph(f"Format źródła: {r['Format']}")
        doc.add_paragraph(utils.wyczysc_tekst_dla_worda(r['Tekst']))
        doc.add_page_break()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# GŁÓWNY MODUŁ STREAMLIT
# ---------------------------------------------------------------------------
def run_module():
    st.title("📦 Ściągacz Interpretacji (Zbiorcze) — v2")

    # ── 1. OBSŁUGA BLOKADY ANTY-BAN ───────────────────────────────────────
    if st.session_state.get('lockout_active_m2', False):
        uplynelo = time.time() - st.session_state.get('lockout_start_m2', 0)
        pozostalo = max(0, int(CZAS_LOCKOUT_S - uplynelo))

        st.error(f"🔒 OCHRONA ANTY-BAN — Przerwa ochronna ({pozostalo}s pozostało)")
        st.progress(min(1.0, uplynelo / CZAS_LOCKOUT_S))

        if uplynelo < CZAS_LOCKOUT_S:
            st.info("System automatycznie wznowi pobieranie po upływie pauzy.")
            time.sleep(1)
            st.rerun()
        else:
            if st.button("▶️ WZNÓW POBIERANIE", type="primary", use_container_width=True):
                st.session_state['lockout_active_m2'] = False
                st.session_state['auto_resume_m2']    = True
                st.rerun()
        st.stop()

    # ── 2. WCZYTANIE STANU Z DYSKU ─────────────────────────────────────────
    konfig          = utils.wczytaj_historie(utils.PLIK_KONFIGURACJI_M2)
    przetworzone_id = set(konfig.get("przetworzone_id", []))
    uszkodzone_id   = set(konfig.get("uszkodzone_id", []))
    pelne_tresci    = utils.wczytaj_pelne_tresci(utils.PLIK_REKORDOW_M2)

    # ── 3. PANEL STANU BAZY ────────────────────────────────────────────────
    if pelne_tresci or uszkodzone_id:
        col_stat1, col_stat2, col_stat3 = st.columns(3)
        col_stat1.metric("✅ Pobrane dokumenty",   len(pelne_tresci))
        col_stat2.metric("❌ Uszkodzone / brak PDF", len(uszkodzone_id))
        col_stat3.metric("📋 Łącznie w kolejce",
                         len(przetworzone_id) + len(uszkodzone_id))

        if pelne_tresci:
            col_dl, col_rst = st.columns([3, 1])
            with col_dl:
                word_bytes = _generuj_word(pelne_tresci)
                st.download_button(
                    label="📄 Pobierz Archiwum Word",
                    data=word_bytes,
                    file_name=f"Interpretacje_{datetime.now().strftime('%Y%m%d_%H%M')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="primary",
                    use_container_width=True
                )
            with col_rst:
                if st.button("🗑️ Wyczyść bazę", use_container_width=True):
                    utils.wyczysc_dane_serwera(utils.PLIK_KONFIGURACJI_M2, utils.PLIK_REKORDOW_M2)
                    st.session_state.pop('queue_m2', None)
                    st.rerun()

        # Podgląd ostatnich pobrań
        with st.expander("🔍 Podgląd pobranych dokumentów"):
            for r in pelne_tresci[-10:][::-1]:
                st.markdown(
                    f"**{r['Sygnatura']}** | {r['Data']} | {r['Podatek']}"
                    + (f" | _{r.get('Format', '')}_" if r.get("Format") else "")
                )

        st.markdown("---")

    # ── 4. INTERFEJS PARAMETRÓW ────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        wybrane_lata = st.multiselect("📅 Lata:", [2024, 2025, 2026], key="l2")
    with col2:
        wybrane_miesiace_ui = st.multiselect("📅 Miesiące:", utils.MIESIACE_PL, key="m2")
    with col3:
        wybrane_podatki = st.multiselect(
            "💰 Podatki:", ["PIT", "CIT", "VAT", "AKCYZA"], default=["VAT"], key="p2"
        )

    # ► NOWE: suwak kontroli szybkości
    with st.expander("⚙️ Ustawienia pobierania"):
        workers = st.slider(
            "Równoległe wątki pobierania",
            min_value=1, max_value=8, value=5,
            help="Więcej wątków = szybciej, ale ryzyko blokady IP rośnie. "
                 "Przy pierwszym uruchomieniu zostaw 5."
        )
        pref_format = st.radio(
            "Preferowany format źródła",
            options=["HTML → PDF (szybszy, zalecany)", "Tylko PDF (wolniejszy, pełna wierność)"],
            index=0,
            help="HTML jest ~3× szybszy niż PDF i generuje czystszy tekst."
        )
        st.session_state['m2_workers']     = workers
        st.session_state['m2_tylko_pdf']   = "Tylko PDF" in pref_format

    c1, c2 = st.columns(2)
    with c1: btn_wznow  = st.button("▶️ Wznów pobieranie",  use_container_width=True)
    with c2: btn_od_nowa = st.button("🔄 Pobierz od nowa",  use_container_width=True)

    # ── 5. LOGIKA POBIERANIA ───────────────────────────────────────────────
    start_pobierania = (
        btn_wznow
        or btn_od_nowa
        or st.session_state.get('auto_resume_m2', False)
    )

    if not start_pobierania:
        st.stop()

    # Reset bazy jeśli użytkownik kliknął "od nowa"
    if btn_od_nowa:
        utils.wyczysc_dane_serwera(utils.PLIK_KONFIGURACJI_M2, utils.PLIK_REKORDOW_M2)
        konfig          = {"przetworzone_id": [], "ukonczone_kombinacje": [], "uszkodzone_id": []}
        przetworzone_id = set()
        uszkodzone_id   = set()
        pelne_tresci    = []
        st.session_state.pop('queue_m2', None)

    # ── 5a. BUDOWANIE KOLEJKI (lub odczyt z session_state po lockout) ──────
    znalezione_ogolem = 0
    bledy_api         = 0

    if st.session_state.get('auto_resume_m2', False) and 'queue_m2' in st.session_state:
        do_pobrania = st.session_state['queue_m2']
        st.info(f"♻️ Wznawianie przerwanego pobierania — {len(do_pobrania)} dokumentów w kolejce.")
    else:
        if not wybrane_lata or not wybrane_miesiace_ui or not wybrane_podatki:
            st.error("⚠️ Proszę wybrać przynajmniej jeden rok, miesiąc i podatek.")
            st.stop()

        wybrane_miesiace = [utils.MIESIACE_PL.index(m) + 1 for m in wybrane_miesiace_ui]
        do_pobrania      = []

        kontener_statusu = st.empty()
        pasek_listowania = st.progress(0)
        kombinacje_total = len(wybrane_lata) * len(wybrane_miesiace) * len(wybrane_podatki)
        kombinacje_idx   = 0

        with requests.Session() as sesja:
            for rok in wybrane_lata:
                for mies in wybrane_miesiace:
                    _, ost = calendar.monthrange(rok, mies)
                    for pod in wybrane_podatki:
                        kombinacje_idx += 1
                        kontener_statusu.info(
                            f"🔍 [{kombinacje_idx}/{kombinacje_total}] "
                            f"Odpytuję MF: {mies:02d}/{rok} ({pod})…"
                        )
                        lista, status = utils.pobierz_wszystko_z_okresu(
                            f"{rok}-{mies:02d}-01",
                            f"{rok}-{mies:02d}-{ost:02d}",
                            sesja, pod, utils.KODY_PODATKOW[pod]
                        )
                        if status in ("ERROR", "TIMEOUT"):
                            bledy_api += 1
                        znalezione_ogolem += len(lista)
                        for d in lista:
                            if d["id"] not in przetworzone_id and d["id"] not in uszkodzone_id:
                                do_pobrania.append(d)
                        pasek_listowania.progress(kombinacje_idx / kombinacje_total)

        kontener_statusu.empty()
        pasek_listowania.empty()

        if bledy_api > 0:
            st.error(
                f"❌ Serwer MF odrzucił {bledy_api} zapytań. "
                "Prawdopodobna czasowa blokada IP — odczekaj 15–30 minut."
            )
            st.stop()

        st.info(
            f"📋 Znaleziono łącznie **{znalezione_ogolem}** dokumentów. "
            f"Do pobrania (nowych): **{len(do_pobrania)}**."
        )

    st.session_state['auto_resume_m2'] = False

    if not do_pobrania:
        st.success("✔️ Brak nowych dokumentów do pobrania — baza jest aktualna.")
        st.stop()

    # ── 5b. RÓWNOLEGŁE POBIERANIE TREŚCI ──────────────────────────────────
    workers_count = st.session_state.get('m2_workers', 5)
    brakujace     = len(do_pobrania)

    st.markdown(
        f"### ⬇️ Pobieranie {brakujace} dokumentów "
        f"({workers_count} równoległych wątków)"
    )

    pasek_glowny    = st.progress(0)
    kontener_licznik = st.empty()
    kontener_log    = st.empty()
    log_lines       = []      # ostatnie N wierszy aktywności

    # Liczniki współdzielone między wątkami (thread-safe przez utils._lock)
    stan = {"ukonczone": 0, "ok": 0, "blad": 0, "blokada": False}

    def on_postep(completed, total, sygnatura, status):
        """Callback wołany przez utils po każdym ukończonym dokumencie."""
        with utils._lock:
            stan["ukonczone"] = completed
            if status == "OK":
                stan["ok"] += 1
                ikona = "✅"
            elif status in ("BRAK_PLIKU", "BŁĄD_CZYTANIA"):
                stan["blad"] += 1
                ikona = "⚠️"
            elif status == "BLOKADA":
                stan["blokada"] = True
                ikona = "🔒"
            elif status == "POMINIETY":
                ikona = "⏭️"
            else:
                ikona = "❓"
            log_lines.insert(0, f"{ikona} [{completed}/{total}] {sygnatura}")
            if len(log_lines) > 8:
                log_lines.pop()

        # Aktualizacja UI (wołane z wątków — Streamlit obsługuje to bezpiecznie)
        pasek_glowny.progress(completed / total)
        kontener_licznik.markdown(
            f"✅ Pobrano: **{stan['ok']}** &nbsp;|&nbsp; "
            f"⚠️ Błędy/brak: **{stan['blad']}** &nbsp;|&nbsp; "
            f"⏳ Łącznie: **{completed}/{total}**"
        )
        kontener_log.code("\n".join(log_lines), language=None)

    # Zapisz kolejkę do session_state PRZED wywołaniem (na wypadek lockout)
    st.session_state['queue_m2'] = do_pobrania

    nowe_tresci, nowe_przetworzone, nowe_uszkodzone, blokada = \
        utils.pobierz_dokumenty_rownolegle(
            do_pobrania,
            przetworzone_id,
            uszkodzone_id,
            callback_postep=on_postep,
            workers=workers_count
        )

    # ── 5c. SCALANIE WYNIKÓW I ZAPIS ──────────────────────────────────────
    pelne_tresci.extend(nowe_tresci)
    konfig["przetworzone_id"].extend(nowe_przetworzone)

    if blokada:
        # Dokumenty, których nie zdążono pobrać — zostają w kolejce
        pobrane_id   = set(nowe_przetworzone)
        kolejka_rest = [d for d in do_pobrania if d["id"] not in pobrane_id
                        and d["id"] not in set(nowe_uszkodzone)]
        st.session_state['queue_m2']        = kolejka_rest
        st.session_state['lockout_active_m2'] = True
        st.session_state['lockout_start_m2']  = time.time()
    else:
        konfig["uszkodzone_id"].extend(nowe_uszkodzone)
        st.session_state.pop('queue_m2', None)

    utils.zapisz_pelne_tresci(utils.PLIK_REKORDOW_M2, pelne_tresci)
    utils.zapisz_historie(utils.PLIK_KONFIGURACJI_M2, konfig)

    if blokada:
        st.rerun()   # pokaże ekran lockout z odliczaniem
    else:
        pasek_glowny.progress(1.0)
        st.success(
            f"🎉 Zakończono! Pobrano **{len(nowe_tresci)}** nowych dokumentów. "
            f"Pominięto (brak pliku): **{len(nowe_uszkodzone)}**."
        )
        st.balloons()
        time.sleep(2)
        st.rerun()
