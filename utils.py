from collections import Counter
from odds_config import odds

def get_combination_count(number):
    digits = list(str(number).zfill(4))  # 补足4位
    counts = Counter(digits).values()
    counts = sorted(counts, reverse=True)

    if counts == [4]:
        return 1
    elif counts == [3, 1]:
        return 4
    elif counts == [2, 2]:
        return 6
    elif counts == [2, 1, 1]:
        return 12
    elif counts == [1, 1, 1, 1]:
        return 24
    else:
        return 0  # 无效号码

def calculate_payout(market, number, amount, type_char, bet_type):
    combo_count = get_combination_count(number)
    base_odds = odds[market][type_char]["1st"]

    if bet_type == "Box":
        total_cost = amount * combo_count
        payout = base_odds
    elif bet_type == "IBox":
        total_cost = amount
        payout = base_odds / combo_count if combo_count > 0 else 0
    else:
        total_cost = amount
        payout = base_odds

    return round(total_cost, 2), round(payout, 2)
