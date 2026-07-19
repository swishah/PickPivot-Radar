import streamlit as st
import time

import paleta

# Zaladowanie wszystkich Twoich odseparowanych plikow
import cfo_analyzer
import raporty
import eksplorator_archiwum
import eksplorator_wyrokow
import zestawienie_tygodniowe
import zestawienie_automat
import monitoring_ui
import ustawienia_systemu

# Logo dolaczone bezposrednio w kodzie (base64) - dziala niezaleznie od
# tego, gdzie i jak jest hostowana aplikacja, bez osobnego pliku obrazka.
_LOGO_B64 = (
    "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"
)


def _naglowek_logo(wysokosc_px: int = 34):
    """
    Renderuje logo + nazwe marki. W trybie ciemnym logo (ktore ma stale
    wpisane ciemne kolory w samym pliku PNG) dostaje jasna plakietke pod
    spodem, zeby napis w logo pozostal czytelny na ciemnym tle.
    """
    tryb = paleta.aktywny_tryb()
    tlo_plakietki = "#F3F1E8" if tryb == "dark" else "transparent"
    kolor_tekstu = paleta.paleta()["text"]
    st.markdown(
        f'''
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
            <div style="background:{tlo_plakietki}; border-radius:8px;
                        padding:4px 7px; display:flex; align-items:center;">
                <img src="data:image/png;base64,{_LOGO_B64}"
                     style="height:{wysokosc_px}px; display:block;">
            </div>
            <span style="font-size:{wysokosc_px * 0.62}px; font-weight:700;
                         color:{kolor_tekstu}; letter-spacing:-0.01em;">
                {paleta.NAZWA_MARKI}
            </span>
        </div>
        ''',
        unsafe_allow_html=True,
    )


# 1. Konfiguracja glownej strony
st.set_page_config(page_title=paleta.NAZWA_MARKI, page_icon="📗", layout="wide")

# 2. Bezpieczny system autoryzacji
# Dane logowania NIE sa zaszyte w kodzie — pochodza z Streamlit Secrets:
#   [auth]
#   login = "..."
#   haslo = "..."
# Dzieki temu repozytorium nie zawiera hasla, a zmiana hasla nie wymaga
# commita (wystarczy edycja Secrets; aplikacja przeladuje sie sama).
import hmac
import time as _time

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'proby_logowania' not in st.session_state:
    st.session_state['proby_logowania'] = 0

if not st.session_state['authenticated']:
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_login, _ = st.columns([1, 2])
    with col_login:
        _naglowek_logo(wysokosc_px=40)
        st.markdown("#### Panel logowania")
        st.markdown("Logowanie do systemu modularnego.")

        username = st.text_input("Login:")
        password = st.text_input("Hasło:", type="password")

        if st.button("🚀 Zaloguj się", type="primary", use_container_width=True):
            try:
                dobry_login = st.secrets["auth"]["login"]
                dobre_haslo = st.secrets["auth"]["haslo"]
            except Exception:
                st.error(
                    "Brak sekcji [auth] w Streamlit Secrets. Dodaj w "
                    "Manage app → Settings → Secrets klucze login i haslo."
                )
                st.stop()

            # Spowolnienie po bledach: kazda kolejna nieudana proba wydluza
            # oczekiwanie (utrudnia zgadywanie hasla automatem).
            if st.session_state['proby_logowania'] >= 3:
                _time.sleep(min(2 ** (st.session_state['proby_logowania'] - 2), 30))

            # hmac.compare_digest — porownanie stalo-czasowe (nie zdradza,
            # ile poczatkowych znakow bylo trafionych).
            login_ok = hmac.compare_digest(username.encode(), dobry_login.encode())
            haslo_ok = hmac.compare_digest(password.encode(), dobre_haslo.encode())

            if login_ok and haslo_ok:
                st.session_state['authenticated'] = True
                st.session_state['proby_logowania'] = 0
                st.rerun()
            else:
                st.session_state['proby_logowania'] += 1
                st.error("Błędne dane logowania.")
    st.stop()

# 3. Glowne menu boczne (Nawigacja)
with st.sidebar:
    _naglowek_logo(wysokosc_px=28)
aktywna_zakladka = st.sidebar.radio(
    "Wybierz moduł:",
    [
        "1. Ściągacz Interpretacji",
        "2. Archiwum Interpretacji",
        "3. Analiza Wskaźnikowa",
        "4. Wyroki Sądów (WSA/NSA)",
        "5. Zestawienie Tygodniowe",
        "6. Zestawienie Tygodniowe Automat (próbna)",
        "7. Monitoring i Powiadomienia",
        "8. Ustawienia Systemu"
    ]
)

st.sidebar.markdown("---")
if st.sidebar.button("🚪 Wyloguj się", use_container_width=True):
    st.session_state['authenticated'] = False
    st.rerun()

st.sidebar.caption(f"© 2026 {paleta.NAZWA_MARKI}")

# 4. System routingu (Przelaczanie modulow)
if aktywna_zakladka.startswith("1."):
    raporty.run_module()
elif aktywna_zakladka.startswith("2."):
    eksplorator_archiwum.run_module()
elif aktywna_zakladka.startswith("3."):
    cfo_analyzer.run_module()
elif aktywna_zakladka.startswith("4."):
    eksplorator_wyrokow.run_module()
elif aktywna_zakladka.startswith("5."):
    zestawienie_tygodniowe.pokaz_zestawienie_tygodniowe()
elif aktywna_zakladka.startswith("6."):
    zestawienie_automat.pokaz_zestawienie_automat()
elif aktywna_zakladka.startswith("7."):
    monitoring_ui.pokaz_monitoring()
elif aktywna_zakladka.startswith("8."):
    ustawienia_systemu.pokaz_ustawienia()
else:
    # Obsluga zakladek, ktore sa dopiero w planach
    nazwa_modulu = aktywna_zakladka.split('. ')[1] if '. ' in aktywna_zakladka else 'Modul'
    st.title(f"🛠️ {nazwa_modulu}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
