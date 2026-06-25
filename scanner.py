import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import warnings
from datetime import datetime

# Wyciszenie ostrzeżeń systemowych
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ==============================================================================
# MAPY SPÓŁEK (Zasoby PickPivot)
# ==============================================================================
WIG20_MAP = {"ALE.WA": "Allegro", "ALR.WA": "Alior Bank", "BDX.WA": "Budimex", "BHW.WA": "Bank Handlowy", "CDR.WA": "CD Projekt", "CPS.WA": "Cyfrowy Polsat", "DNP.WA": "Dino Polska", "JSW.WA": "JSW", "KGH.WA": "KGHM", "KRU.WA": "Kruk", "LPP.WA": "LPP", "MBK.WA": "mBank", "OPL.WA": "Orange Polska", "PEO.WA": "Pekao SA", "PGE.WA": "PGE", "PKO.WA": "PKO BP", "PKN.WA": "ORLEN", "PZU.WA": "PZU", "SPL.WA": "Santander BP", "MDV.WA": "Modivo"}
MWIG40_MAP = {"11B.WA": "11 bit studios", "1AT.WA": "Atal", "ABS.WA": "Asseco BS", "APR.WA": "Auto Partner", "ASB.WA": "ASBIS", "BFT.WA": "Benefit Systems", "CAR.WA": "Inter Cars", "CIG.WA": "CI Games", "CLN.WA": "Celon Pharma", "COG.WA": "Cognor", "DAT.WA": "DataWalk", "DOM.WA": "Dom Development", "EAT.WA": "AmRest", "ENP.WA": "Enea", "EUR.WA": "Eurocash", "GPP.WA": "Grupa Pracuj", "GRN.WA": "Grenevia", "GTC.WA": "GTC", "HUU.WA": "Huuuge", "ING.WA": "ING BSK", "TXT.WA": "Text S.A.", "MIL.WA": "Millennium", "MBR.WA": "Mo-BRUK", "NEU.WA": "Neuca", "PLW.WA": "PlayWay", "RVU.WA": "Revuele", "SEL.WA": "Selena FM", "STP.WA": "Stalproduct", "TEN.WA": "Ten Square Games", "TPE.WA": "Tauron", "VRG.WA": "VRG", "WPL.WA": "Wirtualna Polska", "XTB.WA": "XTB", "GPW.WA": "GPW", "SNK.WA": "Sanok", "AST.WA": "Asseco POL", "ATC.WA": "Arctic Paper"}
DAX_MAP = {"ADS.DE": "Adidas", "AIR.DE": "Airbus", "ALV.DE": "Allianz", "BAS.DE": "BASF", "BAYN.DE": "Bayer", "BEI.DE": "Beiersdorf", "BMW.DE": "BMW", "BNR.DE": "Brenntag", "CBK.DE": "Commerzbank", "CON.DE": "Continental", "1COV.DE": "Covestro", "DTG.DE": "Daimler Truck", "DBK.DE": "Deutsche Bank", "DB1.DE": "Deutsche Börse", "DPW.DE": "DHL Group", "DTE.DE": "Deutsche Telekom", "EOAN.DE": "E.ON", "FRE.DE": "Fresenius", "HNR1.DE": "Hannover Re", "HEI.DE": "Heidelberg Materials", "HEN3.DE": "Henkel", "IFX.DE": "Infineon", "MBG.DE": "Mercedes-Benz", "MRK.DE": "Merck", "MTX.DE": "MTU Aero Engines", "MUV2.DE": "Munich Re", "P911.DE": "Porsche AG", "PAH3.DE": "Porsche SE", "QIA.DE": "Qiagen", "RHM.DE": "Rheinmetall", "RWE.DE": "RWE", "SAP.DE": "SAP", "SRT3.DE": "Sartorius", "SIE.DE": "Siemens", "ENR.DE": "Siemens Energy", "SHL.DE": "Siemens Healthineers", "SY1.DE": "Symrise", "VOW3.DE": "Volkswagen", "VNA.DE": "Vonovia", "ZAL.DE": "Zalando"}
CAC40_MAP = {"AC.PA": "Accor", "AI.PA": "Air Liquide", "AIR.PA": "Airbus", "MT.AS": "ArcelorMittal", "CS.PA": "AXA", "BNP.PA": "BNP Paribas", "EN.PA": "Bouygues", "CAP.PA": "Capgemini", "CA.PA": "Carrefour", "ACA.PA": "Crédit Agricole", "BN.PA": "Danone", "DSY.PA": "Dassault Systèmes", "EDEN.PA": "Edenred", "ENGI.PA": "Engie", "EL.PA": "EssilorLuxottica", "ERF.PA": "Eurofins Scientific", "RMS.PA": "Hermès", "KER.PA": "Kering", "LR.PA": "Legrand", "OR.PA": "L'Oréal", "MC.PA": "LVMH", "ML.PA": "Michelin", "ORP.PA": "Orange", "PRV.PA": "Pernod Ricard", "PUB.PA": "Publicis Groupe", "RNO.PA": "Renault", "SAF.PA": "Safran", "SGO.PA": "Saint-Gobain", "SAN.PA": "Sanofi", "SU.PA": "Schneider Electric", "GLE.PA": "Société Générale", "STLAP.PA": "Stellantis", "STMPA.PA": "STMicroelectronics", "TEP.PA": "Teleperformance", "HO.PA": "Thales", "TTE.PA": "TotalEnergies", "URW.AS": "Unibail-Rodamco-Westfield", "VIE.PA": "Veolia", "DG.PA": "Vinci", "VIV.PA": "Vivendi"}
FTSE_MAP = {"SHEL.L": "Shell", "AZN.L": "AstraZeneca", "HSBA.L": "HSBC", "ULVR.L": "Unilever", "BP.L": "BP", "GSK.L": "GSK", "DGE.L": "Diageo", "REL.L": "RELX", "BATS.L": "British American Tobacco", "GLEN.L": "Glencore", "RIO.L": "Rio Tinto", "BA.L": "BAE Systems", "CPG.L": "Compass Group", "LSEG.L": "LSEG", "NWG.L": "NatWest Group", "BARC.L": "Barclays", "STAN.L": "Standard Chartered", "NG.L": "National Grid", "AHT.L": "Ashtead", "TSCO.L": "Tesco", "LLOY.L": "Lloyds", "PRU.L": "Prudential", "AV.L": "Aviva", "SSE.L": "SSE", "LGEN.L": "Legal & General", "RTO.L": "Rentokil", "NXT.L": "Next", "WPP.L": "WPP", "VOD.L": "Vodafone", "RR.L": "Rolls-Royce", "EZJ.L": "easyJet", "IAG.L": "IAG"}
IBEX_MAP = {"ANA.MC": "Acciona", "ACX.MC": "Acerinox", "ACS.MC": "ACS", "AENA.MC": "Aena", "AMS.MC": "Amadeus", "BKT.MC": "Bankinter", "BBVA.MC": "BBVA", "CABK.MC": "CaixaBank", "CLNX.MC": "Cellnex", "ENG.MC": "Enagás", "ELE.MC": "Endesa", "FER.MC": "Ferrovial", "FDR.MC": "Fluidra", "GRF.MC": "Grifols", "IAG.MC": "IAG", "IBE.MC": "Iberdrola", "ITX.MC": "Inditex", "IDR.MC": "Indra", "COL.MC": "Inmobiliaria Colonial", "LOG.MC": "Logista", "MAP.MC": "Mapfre", "MEL.MC": "Meliá Hotels", "MRL.MC": "Merlin Properties", "NTGY.MC": "Naturgy", "RED.MC": "Redeia", "REP.MC": "Repsol", "ROVI.MC": "Rovi", "SAB.MC": "Sabadell", "SAN.MC": "Banco Santander", "SCYR.MC": "Sacyr", "TEF.MC": "Telefónica", "UNI.MC": "Unicaja"}
OMX_MAP = {"ABB.ST": "ABB", "ALFA.ST": "Alfa Laval", "ASSA-B.ST": "ASSA ABLOY", "ATCO-A.ST": "Atlas Copco A", "ATCO-B.ST": "Atlas Copco B", "AZN.ST": "AstraZeneca", "BOL.ST": "Boliden", "ELUX-B.ST": "Electrolux", "ERIC-B.ST": "Ericsson", "ESSITY-B.ST": "Essity", "EVO.ST": "Evolution", "GETI-B.ST": "Getinge", "HEXA-B.ST": "Hexagon", "HM-B.ST": "H&M", "INVE-B.ST": "Investor B", "KINV-B.ST": "Kinnevik", "NDA-SE.ST": "Nordea", "SAND.ST": "Sandvik", "SCA-B.ST": "SCA", "SEB-A.ST": "SEB", "SHB-A.ST": "Handelsbanken", "SKA-B.ST": "Skanska", "SKF-B.ST": "SKF", "STE-R.ST": "Stora Enso", "SWED-A.ST": "Swedbank", "SWMA.ST": "Swedish Match", "TEL2-B.ST": "Tele2", "TELIA.ST": "Telia", "VOLV-B.ST": "Volvo B"}
OBX_MAP = {"EQNR.OL": "Equinor", "DNB.OL": "DNB Bank", "AKBP.OL": "Aker BP", "TEL.OL": "Telenor", "NHY.OL": "Norsk Hydro", "MOWI.OL": "Mowi", "YAR.OL": "Yara International", "ORK.OL": "Orkla", "SUBC.OL": "Subsea 7", "TOM.OL": "Tomra Systems", "STB.OL": "Storebrand", "SALM.OL": "SalMar", "GJFS.OL": "Gjensidige", "AKER.OL": "Aker", "SCHA.OL": "Schibsted A", "FRO.OL": "Frontline", "TGS.OL": "TGS", "BAKKA.OL": "Bakkafrost", "LSG.OL": "Lerøy Seafood", "KOG.OL": "Kongsberg Gruppen", "NOD.OL": "Nordic Semiconductor", "NEL.OL": "Nel", "VAR.OL": "Vår Energi", "MPCC.OL": "MPC Container Ships"}

@st.cache_data(ttl=3600*24) # Cacheuje listę S&P 500 na 24h
def get_sp500_map():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        table = pd.read_html(io.StringIO(response.text))[0]
        table['Symbol'] = table['Symbol'].str.replace('.', '-', regex=False)
        return dict(zip(table['Symbol'], table['Security']))
    except:
        return {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp."}

def run_monte_carlo(data):
    returns = data['Close'].pct_change().dropna()
    if len(returns) < 30: return 0, 0
    mu, sigma = returns.mean(), returns.std()
    last = float(data['Close'].iloc[-1])
    sims = [last * (1 + np.random.normal(mu, sigma, 30)).prod() for _ in range(30)]
    med = np.median(sims)
    return round(med, 2), round(((med - last) / last) * 100, 1)

# Streamlit Cache - zapisuje wyniki skanowania na godzinę
@st.cache_data(ttl=3600, show_spinner=False)
def analyze_market(ticker_map, label):
    results = []
    curr_y = datetime.now().year
    y1, y2, y3 = curr_y - 1, curr_y - 2, curr_y - 3

    for t, full_name in ticker_map.items():
        try:
            df = yf.download(t, period="max", interval="1d", progress=False, auto_adjust=True, actions=True)
            if df.empty or len(df) < 200: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

            p = float(df['Close'].iloc[-1])

            # POBIERANIE DANYCH FUNDAMENTALNYCH
            try:
                ticker_obj = yf.Ticker(t)
                info = ticker_obj.info
            except:
                info = {}

            def safe_get(key, is_pct=False):
                val = info.get(key)
                if val is None or pd.isna(val): return "BRAK"
                return round(val * 100, 2) if is_pct else round(val, 2)

            pe = safe_get('trailingPE')
            fwd_pe = safe_get('forwardPE')
            pb = safe_get('priceToBook')
            roe = safe_get('returnOnEquity', is_pct=True)
            op_margin = safe_get('operatingMargins', is_pct=True)
            debt_eq = safe_get('debtToEquity') 
            eps_growth = safe_get('earningsGrowth', is_pct=True)

            int_pe = "⚪ NEUTRALNIE"
            if isinstance(pe, (int, float)):
                if pe < 15: int_pe = "🟢 TANIO"
                elif pe > 25: int_pe = "🔴 DROGO"

            int_roe = "⚪ NEUTRALNIE"
            if isinstance(roe, (int, float)):
                if roe > 15: int_roe = "🟢 WYSOKIE"
                elif roe < 5: int_roe = "🔴 NISKIE"

            int_eps = "⚪ NEUTRALNIE"
            if isinstance(eps_growth, (int, float)):
                if eps_growth > 0: int_eps = "🟢 WZROST"
                elif eps_growth < 0: int_eps = "🔴 SPADEK"

            # Dywidendy
            div_y1_str, div_y2_str, div_y3_str, div_yield = "BRAK", "BRAK", "BRAK", "BRAK"
            if 'Dividends' in df.columns:
                divs = df['Dividends']
                if not divs.empty and divs.sum() > 0:
                    divs_by_year = divs.groupby(divs.index.year).sum()
                    div_y1 = round(float(divs_by_year.get(y1, 0)), 2)
                    div_y2 = round(float(divs_by_year.get(y2, 0)), 2)
                    div_y3 = round(float(divs_by_year.get(y3, 0)), 2)
                    if div_y1 > 0:
                        div_y1_str = str(div_y1)
                        if p > 0: div_yield = round((div_y1 / p) * 100, 2)
                    if div_y2 > 0: div_y2_str = str(div_y2)
                    if div_y3 > 0: div_y3_str = str(div_y3)

            # Technika
            ath_val = df['High'].max()
            atl_val = df['Low'].min()
            ath = float(ath_val.item() if hasattr(ath_val, 'item') else ath_val)
            atl = float(atl_val.item() if hasattr(atl_val, 'item') else atl_val)
            pct_from_ath = round(((p - ath) / ath) * 100, 1)

            delta = df['Close'].diff()
            rsi = 100 - (100 / (1 + (delta.where(delta > 0, 0).rolling(14).mean() / -delta.where(delta < 0, 0).rolling(14).mean()))).iloc[-1]
            macd = (df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean())
            macd_s = macd.ewm(span=9).mean()
            sma20 = float(df['Close'].rolling(20).mean().iloc[-1])
            sma50 = float(df['Close'].rolling(50).mean().iloc[-1])
            sma100 = float(df['Close'].rolling(100).mean().iloc[-1])
            sma200 = float(df['Close'].rolling(200).mean().iloc[-1])
            std20 = df['Close'].rolling(20).std().iloc[-1]
            b_low = sma20 - (std20 * 2)
            b_up = sma20 + (std20 * 2)
            b_pct = (p - b_low) / (b_up - b_low) if (b_up - b_low) != 0 else 0.5
            v_rat = float(df['Volume'].iloc[-1] / df['Volume'].rolling(20).mean().iloc[-1])
            mc_t, mc_p = run_monte_carlo(df)
            p_low = df['Low'].shift(1).rolling(20).min().iloc[-1]
            smc = "💎 SMC BUY" if (df['Low'].iloc[-1] < p_low and p > p_low and v_rat > 1.2) else "Neutralny"

            # Wynik
            sigs = [rsi < 35, macd.iloc[-1] > macd_s.iloc[-1], p > sma20, p > sma50, p > sma100, p > sma200, b_pct < 0.15, v_rat > 1.3, smc == "💎 SMC BUY", mc_p > 3, pct_from_ath < -15]
            score = int(sum(1 for s in sigs if s))

            results.append({
                "Nazwa": full_name, "Ticker": t, "Cena": round(p,2), "ATH": round(ath,2), "ATL": round(atl,2), "% od ATH": pct_from_ath,
                f"Dyw. {y1}": div_y1_str, f"Dyw. {y2}": div_y2_str, f"Dyw. {y3}": div_y3_str, "Stopa Dyw. (%)": div_yield,
                "C/Z (P/E)": pe, "Int. C/Z": int_pe, "Forward C/Z": fwd_pe, "C/WK (P/B)": pb,
                "ROE (%)": roe, "Int. ROE": int_roe, "Marża Operac. (%)": op_margin, "Dług/Kapitał": debt_eq, "Wzrost EPS (%)": eps_growth, "Int. EPS": int_eps,
                "RSI": round(rsi,1), "Int. RSI": "🟢 KUPUJ" if rsi < 35 else ("🔴 SPRZEDAJ" if rsi > 65 else "⚪ HOLD"),
                "MACD": round(macd.iloc[-1],2), "Int. MACD": "🟢 KUPUJ" if macd.iloc[-1] > macd_s.iloc[-1] else "🔴 SPRZEDAJ",
                "SMA 20": round(sma20,2), "SMA 50": round(sma50,2), "SMA 100": round(sma100,2), "SMA 200": round(sma200,2),
                "Int. Trend (SMA)": "🟢 SILNA HOSSA" if p > sma200 and p > sma50 else ("🔴 BESSA" if p < sma200 else "⚪ KONSOLIDACJA"),
                "%B (Bollinger)": round(b_pct,2), "Int. Wstęgi": "🟢 ODBICIE (KUP)" if b_pct < 0.15 else "⚪ NEUTRALNIE",
                "Wolumen (Ratio)": round(v_rat,2), "Int. Wolumen": "🟢 AKUMULACJA" if v_rat > 1.3 and delta.iloc[-1] > 0 else "⚪ ZWYKŁY",
                "SMC": smc, "MC Target (30d)": mc_t, "MC Prognoza %": mc_p, "Int. MC": "🟢 WZROST" if mc_p > 3 else "🔴 SPADEK/BOCZNIAK",
                "SUMA BUY": score
            })
        except Exception as e: continue
    
    if not results: return pd.DataFrame()
    return pd.DataFrame(results).sort_values(by='SUMA BUY', ascending=False)

def generate_excel_in_memory(data_dict):
    """Generuje plik Excel do pamięci (BytesIO), aby można go było pobrać z poziomu Streamlit."""
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    workbook = writer.book
    fmt_green = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100', 'bold': True})
    fmt_red = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
    fmt_yellow = workbook.add_format({'bg_color': '#FFEB9C', 'font_color': '#9C6500'})
    fmt_header = workbook.add_format({'bg_color': '#2C3E50', 'font_color': '#FFFFFF', 'bold': True, 'border': 1})

    for sheet_name, df in data_dict.items():
        if df.empty: continue
        df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
        ws = writer.sheets[sheet_name[:31]]
        ws.freeze_panes(1, 1)
        ws.autofilter(0, 0, len(df), len(df.columns) - 1)

        for col_num, value in enumerate(df.columns.values):
            ws.write(0, col_num, value, fmt_header)
            col_len = max(df[value].astype(str).map(len).max(), len(value)) + 2
            ws.set_column(col_num, col_num, min(col_len, 25))

        for row in range(1, len(df) + 1):
            for col in range(len(df.columns)):
                cell_val = str(df.iloc[row-1, col])
                if any(x in cell_val for x in ["🟢", "KUP", "BUY", "HOSSA", "WZROST", "AKUMULACJA", "ODBICIE", "TANIO", "WYSOKIE"]):
                    ws.write(row, col, df.iloc[row-1, col], fmt_green)
                elif any(x in cell_val for x in ["🔴", "SPRZEDAJ", "SELL", "BESSA", "SPADEK", "DROGO", "NISKIE"]):
                    ws.write(row, col, df.iloc[row-1, col], fmt_red)
                elif any(x in cell_val for x in ["⚪", "HOLD", "KONSOLIDACJA", "NEUTRALNIE", "ZWYKŁY"]):
                    ws.write(row, col, df.iloc[row-1, col], fmt_yellow)
    writer.close()
    processed_data = output.getvalue()
    return processed_data

# ==============================================================================
# INTERFEJS STREAMLIT DLA PICKPIVOT
# ==============================================================================

st.set_page_config(page_title="PickPivot Skaner", page_icon="📊", layout="wide")

st.title("📊 PickPivot: Global Market Scanner")
st.markdown("Witaj w zaawansowanym module analitycznym PickPivot. System weryfikuje dane techniczne oraz fundamentalne giełd z Europy i USA.")

ALL_MARKETS = {
    "Polska (WIG)": {**WIG20_MAP, **MWIG40_MAP},
    "Niemcy (DAX)": DAX_MAP,
    "Francja (CAC 40)": CAC40_MAP,
    "UK (FTSE 100)": FTSE_MAP,
    "Hiszpania (IBEX 35)": IBEX_MAP,
    "Szwecja (OMX 30)": OMX_MAP,
    "Norwegia (OBX)": OBX_MAP,
    "USA (S&P 500)": get_sp500_map()
}

st.sidebar.header("Ustawienia Skanowania")
selected_markets = st.sidebar.multiselect(
    "Wybierz rynki do analizy:",
    options=list(ALL_MARKETS.keys()),
    default=["Polska (WIG)"] # Domyślnie zaznaczony tylko WIG dla szybkości
)

if st.sidebar.button("Uruchom Analizę", type="primary"):
    if not selected_markets:
        st.warning("Proszę wybrać przynajmniej jeden rynek z panelu bocznego.")
    else:
        st.info("Pobieranie danych z Yahoo Finance... To może zająć kilka minut w zależności od liczby wybranych rynków.")
        
        # Pojemnik na wyniki
        final_results = {}
        
        # Pasek postępu
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, market_name in enumerate(selected_markets):
            status_text.text(f"Analizuję: {market_name}...")
            # Skanowanie
            df_market = analyze_market(ALL_MARKETS[market_name], market_name)
            final_results[market_name] = df_market
            # Aktualizacja postępu
            progress_bar.progress((i + 1) / len(selected_markets))
            
        status_text.text("Analiza zakończona!")
        
        # --- PREZENTACJA WYNIKÓW W ZAKŁADKACH ---
        st.subheader("Wyniki Skanowania")
        tabs = st.tabs(list(final_results.keys()))
        
        for tab, market_name in zip(tabs, final_results.keys()):
            with tab:
                df = final_results[market_name]
                if not df.empty:
                    # Wyświetlenie interaktywnej tabeli w Streamlit
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.write("Brak danych do wyświetlenia.")
                    
        # --- OPCJA POBRANIA EXCELA ---
        st.markdown("---")
        st.subheader("Eksport Danych")
        
        excel_data = generate_excel_in_memory(final_results)
        
        st.download_button(
            label="📥 Pobierz sformatowany plik Excel",
            data=excel_data,
            file_name=f"PickPivot_Global_Report_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
