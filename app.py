import streamlit as st
import time

# Załadowanie wszystkich Twoich odseparowanych plików
import radar
import downloader
import market
import cfo_analyzer

# 1. Konfiguracja głównej strony
st.set_page_config(page_title="PickPivot Platform", page_icon="⚡", layout="wide")

# 2. Bezpieczny system autoryzacji
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

# 3. Główne menu boczne (Nawigacja)
st.sidebar.title("📌 Menu PickPivot")
aktywna_zakladka = st.sidebar.radio(
    "Wybierz moduł:",
    [
        "1. Radar Orzecznictwa",
        "2. Ściągacz Interpretacji",
        "3. Global Market Scanner",
        "4. Analiza Wskaźnikowa",
        "5. Historia Pobierania (Wkrótce)",
        "6. Ustawienia Systemu (Wkrótce)"
    ]
)

st.sidebar.markdown("---")
if st.sidebar.button("🚪 Wyloguj się", use_container_width=True):
    st.session_state['authenticated'] = False
    st.rerun()

st.sidebar.caption("© 2026 PickPivot Modular Engine")

# 4. System routingu (Przełączanie modułów)
if aktywna_zakladka.startswith("1."):
    radar.run_module()
elif aktywna_zakladka.startswith("2."):
    downloader.run_module()
elif aktywna_zakladka.startswith("3."):
    market.run_module()
elif aktywna_zakladka.startswith("4."):
    cfo_analyzer.run_module()
else:
    # Obsługa zakładek, które są dopiero w planach
    nazwa_modulu = aktywna_zakladka.split('. ')[1] if '. ' in aktywna_zakladka else 'Moduł'
    st.title(f"🛠️ {nazwa_modulu}")
    st.info("Ta funkcjonalność jest obecnie w fazie projektowania i zostanie dodana w przyszłości.", icon="ℹ️")
