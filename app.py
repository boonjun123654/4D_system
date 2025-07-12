from flask import Flask, render_template, request, redirect, flash
from odds_config import odds
from datetime import datetime, timedelta
from utils import calculate_payout
from models import db, FourDBet,Agent4D
from collections import defaultdict
import os

value = odds["M"]["S"]["1st"]
print("M 市场 小 投注 头奖赔率 =", value)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key'

db.init_app(app)
with app.app_context():
    db.create_all()

@app.route('/')
def index():
    return render_template('index.html', odds=odds)

@app.route('/bet', methods=['GET', 'POST'])
def bet():
    date_today = datetime.today()
    results = []

    if request.method == 'POST':
        for i in range(1, 13):
            number = request.form.get(f'number{i}', '').strip()
            if not number or not number.isdigit():
                continue
            number = number.zfill(4)

            bet_type = request.form.get(f'type{i}', '正字')
            B = float(request.form.get(f'B{i}', 0) or 0)
            S = float(request.form.get(f'S{i}', 0) or 0)
            A = float(request.form.get(f'A{i}', 0) or 0)
            C = float(request.form.get(f'C{i}', 0) or 0)

            # 日期
            dates = [
                (date_today + timedelta(days=d)).strftime("%d/%m")
                for d in range(7)
                if request.form.get(f'date{i}_{d}') == 'on'
            ]

            # 市场
            markets = [
                m for m in ['M','P','T','S','B','K','W','H','E']
                if request.form.get(f'market{i}_{m}') == 'on'
            ]

            if not dates or not markets:
                continue

            # 组合数计算
            def get_comb_count(n):
                digits = list(n)
                counts = {d: digits.count(d) for d in set(digits)}
                pattern = sorted(counts.values(), reverse=True)
                if pattern == [4]: return 1
                if pattern == [3, 1]: return 4
                if pattern == [2, 2]: return 6
                if pattern == [2, 1, 1]: return 12
                if pattern == [1, 1, 1, 1]: return 24
                return 1

            factor = get_comb_count(number) if bet_type == 'Box' else 1
            total = (B + S + A + C) * factor * len(dates) * len(markets)

            bet = FourDBet(
                agent_id=None,
                number=number,
                type=bet_type,
                b=B, s=S, a=A, c=C,
                total=total,
                win_amount=0,
                dates=dates,
                markets=markets
            )
            db.session.add(bet)

        db.session.commit()
        flash("✅ 成功提交下注记录")
        return redirect('/bet')

    return render_template('bet.html', date_today=date_today, timedelta=timedelta, results=results)

@app.route('/report')
def report():
    return render_template('report.html')

@app.route('/history')
def history():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime.today()

    records = FourDBet.query.filter(
        FourDBet.created_at >= start_date,
        FourDBet.created_at <= end_date + timedelta(days=1)
    ).order_by(FourDBet.created_at.desc()).all()

    # 按日期归组
    grouped = defaultdict(list)
    for r in records:
        date_key = r.created_at.strftime("%Y-%m-%d")
        grouped[date_key].append(r)

    return render_template("history.html", grouped=grouped, start_date=start_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"))

@app.route('/admin/agents', methods=['GET', 'POST'])
def manage_agents():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        commission = request.form.get('commission')

        if not username or not password or not commission:
            flash("❗ 请填写完整信息")
        else:
            existing = Agent4D.query.filter_by(username=username).first()
            if existing:
                flash("❌ 用户名已存在")
            else:
                agent = Agent4D(
                    username=username,
                    password=password,
                    commission=commission
                )
                db.session.add(agent)
                db.session.commit()
                flash(f"✅ 成功创建代理 {username}")

        return redirect('/admin/agents')

    agents = Agent4D.query.all()
    return render_template('manage_agents.html', agents=agents)

@app.route('/admin/agents/<int:agent_id>/update_password', methods=['POST'])
def update_agent_password(agent_id):
    new_pw = request.form.get('new_password')
    agent = Agent4D.query.get(agent_id)
    if agent and new_pw:
        agent.password = new_pw
        db.session.commit()
        flash(f"✅ 已更新 {agent.username} 密码")
    else:
        flash("❌ 更新失败")
    return redirect('/admin/agents')


@app.route('/admin/agents/<int:agent_id>/delete', methods=['POST'])
def delete_agent(agent_id):
    agent = Agent4D.query.get(agent_id)
    if agent:
        db.session.delete(agent)
        db.session.commit()
        flash(f"✅ 已删除代理 {agent.username}")
    else:
        flash("❌ 删除失败")
    return redirect('/admin/agents')

if __name__ == '__main__':
    app.run(debug=True)
