from flask import Flask, render_template, request
from odds_config import odds

value = odds["M"]["S"]["1st"]
print("M 市场 小 投注 头奖赔率 =", value)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html', odds=odds)

@app.route('/bet')
def bet():
    return render_template('bet.html')

@app.route('/report')
def report():
    return render_template('report.html')

@app.route('/history')
def history():
    return render_template('history.html')

if __name__ == '__main__':
    app.run(debug=True)
