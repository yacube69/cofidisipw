def calculate_in05(data: dict) -> dict:
    """
    Calculates the IN05 bankruptcy model for Czech companies.
    Returns the score and the corresponding zone (Safe, Grey, Distress).
    """
    # Načtení hodnot, pokud chybí, dosadí se 0.0
    total_assets = data.get('total_assets', 0.0)
    external_sources = data.get('external_sources', 0.0)
    operating_result = data.get('operating_result', 0.0)  # EBIT
    interest_expense = data.get('interest_expense', 0.0)
    net_turnover = data.get('net_turnover', 0.0)
    current_assets = data.get('current_assets', 0.0)
    liabilities_short_term = data.get('liabilities_short_term', 0.0)

    # V1: Total Assets / Total Liabilities
    v1 = (total_assets / external_sources) if external_sources != 0 else 0.0

    # V2: EBIT / Interest Expense
    # Pravidlo autorů: Pokud jsou úroky 0 nebo se blíží nule, nebo je poměr vyšší než 9, fixuje se hodnota na 9.
    if interest_expense <= 0:
        v2 = 9.0
    else:
        v2 = min((operating_result / interest_expense), 9.0)

    # V3: EBIT / Total Assets
    v3 = (operating_result / total_assets) if total_assets != 0 else 0.0

    # V4: Revenue / Total Assets
    v4 = (net_turnover / total_assets) if total_assets != 0 else 0.0

    # V5: Current Assets / Short-term Liabilities
    # Poznámka z dokumentace: bank_loans_short_term (C.II.2.) jsou již obsaženy v liabilities_short_term (C.II.),
    # proto je zde znovu nepřičítáme, abychom zamezili duplicitě.
    v5 = (current_assets / liabilities_short_term) if liabilities_short_term != 0 else 0.0

    # Výpočet finálního skóre
    score = (0.13 * v1) + (0.04 * v2) + (3.97 * v3) + (0.21 * v4) + (0.09 * v5)

    # Určení zóny
    if score > 1.6:
        zone = "Safe"
    elif score < 0.9:
        zone = "Distress"
    else:
        zone = "Grey"

    return {"score": round(score, 4), "zone": zone}


def calculate_altman_z_prime(data: dict) -> dict:
    """
    Calculates the Altman Z'-score for private (non-listed) companies.
    Returns the score and the corresponding zone (Safe, Grey, Distress).
    """
    total_assets = data.get('total_assets', 0.0)
    external_sources = data.get('external_sources', 0.0)
    operating_result = data.get('operating_result', 0.0)  # EBIT
    retained_earnings = data.get('retained_earnings', 0.0)
    equity = data.get('equity', 0.0)
    net_turnover = data.get('net_turnover', 0.0)
    current_assets = data.get('current_assets', 0.0)
    liabilities_short_term = data.get('liabilities_short_term', 0.0)

    # Pokud nemá firma aktiva, nemá smysl počítat (zamezení dělení nulou u většiny ukazatelů)
    if total_assets == 0:
        return {"score": 0.0, "zone": "Distress"}

    # X1: Working Capital / Total Assets
    working_capital = current_assets - liabilities_short_term
    x1 = working_capital / total_assets

    # X2: Retained Earnings / Total Assets
    x2 = retained_earnings / total_assets

    # X3: EBIT / Total Assets
    x3 = operating_result / total_assets

    # X4: Book Value of Equity / Total Liabilities
    x4 = (equity / external_sources) if external_sources != 0 else 0.0

    # X5: Revenue / Total Assets
    x5 = net_turnover / total_assets

    # Výpočet finálního skóre
    score = (0.717 * x1) + (0.847 * x2) + (3.107 * x3) + (0.420 * x4) + (0.998 * x5)

    # Určení zóny
    if score > 2.9:
        zone = "Safe"
    elif score < 1.23:
        zone = "Distress"
    else:
        zone = "Grey"

    return {"score": round(score, 4), "zone": zone}
