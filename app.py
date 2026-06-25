import streamlit as st
import time

# Załadowanie naszych nowych plików-modułów
import radar
import downloader
import market
import cfo_analyzer

st.set_page_config(page_title="PickPivot Platform", page_icon="⚡", layout="wide")

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

if not st.session_state['authenticated']:
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_login, _ = st.columns([1, 2])
    with col_login:
        st.title("🔐 Panel PickPivot")
        st.markdown("Logowanie do systemu modularnego.")
        username = st.text_input("Login:")
        password = st.text_input("Hasło:", type="password")
        
        if st.button("🚀 Zaloguj się", type="primary", use_container_width=True):
            if username == "DORADCA" and password == "kontotestowe413":
                st.session_state['authenticated'] = True
                st.rerun()
            else:
                st.error("Błędne dane logowania.")
    st.stop() 

# --- GŁÓWNE MENU BOCZNE ---
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

st.sidebar.markdown("---")
if st.sidebar.button("🚪 Wyloguj się", use_container_width=True):
    st.session_state['authenticated'] = False
    st.rerun()

st.sidebar.caption("© 2026 PickPivot Modular Engine")

# --- KIEROWANIE DO ODPOWIEDNICH PLIKÓW ---
if aktywna_zakladka.startswith("1."):
    radar.run_module()
elif aktywna_zakladka.startswith("2."):
    downloader.run_module()
elif aktywna_zakladka.startswith("3."):
    market.run_module()
elif aktywna_zakladka.startswith("4."):
    cfo_analyzer.run_module()
else:
    st.title("🛠️ Moduł w budowie")
    st.info("Ta funkcjonalność zostanie dodana wkrótce.")
