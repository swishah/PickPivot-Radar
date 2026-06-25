import streamlit as st
import pandas as pd
import io
import re
from bs4 import BeautifulSoup
from datetime import datetime

# --- SILNIK PARSOWANIA XML/XAdES ---
def parse_financial_xml(file_bytes):
    soup = BeautifulSoup(file_bytes, 'xml')

    def get_value(tag_regex):
        tag = soup.find(re.compile(tag_regex, re.I))
        if tag:
            kwota = tag.find(re.compile('KwotaA', re.I))
            if kwota and kwota.text:
                try:
                    return float(kwota.text.strip())
                except:
                    pass
        return 0.0

    data = {
        "AktywaRazem": get_value("AktywaRazem") or get_value("SumaAktywow"),
        "AktywaObrotowe": get_value("AktywaObrotowe"),
        "Zapasy": get_value("Zapasy"),
        "NaleznosciKrotkoterminowe": get_value("NaleznosciKrotkoterminowe"),
        "InwestycjeKrotkoterminowe": get_value("InwestycjeKrotkoterminowe"),
        "KapitalWlasny": get_value("KapitalFunduszWlasny") or get_value("KapitalWlasny"),
        "ZobowiazaniaOgolem": get_value("ZobowiazaniaIRezerwyNaZobowiazania") or get_value("ZobowiazaniaOgolem"),
        "ZobowiazaniaKrotkoterminowe": get_value("ZobowiazaniaKrotkoterminowe"),
        "PrzychodySprzedaz": get_value("PrzychodyNettoZeSprzedazy") or get_value("PrzychodyNetto"),
        "ZyskOperacyjny": get_value("ZyskStrataZDzialalnosciOperacyjnej") or get_value("ZyskOperacyjny"),
        "ZyskBrutto": get_value("ZyskStrataBrutto"),
        "ZyskNetto": get_value("ZyskStrataNetto"),
        "KosztyDzialalnosciOperacyjnej": get_value("KosztyDzialalnosciOperacyjnej") or get_value("KosztWytworzeniaSprzedanychProduktow"),
        "KosztyFinansowe": get_value("KosztyFinansowe")
    }
    return data

# --- SILNIK OBLICZENIOWY WSKAŹNIKÓW (15 KPI) ---
def calculate_ratios(data):
    ratios = []

    def safe_div(n, d):
        return n / d if d and d != 0 else 0.0

    # PŁYNNOŚĆ
    cr = safe_div(data['AktywaObrotowe'], data['ZobowiazaniaKrotkoterminowe'])
    ratios.append({"Grupa": "Płynność", "Wskaźnik": "Płynność bieżąca (Current Ratio)", "Wynik": round(cr, 2), "Interpretacja": "🟢 Optymalna" if 1.2 <= cr <= 2.0 else ("🔴 Zagrożenie" if cr < 1.2 else "⚪ Nadpłynność")})

    qr = safe_div((data['AktywaObrotowe'] - data['Zapasy']), data['ZobowiazaniaKrotkoterminowe'])
    ratios.append({"Grupa": "Płynność", "Wskaźnik": "Płynność szybka (Quick Ratio)", "Wynik": round(qr, 2), "Interpretacja": "🟢 Optymalna" if 1.0 <= qr <= 1.2 else ("🔴 Niska" if qr < 1.0 else "⚪ Wysoka")})

    cash_r = safe_div(data['InwestycjeKrotkoterminowe'], data['ZobowiazaniaKrotkoterminowe'])
    ratios.append({"Grupa": "Płynność", "Wskaźnik": "Płynność gotówkowa (Cash Ratio)", "Wynik": round(cash_r, 2), "Interpretacja": "🟢 Bezpieczna" if cash_r >= 0.2 else "🔴 Niska"})

    # RENTOWNOŚĆ
    ros = safe_div(data['ZyskNetto'], data['PrzychodySprzedaz']) * 100
    ratios.append({"Grupa": "Rentowność", "Wskaźnik": "Rentowność sprzedaży (ROS) %", "Wynik": round(ros, 2), "Interpretacja": "🟢 Dodatnia" if ros > 0 else "🔴 Ujemna (Strata)"})

    roa = safe_div(data['ZyskNetto'], data['AktywaRazem']) * 100
    ratios.append({"Grupa": "Rentowność", "Wskaźnik": "Rentowność aktywów (ROA) %", "Wynik": round(roa, 2), "Interpretacja": "🟢 Generuje zysk" if roa > 0 else "🔴 Niszczy kapitał"})

    roe = safe_div(data['ZyskNetto'], data['KapitalWlasny']) * 100
    ratios.append({"Grupa": "Rentowność", "Wskaźnik": "Rentowność kapitału (ROE) %", "Wynik": round(roe, 2), "Interpretacja": "🟢 Atrakcyjna" if roe > roa else "🔴 Niska"})

    marza_op = safe_div(data['ZyskOperacyjny'], data['PrzychodySprzedaz']) * 100
    ratios.append({"Grupa": "Rentowność", "Wskaźnik": "Marża operacyjna %", "Wynik": round(marza_op, 2), "Interpretacja": "🟢 Zyskowna" if marza_op > 0 else "🔴 Stratna"})

    marza_br = safe_div(data['ZyskBrutto'], data['PrzychodySprzedaz']) * 100
    ratios.append({"Grupa": "Rentowność", "Wskaźnik": "Marża brutto %", "Wynik": round(marza_br, 2), "Interpretacja": "🟢 Zyskowna" if marza_br > 0 else "🔴 Stratna"})

    # ZADŁUŻENIE
    dr = safe_div(data['ZobowiazaniaOgolem'], data['AktywaRazem']) * 100
    ratios.append({"Grupa": "Zadłużenie", "Wskaźnik": "Ogólne zadłużenie (Debt Ratio) %", "Wynik": round(dr, 2), "Interpretacja": "🟢 Bezpieczne (40-60%)" if 40 <= dr <= 60 else ("🔴 Zbyt wysokie" if dr > 60 else "⚪ Niskie (Konserwatywne)")})

    dte = safe_div(data['ZobowiazaniaOgolem'], data['KapitalWlasny']) * 100
    ratios.append({"Grupa": "Zadłużenie", "Wskaźnik": "Zadłużenie kapitału (D/E) %", "Wynik": round(dte, 2), "Interpretacja": "🟢 Umiarkowane" if dte <= 200 else "🔴 Wysokie ryzyko"})

    icr = safe_div(data['ZyskOperacyjny'], data['KosztyFinansowe'])
    ratios.append({"Grupa": "Zadłużenie", "Wskaźnik": "Pokrycie odsetek (ICR)", "Wynik": round(icr, 2), "Interpretacja": "🟢 Bezpieczne" if icr >= 3.0 else ("🔴 Zagrożenie" if icr < 1.0 else "⚪ Wymaga uwagi")})

    # SPRAWNOŚĆ (EFEKTYWNOŚĆ)
    ato = safe_div(data['PrzychodySprzedaz'], data['AktywaRazem'])
    ratios.append({"Grupa": "Sprawność", "Wskaźnik": "Rotacja aktywów (ATO)", "Wynik": round(ato, 2), "Interpretacja": "🟢 Efektywne" if ato > 1.0 else "🔴 Niska efektywność"})

    koszty_proxy = data['KosztyDzialalnosciOperacyjnej'] if data['KosztyDzialalnosciOperacyjnej'] > 0 else data['PrzychodySprzedaz']
    
    inv_turn = safe_div(data['Zapasy'], koszty_proxy) * 365
    ratios.append({"Grupa": "Sprawność", "Wskaźnik": "Rotacja zapasów (dni)", "Wynik": round(inv_turn, 0), "Interpretacja": "⚪ Zależy od branży"})

    rec_turn = safe_div(data['NaleznosciKrotkoterminowe'], data['PrzychodySprzedaz']) * 365
    ratios.append({"Grupa": "Sprawność", "Wskaźnik": "Rotacja należności (dni)", "Wynik": round(rec_turn, 0), "Interpretacja": "🟢 Szybka ściągalność" if rec_turn < 60 else "🔴 Zatory płatnicze"})

    pay_turn = safe_div(data['ZobowiazaniaKrotkoterminowe'], koszty_proxy) * 365
    ratios.append({"Grupa": "Sprawność", "Wskaźnik": "Rotacja zobowiązań (dni)", "Wynik": round(pay_turn, 0), "Interpretacja": "⚪ Wymaga analizy cash-flow"})

    return pd.DataFrame(ratios)

# --- GŁÓWNA FUNKCJA WYSWIETLAJĄCA MODUŁ ---
def run_module():
    st.title("📈 Analiza Wskaźnikowa z e-Sprawozdań")
    st.markdown("Automatyczny audyt kondycji finansowej spółki. Wyłapuje 15 najistotniejszych wskaźników z urzędowego pliku XML lub XAdES i dokonuje ich błyskawicznej interpretacji.")

    uploaded_file = st.file_uploader("📂 Wgraj sprawozdanie finansowe (*.xml lub *.xades)", type=['xml', 'xades'])

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        
        with st.spinner("Skanowanie i parsowanie ukrytych tagów pliku urzędowego..."):
            dane_finansowe = parse_financial_xml(file_bytes)
            
        if not dane_finansowe or dane_finansowe["AktywaRazem"] == 0:
            st.error("❌ Błąd analizy: Nie udało się odnaleźć kluczowych danych finansowych w tym pliku. Upewnij się, że to poprawne e-Sprawozdanie (KRS).")
        else:
            st.success("✔️ Sprawozdanie rozkodowane pomyślnie!")
            
            with st.expander("🔎 Podgląd wyodrębnionych danych źródłowych (Weryfikacja)"):
                df_raw = pd.DataFrame(list(dane_finansowe.items()), columns=["Pozycja Bilansowa / RZiS", "Kwota (Bieżący Rok)"])
                st.dataframe(df_raw, use_container_width=True, hide_index=True)
                
            st.markdown("### 📊 Wynik Analizy Wskaźnikowej (Audyt CFO)")
            df_ratios = calculate_ratios(dane_finansowe)
            
            st.dataframe(df_ratios, use_container_width=True, hide_index=True)
            
            st.markdown("---")
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_ratios.to_excel(writer, sheet_name='Analiza Wskaznikowa', index=False)
                df_raw.to_excel(writer, sheet_name='Dane Zrodlowe', index=False)
                
                workbook = writer.book
                worksheet = writer.sheets['Analiza Wskaznikowa']
                fmt_header = workbook.add_format({'bg_color': '#2C3E50', 'font_color': '#FFFFFF', 'bold': True})
                for col_num, value in enumerate(df_ratios.columns.values):
                    worksheet.write(0, col_num, value, fmt_header)
                worksheet.set_column('A:A', 15)
                worksheet.set_column('B:B', 35)
                worksheet.set_column('C:C', 12)
                worksheet.set_column('D:D', 30)

            st.download_button(
                label="📥 Pobierz Audyt Wskaźnikowy (Excel)",
                data=output.getvalue(),
                file_name=f"Analiza_CFO_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )
