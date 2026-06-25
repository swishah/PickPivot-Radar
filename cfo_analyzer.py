import streamlit as st
import pandas as pd
import io
import re
import time
from bs4 import BeautifulSoup
from datetime import datetime

# --- 1. USTAWIENIA STRONY ---
try:
    st.set_page_config(page_title="PickPivot CFO", page_icon="📈", layout="wide")
except:
    pass

# --- 2. ZAAWANSOWANY SILNIK PARSOWANIA ---
def parse_financial_hybrid(file_bytes, filename):
    content = file_bytes.decode('utf-8', errors='ignore')
    
    # ---------------------------------------------------------
    # FAZA 1: Odczyt surowego XML/XAdES (Inteligentne schematy KRS)
    # ---------------------------------------------------------
    soup_xml = BeautifulSoup(content, 'xml')
    
    def get_vals(parent, tag_names):
        if not parent: return 0.0
        for name in tag_names:
            tag = parent.find(name=re.compile(rf'^(.*:)?{name}$', re.I))
            if tag:
                kwota = tag.find(name=re.compile(r'^(.*:)?KwotaA$', re.I))
                if kwota and kwota.text:
                    try:
                        return float(kwota.text.strip())
                    except: pass
        return 0.0

    bilans = soup_xml.find(name=re.compile(r'^(.*:)?Bilans.*$', re.I))
    rzis = soup_xml.find(name=re.compile(r'^(.*:)?RZiS.*$', re.I))
    
    aktywa = get_vals(bilans, ['AktywaRazem', 'SumaAktywow', 'Aktywa'])
    
    if aktywa > 0:
        akt_obr = get_vals(bilans, ['AktywaObrotowe', 'Aktywa_B'])
        zapasy = get_vals(bilans, ['Zapasy', 'Aktywa_B_I'])
        nal_krotko = get_vals(bilans, ['NaleznosciKrotkoterminowe', 'Aktywa_B_II'])
        inw_krotko = get_vals(bilans, ['InwestycjeKrotkoterminowe', 'Aktywa_B_III'])
        kap_wlasny = get_vals(bilans, ['KapitalFunduszWlasny', 'KapitalWlasny', 'Pasywa_A'])
        zob_ogolem = get_vals(bilans, ['ZobowiazaniaIRezerwyNaZobowiazania', 'ZobowiazaniaOgolem', 'Pasywa_B'])
        zob_krotko = get_vals(bilans, ['ZobowiazaniaKrotkoterminowe', 'Pasywa_B_III'])
        
        przych = get_vals(rzis, ['PrzychodyNettoZeSprzedazy', 'PrzychodyNetto'])
        zysk_op = get_vals(rzis, ['ZyskStrataZDzialalnosciOperacyjnej'])
        zysk_brutto = get_vals(rzis, ['ZyskStrataBrutto'])
        zysk_netto = get_vals(rzis, ['ZyskStrataNetto'])
        koszty_fin = get_vals(rzis, ['KosztyFinansowe'])
        
        koszty_op = get_vals(rzis, ['KosztyDzialalnosciOperacyjnej'])
        if koszty_op == 0.0:
            koszty_op = get_vals(rzis, ['KosztWytworzeniaSprzedanychProduktow']) + get_vals(rzis, ['KosztySprzedazy']) + get_vals(rzis, ['KosztyOgolnegoZarzadu'])
            
        rzis_kalk = rzis.find(name=re.compile(r'^(.*:)?RZiSKalk$', re.I)) if rzis else None
        rzis_porown = rzis.find(name=re.compile(r'^(.*:)?RZiSPorown$', re.I)) if rzis else None
        
        if rzis_kalk:
            if przych == 0: przych = get_vals(rzis_kalk, ['A'])
            if koszty_op == 0: koszty_op = get_vals(rzis_kalk, ['B']) + get_vals(rzis_kalk, ['D'])
            if zysk_op == 0: zysk_op = get_vals(rzis_kalk, ['E']) + get_vals(rzis_kalk, ['F']) - get_vals(rzis_kalk, ['G'])
            if koszty_fin == 0: koszty_fin = get_vals(rzis_kalk, ['I'])
            if zysk_brutto == 0: zysk_brutto = get_vals(rzis_kalk, ['J'])
            if zysk_netto == 0: zysk_netto = get_vals(rzis_kalk, ['L'])
        elif rzis_porown:
            if przych == 0: przych = get_vals(rzis_porown, ['A'])
            if koszty_op == 0: koszty_op = get_vals(rzis_porown, ['B'])
            if zysk_op == 0: zysk_op = get_vals(rzis_porown, ['F'])
            if koszty_fin == 0: koszty_fin = get_vals(rzis_porown, ['H'])
            if zysk_brutto == 0: zysk_brutto = get_vals(rzis_porown, ['I'])
            if zysk_netto == 0: zysk_netto = get_vals(rzis_porown, ['L'])
        elif rzis: 
            if przych == 0: przych = get_vals(rzis, ['A'])
            if koszty_op == 0: koszty_op = get_vals(rzis, ['B'])
            if zysk_op == 0: zysk_op = przych - koszty_op
            if zysk_netto == 0: zysk_netto = get_vals(rzis, ['F'])
            if zysk_brutto == 0: zysk_brutto = zysk_netto
            
        data_xml = {
            "AktywaRazem": aktywa,
            "AktywaObrotowe": akt_obr,
            "Zapasy": zapasy,
            "NaleznosciKrotkoterminowe": nal_krotko,
            "InwestycjeKrotkoterminowe": inw_krotko,
            "KapitalWlasny": kap_wlasny,
            "ZobowiazaniaOgolem": zob_ogolem,
            "ZobowiazaniaKrotkoterminowe": zob_krotko,
            "PrzychodySprzedaz": przych,
            "ZyskOperacyjny": zysk_op,
            "ZyskBrutto": zysk_brutto,
            "ZyskNetto": zysk_netto,
            "KosztyDzialalnosciOperacyjnej": koszty_op,
            "KosztyFinansowe": koszty_fin
        }
        return data_xml, "XML/XAdES (Oficjalny algorytm KRS - Zgodność ze schematami)"

    # ---------------------------------------------------------
    # FAZA 2: Odczyt z plików przeglądarkowych HTML
    # ---------------------------------------------------------
    soup_html = BeautifulSoup(content, 'lxml')
    
    def get_html_value(keywords):
        for kw in keywords:
            elems = soup_html.find_all(string=re.compile(kw, re.I))
            for elem in elems:
                parent = elem.find_parent(['td', 'th', 'div', 'span'])
                if parent:
                    for sibling in parent.find_next_siblings(['td', 'th', 'div']):
                        text_val = sibling.get_text(strip=True).replace(' ', '').replace(' ', '').replace(',', '.')
                        try:
                            if re.match(r'^-?\d+(\.\d+)?$', text_val):
                                return float(text_val)
                        except: pass
        return 0.0

    data_html = {
        "AktywaRazem": get_html_value(["Aktywa razem", "Suma aktywów"]),
        "AktywaObrotowe": get_html_value(["Aktywa obrotowe"]),
        "Zapasy": get_html_value(["Zapasy"]),
        "NaleznosciKrotkoterminowe": get_html_value(["Należności krótkoterminowe", "Naleznosci krotkoterminowe"]),
        "InwestycjeKrotkoterminowe": get_html_value(["Inwestycje krótkoterminowe"]),
        "KapitalWlasny": get_html_value(["Kapitał własny", "Kapitał (fundusz) własny", "Kapital wlasny"]),
        "ZobowiazaniaOgolem": get_html_value(["Zobowiązania i rezerwy na zobowiązania", "Zobowiązania ogółem"]),
        "ZobowiazaniaKrotkoterminowe": get_html_value(["Zobowiązania krótkoterminowe"]),
        "PrzychodySprzedaz": get_html_value(["Przychody netto ze sprzedaży", "Przychody netto", "Przychody ze sprzedaży"]),
        "ZyskOperacyjny": get_html_value(["Zysk z działalności operacyjnej", "Strata z działalności operacyjnej", "Zysk (strata) z działalności operacyjnej"]),
        "ZyskBrutto": get_html_value(["Zysk brutto", "Strata brutto", "Zysk (strata) brutto"]),
        "ZyskNetto": get_html_value(["Zysk netto", "Strata netto", "Zysk (strata) netto"]),
        "KosztyDzialalnosciOperacyjnej": get_html_value(["Koszty działalności operacyjnej", "Koszty dzialalnosci operacyjnej", "Koszty operacyjne"]),
        "KosztyFinansowe": get_html_value(["Koszty finansowe"])
    }

    if data_html["AktywaRazem"] > 0:
        return data_html, "HTML (Ekstrakcja ze struktury wizualnej przeglądarki)"

    return None, "Nie rozpoznano struktury finansowej"

# --- 3. SILNIK OBLICZENIOWY WSKAŹNIKÓW (15 KPI) ---
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

# --- 4. GŁÓWNA FUNKCJA WYSWIETLAJĄCA MODUŁ ---
def run_module():
    st.title("📈 Analiza Wskaźnikowa z e-Sprawozdań")
    st.markdown("Automatyczny audyt kondycji finansowej spółki. Silnik rozpoznaje urzędowe pliki **XML/XAdES**, dopasowuje strukturę do odpowiedniej Jednostki (Inna, Mała, Mikro) i dokonuje błyskawicznej interpretacji.")

    uploaded_file = st.file_uploader("📂 Wgraj plik ze sprawozdaniem finansowym", type=['xml', 'xades', 'html', 'htm'])

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        
        with st.spinner("Skanowanie i dopasowywanie struktury pliku..."):
            dane_finansowe, metoda = parse_financial_hybrid(file_bytes, uploaded_file.name)
            
        if not dane_finansowe:
            st.error("❌ Błąd analizy: System nie odnalazł kluczowych tagów. Sprawdź, czy plik na pewno pochodzi z systemu eKRS.")
        else:
            st.success(f"✔️ Sprawozdanie rozkodowane pomyślnie! Wykryty format bazy: **{metoda}**")
            
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

if __name__ == "__main__":
    if st.session_state.get('authenticated', False):
        run_module()
