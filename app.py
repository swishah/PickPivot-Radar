import streamlit as st
import time

import radar
import downloader
import scanner
import cfo_analyzer

st.set_page_config(page_title="PickPivot Platform", page_icon="⚡", layout="wide")

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

if not st.session_state['authenticated']:
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_login, _ = st.columns([1, 2])
    with col_login:
        st.title("🔐 Panel PickPivot")
        u = st.text_input("Login:")
        p = st.text_input("Hasło:", type="password")
        if st.button("🚀 Zaloguj się", type="primary"):
            if u == "DORADCA" and p == "kontotestowe413":
                st.session_state['authenticated'] = True
                st.rerun()
            else: st.error("Błąd logowania")
    st.stop() 

st.sidebar.title("📌 Menu PickPivot")
aktywna_zakladka = st.sidebar.radio(
    "Wybierz moduł:",
    [
        "1. Radar Orzecznictwa",
        "2. Ściągacz Interpretacji",
        "3. Global Market Scanner",
        "4. Analiza Wskaźnikowa",
        "5. Historia Pobierania"
    ]
)

if st.sidebar.button("🚪 Wyloguj się", use_container_width=True):
    st.session_state['authenticated'] = False
    st.rerun()

st.sidebar.caption("© 2026 PickPivot v14.0 Modular")

# --- SYSTEM ROUTINGU (Przełączanie plików) ---
if aktywna_zakladka.startswith("1."):
    radar.run_module()
elif aktywna_zakladka.startswith("2."):
    downloader.run_module()
elif aktywna_zakladka.startswith("3."):
    scanner.run_module()
elif aktywna_zakladka.startswith("4."):
    cfo_analyzer.run_module()
else:
    st.title("🛠️ Moduł w budowie")
    st.info("Ta funkcjonalność zostanie dodana w przyszłości.")
