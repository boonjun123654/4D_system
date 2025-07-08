from flask import Flask, render_template, request
from odds_config import odds
from datetime import datetime, timedelta
from utils import calculate_payout

value = odds["M"]["S"]["1st"]
print("M 市场 小 投注 头奖赔率 =", value)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html', odds=odds)

@app.route('/bet', methods=['GET', 'POST'])
def bet():
    date_today = datetime.today()
    results = []

    if request.method == 'POST':
        for i in range(1, 13):
            number = request.form.get(f'number{i}')
            if not number or not number.isdigit():
                continue
            number = number.zfill(4)

            bet_type = request.form.get(f'type{i}', '正字')  # 正字 / Box / IBox

            for type_char in ['B', 'S', 'A', 'C']:
                amount_str = request.form.get(f'{type_char}{i}', '0')
                try:
                    amount = float(amount_str)
                except:
                    amount = 0

                if amount <= 0:
                    continue

                for market in ['M','P','T','S','B','K','W','H','E']:
                    if request.form.get(f'market{i}_{market}'):
                        total_cost, payout = calculate_payout(market, number, amount, type_char, bet_type)
                        results.append({
                            'row': i,
                            'number': number,
                            'type': type_char,
                            'market': market,
                            'bet_type': bet_type,
                            'amount': amount,
                            'total': total_cost,
                            'payout': payout
                        })

    return render_template('bet.html', date_today=date_today, timedelta=timedelta, results=results)

@app.route('/report')
def report():
    return render_template('report.html')

@app.route('/history')
def history():
    return render_template('history.html')

if __name__ == '__main__':
    app.run(debug=True)
