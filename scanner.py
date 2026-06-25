import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import io
import requests
from datetime import datetime

WIG20_MAP = {"ALE.WA": "Allegro", "PKO.WA": "PKO BP"} # Skrócone dla czytelności
def get_sp500_map(): return {"AAPL": "Apple", "MSFT": "Microsoft"}

def analyze_market(ticker_map):
    results = []
    for t, name in ticker_map.items():
        try:
            df = yf.download(t, period="1y", progress=False)
            if df.empty: continue
            p = float(df['Close'].iloc[-1])
            results.append({"Nazwa": name, "Ticker": t, "Cena": round(p,2), "SUMA BUY": 5})
        except: pass
    return pd.DataFrame(results)

def run_module():
    st.title("📊 Global Market Scanner")
    ALL_MARKETS = {"Polska (WIG)": WIG20_MAP, "USA": get_sp500_map()}
    selected = st.sidebar.multiselect("Rynki:", list(ALL_MARKETS.keys()), default=["Polska (WIG)"])

    if st.button("Uruchom Analizę", type="primary"):
        results = {}
        for m in selected: results[m] = analyze_market(ALL_MARKETS[m])
        st.success("Zakończono!")
        for m, df in results.items():
            st.subheader(m)
            st.dataframe(df)
