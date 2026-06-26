def pobierz_wszystko_z_okresu(data_start_str, data_koniec_str, sesja, nazwa_podatku, kod_sygnatury):
    dokumenty_podatkowe = []
    page = 0
    while True:
        url = SEARCH_API_URL_BASE.format(page=page)
        
        # Zaktualizowany PAYLOAD z wymuszonym pustym zapytaniem "query"
        payload = {
            "query": "",
            "filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": data_start_str, "DT_WYD_end": data_koniec_str},
            "columns": ["SYG", "ID_INFORMACJI", "DT_WYD"],
            "searchInFullPhrase": False, "searchInContent": False, "searchInSynonyms": False, "warunkiDodatkowe": []
        }
        
        try:
            response = sesja.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}, timeout=15)
            if response.status_code == 200:
                dane = response.json()
                wyniki = dane.get('content') or dane.get('items') or []
                if not wyniki:
                    for k, v in dane.items():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) and ('id' in v[0] or 'ID_INFORMACJI' in v[0]):
                            wyniki = v; break
                for d in wyniki:
                    sygnatura = str(d.get('SYG', '')).upper()
                    if kod_sygnatury in sygnatura:
                        doc_id = str(d.get('id') or d.get('ID_INFORMACJI'))
                        if doc_id: dokumenty_podatkowe.append({"id": doc_id, "sygnatura": sygnatura, "typ": nazwa_podatku, "data": str(d.get('DT_WYD', '')).split('T')[0]})
                if len(wyniki) < 100: break
                page += 1
                time.sleep(0.2)
            else: return dokumenty_podatkowe, "ERROR"
        except: return dokumenty_podatkowe, "ERROR"
    return dokumenty_podatkowe, "OK"
