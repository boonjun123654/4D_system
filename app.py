from flask import Flask, render_template, request, redirect, flash, session
from odds_config import odds
from datetime import datetime, timedelta,time
from utils import calculate_payout
from models import db, FourDBet, Agent4D,DrawResult4D  
from collections import defaultdict
from functools import wraps
from decimal import Decimal
import pytesseract
from PIL import Image
from werkzeug.utils import secure_filename
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

@app.route("/")
def index():
    return render_template("index.html", odds=odds)

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
        # ✅ 添加下注时间限制检查
        now = datetime.now()
        today_str = now.strftime('%d/%m')
        selected_dates = set()

        for i in range(1, 13):
            for d in range(7):
                if request.form.get(f'date{i}_{d}') == 'on':
                    date_str = (date_today + timedelta(days=d)).strftime('%d/%m')
                    selected_dates.add(date_str)

        # ✅ 如果有下注当天的日期，并且已过晚上7点，阻止下注
        if today_str in selected_dates and now.time() >= time(19, 0):
            flash("⚠️ 晚上 7 点后无法下注当天的号码")
            return redirect('/bet')

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

    # 获取下注记录
    records = FourDBet.query.filter(
        FourDBet.created_at >= start_date,
        FourDBet.created_at <= end_date + timedelta(days=1)
    ).all()

    # 所有代理
    agents = Agent4D.query.all()
    agent_map = {a.username: a for a in agents}

    # Ground A：MPTSBWK=26%，HE=19%
    # Ground B：MPTSHEBWK=22%
    ground_commission = {
        "A": {
            "M": Decimal("0.26"), "P": Decimal("0.26"), "T": Decimal("0.26"), "S": Decimal("0.26"),
            "B": Decimal("0.26"), "W": Decimal("0.26"), "K": Decimal("0.26"),
            "H": Decimal("0.19"), "E": Decimal("0.19")
        },
        "B": {
            "M": Decimal("0.22"), "P": Decimal("0.22"), "T": Decimal("0.22"), "S": Decimal("0.22"),
            "H": Decimal("0.22"), "E": Decimal("0.22"), "B": Decimal("0.22"),
            "W": Decimal("0.22"), "K": Decimal("0.22")
        }
    }

    report_data = defaultdict(lambda: {
        "username": "",
        "sales": Decimal("0.00"),
        "commission": Decimal("0.00"),
        "win_amount": Decimal("0.00"),
        "net": Decimal("0.00")
    })

    for r in records:
        agent = agent_map.get(r.agent_id)
        if not agent:
            continue

        group = agent.commission_group or 'A'
        per_market_total = r.total / Decimal(len(r.markets) or 1)

        commission_total = Decimal("0.00")
        for m in r.markets:
            rate = ground_commission.get(group, {}).get(m, Decimal("0.00"))
            commission_total += per_market_total * rate

        report_data[r.agent_id]["username"] = r.agent_id
        report_data[r.agent_id]["sales"] += r.total
        report_data[r.agent_id]["commission"] += commission_total
        report_data[r.agent_id]["win_amount"] = Decimal("0.00")  # 固定为 0
        report_data[r.agent_id]["net"] = (
            report_data[r.agent_id]["sales"]
            - report_data[r.agent_id]["commission"]
            - report_data[r.agent_id]["win_amount"]
        )

    return render_template(
        "report.html",
        report_data=report_data.values(),
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )

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
        commission_group = request.form.get('commission_group')  # 修改点：读取 A/B

        if not username or not password or not commission_group:
            flash("❗ 请填写完整信息")
        else:
            existing = Agent4D.query.filter_by(username=username).first()
            if existing:
                flash("❌ 用户名已存在")
            else:
                agent = Agent4D(
                    username=username,
                    password=password,
                    commission_group=commission_group  # 修改点：保存为 A/B
                )
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

@app.route('/admin/ocr', methods=['GET', 'POST'])
def ocr_upload():
    if session.get('role') != 'admin':
        return redirect('/')

    results = ""
    if request.method == 'POST':
        image = request.files.get('image')
        if image:
            filename = secure_filename(image.filename)
            filepath = os.path.join('uploads', filename)
            image.save(filepath)

            # OCR 识别
            text = pytesseract.image_to_string(Image.open(filepath), lang='eng')
            results = text.strip()

    return render_template('admin_ocr_upload.html', results=results)

@app.route('/admin/draw_input', methods=['GET', 'POST'])
def admin_draw_input():
    if session.get('role') != 'admin':
        return redirect('/')

    if request.method == 'POST':
        for market in ['M', 'P', 'T', 'S', 'B', 'K', 'W', 'H', 'E']:
            date = request.form.get(f'date_{market}')
            first = request.form.get(f'first_{market}')
            second = request.form.get(f'second_{market}')
            third = request.form.get(f'third_{market}')
            special = request.form.get(f'special_{market}')
            consolation = request.form.get(f'consolation_{market}')

            if first and second and third:
                result = DrawResult4D(
                    date=date,
                    market=market,
                    first=first,
                    second=second,
                    third=third,
                    special=special,
                    consolation=consolation
                )
                db.session.add(result)
        db.session.commit()
        flash("✅ 成功上传开奖成绩")
        return redirect('/admin/draw_input')

    return render_template('admin_draw_input.html')

if __name__ == '__main__':
    app.run(debug=True)
