from flask import Flask, render_template, request, redirect, flash, session
from odds_config import odds
from datetime import datetime, timedelta
from utils import calculate_payout
from models import db, FourDBet, Agent4D
from collections import defaultdict
from functools import wraps
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key'

db.init_app(app)
with app.app_context():
    db.create_all()

# 登录保护装饰器
def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            return redirect('/login')
        return view_func(*args, **kwargs)
    return wrapper

@app.route('/')
def index():
    if 'username' not in session:
        return redirect('/login')
    if session.get('role') == 'admin':
        return redirect('/report')
    else:
        return redirect('/bet')

@app.route('/bet', methods=['GET', 'POST'])
@login_required
def bet():
    date_today = datetime.today()
    results = []

    if session.get('role') == 'admin':
        agents = Agent4D.query.all()
    else:
        agents = []

    if request.method == 'POST':
        # 决定 agent_id
        if session.get('role') == 'admin':
            selected_agent_id = request.form.get('agent_id')
            agent = Agent4D.query.get(int(selected_agent_id)) if selected_agent_id else None
        else:
            agent = Agent4D.query.filter_by(username=session['username']).first()

        agent_id = agent.username if agent else None

        for i in range(1, 13):
            number = request.form.get(f'number{i}', '').strip()
            if not number or not number.isdigit():
                continue
            number = number.zfill(4)

            bet_type = request.form.get(f'type{i}', '正字')
            win_amount = float(request.form.get(f"win_amount{i}") or 0)
            B = float(request.form.get(f'B{i}', 0) or 0)
            S = float(request.form.get(f'S{i}', 0) or 0)
            A = float(request.form.get(f'A{i}', 0) or 0)
            C = float(request.form.get(f'C{i}', 0) or 0)

            dates = [
                (date_today + timedelta(days=d)).strftime("%d/%m")
                for d in range(7)
                if request.form.get(f'date{i}_{d}') == 'on'
            ]

            markets = [
                m for m in ['M','P','T','S','B','K','W','H','E']
                if request.form.get(f'market{i}_{m}') == 'on'
            ]

            if not dates or not markets:
                continue

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
                agent_id=agent_id,
                number=number,
                type=bet_type,
                b=B, s=S, a=A, c=C,
                total=total,
                win_amount=win_amount,
                dates=dates,
                markets=markets
            )
            db.session.add(bet)

        db.session.commit()
        flash("✅ 成功提交下注记录")
        return redirect('/bet')

    return render_template('bet.html', date_today=date_today, timedelta=timedelta, results=results, agents=agents)

@app.route('/report')
@login_required
def report():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime.today()

    records = FourDBet.query.filter(
        FourDBet.created_at >= start_date,
        FourDBet.created_at <= end_date + timedelta(days=1)
    ).all()

    agent_map = {a.username: a for a in Agent4D.query.all()}
    report_data = defaultdict(lambda: {"sales": 0.0, "commission_rate": 0.0})

    for r in records:
        agent_id = r.agent_id or "未绑定"
        report_data[agent_id]["sales"] += r.total
        if agent_id in agent_map:
            report_data[agent_id]["commission_rate"] = agent_map[agent_id].commission

    for agent_id, data in report_data.items():
        data["username"] = agent_id
        data["commission"] = round(data["sales"] * data["commission_rate"], 2)
        data["win_amount"] = 0
        data["net"] = round(data["win_amount"] - data["commission"], 2)

    return render_template('report.html', report_data=report_data, start_date=start_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"))

@app.route('/history')
@login_required
def history():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    selected_agent = request.args.get('agent_id')

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime.today()

    query = FourDBet.query.filter(
        FourDBet.created_at >= start_date,
        FourDBet.created_at <= end_date + timedelta(days=1)
    )

    # 筛选代理
    if session.get('role') == 'agent':
        query = query.filter_by(agent_id=session['username'])
    elif selected_agent:
        query = query.filter_by(agent_id=selected_agent)

    records = query.order_by(FourDBet.created_at.desc()).all()

    grouped = defaultdict(list)
    for r in records:
        date_key = r.created_at.strftime("%Y-%m-%d")
        grouped[date_key].append(r)

    agents = Agent4D.query.all() if session.get('role') == 'admin' else []

    return render_template(
        "history.html",
        grouped=grouped,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        agents=agents
    )

@app.route('/admin/agents', methods=['GET', 'POST'])
@login_required
def manage_agents():
    if session.get('role') != 'admin':
        return redirect('/')
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
                agent = Agent4D(username=username, password=password, commission=commission)
                db.session.add(agent)
                db.session.commit()
                flash(f"✅ 成功创建代理 {username}")

        return redirect('/admin/agents')

    agents = Agent4D.query.all()
    return render_template('manage_agents.html', agents=agents)

@app.route('/admin/agents/<int:agent_id>/update_password', methods=['POST'])
@login_required
def update_agent_password(agent_id):
    if session.get('role') != 'admin':
        return redirect('/')
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
@login_required
def delete_agent(agent_id):
    if session.get('role') != 'admin':
        return redirect('/')
    agent = Agent4D.query.get(agent_id)
    if agent:
        db.session.delete(agent)
        db.session.commit()
        flash(f"✅ 已删除代理 {agent.username}")
    else:
        flash("❌ 删除失败")
    return redirect('/admin/agents')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username == 'admin' and password == 'admin123':
            session['username'] = 'admin'
            session['role'] = 'admin'
            return redirect('/')
        agent = Agent4D.query.filter_by(username=username, password=password).first()
        if agent:
            session['username'] = agent.username
            session['role'] = 'agent'
            return redirect('/')
        flash("❌ 登录失败，请检查用户名或密码")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

if __name__ == '__main__':
    app.run(debug=True)
