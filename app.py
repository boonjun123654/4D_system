from flask import Flask, render_template, request, redirect, flash, session,jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from odds_config import odds
from datetime import datetime, timedelta,time
from utils import calculate_payout
from sqlalchemy import func,any_
from models import db, FourDBet, Agent4D,DrawResult4D  
from collections import defaultdict
from itertools import permutations
from functools import wraps
from decimal import Decimal
import pytesseract
from pytz import timezone
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

def lock_today_bets():
    now = datetime.now(timezone('Asia/Kuala_Lumpur'))
    today_str = now.strftime("%d/%m")
    print(f"[DEBUG] 今天日期: {today_str}")
    bets = FourDBet.query.filter(FourDBet.dates.any(today_str), FourDBet.status == 'active').all()
    for bet in bets:
        bet.status = 'locked'
    db.session.commit()
    print(f"[{now}] ✅ 锁注完成 {len(bets)} 条")

scheduler = BackgroundScheduler(timezone='Asia/Kuala_Lumpur')
scheduler.add_job(lock_today_bets,CronTrigger(hour=19, minute=0))
scheduler.start()

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

@app.route('/admin/lock_bets', methods=['POST'])
@login_required
def admin_lock_bets():
    if session.get('role') != 'admin':
        return "❌ 无权限"
    with app.app_context():
        lock_today_bets()
    return redirect('/admin/agents?locked=1')

@app.route('/bet', methods=['GET', 'POST'])
@login_required
def bet():
    malaysia = timezone('Asia/Kuala_Lumpur')
    now = datetime.now(malaysia)
    date_today = now.date()
    results = []

    if session.get('role') == 'admin':
        agents = Agent4D.query.all()
    else:
        agents = []

    if request.method == 'POST':
        now = datetime.now(timezone('Asia/Kuala_Lumpur'))
        today_str = now.strftime('%d/%m')
        selected_dates = set()

        for i in range(1, 13):
            for d in range(7):
                if request.form.get(f'date{i}_{d}') == 'on':
                    date_str = (date_today + timedelta(days=d)).strftime('%d/%m')
                    selected_dates.add(date_str)

        if today_str in selected_dates and now.time() >= time(19, 0):
            return redirect('/bet?error=cutoff')

        if session.get('role') == 'admin':
            selected_agent_id = request.form.get('agent_id')
            agent = Agent4D.query.get(int(selected_agent_id)) if selected_agent_id else None
        else:
            agent = Agent4D.query.filter_by(username=session['username']).first()

        agent_id = agent.username if agent else None

        def get_box_permutations(n):
            from itertools import permutations
            return list(set([''.join(p) for p in permutations(n)]))

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

            box_permutations = get_box_permutations(number) if bet_type == 'Box' else [number]
            # 检查用排序（用于过滤器匹配），但保存原始号码
            normalized_number = ''.join(sorted(number)) if bet_type in ['Box', 'IBox'] else number
            save_number = number  # 不管什么类型都保存原始号码

            for market in markets:
                for date_str in dates:
                        all_perms = get_box_permutations(number)  # 全部排列
                        existing_bets = db.session.query(FourDBet).filter(
                            FourDBet.number.in_(all_perms),
                            FourDBet.markets.any(market),
                            FourDBet.dates.any(date_str),
                            FourDBet.status == 'active'
                        ).all()

                        existing_total = sum(float(eb.win_amount) for eb in existing_bets)

                        if existing_total + win_amount > 10000:
                            return redirect(f"/bet?error=limit&number={number}&market={market}&date={date_str}")

            factor = get_comb_count(number) if bet_type == 'Box' else 1
            total = (B + S + A + C) * factor * len(dates) * len(markets)

            bet = FourDBet(
                agent_id=agent_id,
                number=save_number,
                type=bet_type,
                b=B, s=S, a=A, c=C,
                total=total,
                win_amount=win_amount,
                dates=dates,
                markets=markets,
                status='active'
            )
            db.session.add(bet)

        db.session.commit()
        return redirect('/bet')

    return render_template('bet.html', date_today=date_today, timedelta=timedelta, results=results, agents=agents)

@app.route('/winning')
def winning_view():
    if 'username' not in session:
        return redirect('/login')

    role = session.get('role')
    username = session.get('username')
    selected_date = request.args.get('date')  # e.g., '2025-07-22'

    # 默认值：results = None 表示页面初次打开还没选择日期
    results = None

    if selected_date:
        # 加载开奖结果
        all_results = DrawResult4D.query.filter_by(date=selected_date).all()
        result_map = defaultdict(dict)
        for r in all_results:
            result_map[r.date.strftime("%Y-%m-%d")][r.market] = {
                "1st": r.first,
                "2nd": r.second,
                "3rd": r.third,
                "special": r.special.split(',') if r.special else [],
                "consolation": r.consolation.split(',') if r.consolation else []
            }

        # 查询锁定下注
        if role == 'admin':
            bets = FourDBet.query.filter(
                FourDBet.status == 'locked',
                FourDBet.dates.any(datetime.strptime(selected_date, "%Y-%m-%d").strftime("%d/%m"))
            ).all()
        else:
            bets = FourDBet.query.filter(
                FourDBet.status == 'locked',
                FourDBet.agent_id == username,
                FourDBet.dates.any(datetime.strptime(selected_date, "%Y-%m-%d").strftime("%d/%m"))
            ).all()

        results = []
        for bet in bets:
            win_total = 0
            number = bet.number
            type_ = bet.type
            combo_numbers = get_box_combinations(number) if type_ in ['Box', 'IBox'] else [number]
            date_key = selected_date  # already YYYY-MM-DD

            for market in bet.markets:
                market_result = result_map.get(date_key, {}).get(market)
                if not market_result:
                    continue

                for combo in combo_numbers:
                    for prize_name in ['1st', '2nd', '3rd']:
                        if combo == market_result[prize_name]:
                            win_total += get_odds(market, prize_name, bet, type_)
                    for prize_name in ['special', 'consolation']:
                        if combo in market_result[prize_name]:
                            win_total += get_odds(market, prize_name, bet, type_)

            if win_total > 0:
                results.append({
                    "agent_id": bet.agent_id,
                    "number": number,
                    "type": type_,
                    "markets": ','.join(bet.markets),
                    "dates": ','.join(bet.dates),
                    "b": float(bet.b),
                    "s": float(bet.s),
                    "a": float(bet.a),
                    "c": float(bet.c),
                    "win_amount": round(win_total, 2)
                })

    return render_template("winning.html", results=results, selected_date=selected_date)

def get_box_combinations(number):
    if len(number) != 4 or not number.isdigit():
        return []
    return sorted(set([''.join(p) for p in permutations(number)]))

def get_odds(market, prize_name, bet, type_):
    try:
        market_odds = odds[market]
        total = 0
        if bet.b:
            total += float(bet.b) * market_odds["B"].get(prize_name, 0)
        if bet.s:
            total += float(bet.s) * market_odds["S"].get(prize_name, 0)
        if bet.a:
            total += float(bet.a) * market_odds["A"].get(prize_name, 0)
        if bet.c:
            total += float(bet.c) * market_odds["C"].get(prize_name, 0)
        if type_ == "IBox":
            return total / len(get_box_combinations(bet.number))
        return total
    except:
        return 0

@app.route('/report')
@login_required
def report():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime.today()

    # 所有下注记录（不使用 created_at 过滤）
    all_bets = FourDBet.query.all()

    # 所有代理
    agents = Agent4D.query.all()
    agent_map = {a.username: a for a in agents}

    # Ground A/B 佣金率
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

    for r in all_bets:
        # ✅ 过滤目标开奖日是否在范围内
        match = False
        for d in r.dates:
            try:
                d_date = datetime.strptime(d, "%d/%m").replace(year=start_date.year)
                if start_date <= d_date <= end_date:
                    match = True
                    break
            except:
                continue
        if not match:
            continue

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
    now = datetime.now(timezone('Asia/Kuala_Lumpur'))
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    selected_agent = request.args.get('agent_id')

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime.today()

    query = FourDBet.query

    # 筛选代理
    if session.get('role') == 'agent':
        query = query.filter_by(agent_id=session['username'])
    elif selected_agent:
        query = query.filter_by(agent_id=selected_agent)

    all_records = query.order_by(FourDBet.created_at.desc()).all()

    grouped = defaultdict(list)
    for r in all_records:
        for d in r.dates:
            try:
                d_date = datetime.strptime(d, "%d/%m")  # 解析成 datetime 对象（只含日月）
                d_date = d_date.replace(year=start_date.year)  # 假设是当前年份
                if start_date <= d_date <= end_date:
                    grouped[d].append(r)
            except:
                continue  # 防止格式不正确时报错

    # ✅ 日期从新到旧排序
    grouped = dict(sorted(grouped.items(), key=lambda x: datetime.strptime(x[0], "%d/%m"), reverse=True))

    agents = Agent4D.query.all() if session.get('role') == 'admin' else []

    return render_template(
        "history.html",
        grouped=grouped,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        selected_agent=selected_agent,
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
            return redirect('/admin/agents?error=missing')
        else:
            existing = Agent4D.query.filter_by(username=username).first()
            if existing:
                return redirect('/admin/agents?error=exists')
            else:
                agent = Agent4D(
                    username=username,
                    password=password,
                    commission_group=commission_group  # 修改点：保存为 A/B
                )
                db.session.add(agent)
                db.session.commit()
                return redirect(f'/admin/agents?success={username}')

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
        return redirect(f"/admin/agents?pw_updated={agent.username}")
    else:
        return redirect("/admin/agents?pw_error=1")

@app.route('/admin/agents/<int:agent_id>/delete', methods=['POST'])
@login_required
def delete_agent(agent_id):
    if session.get('role') != 'admin':
        return redirect('/')
    agent = Agent4D.query.get(agent_id)
    if agent:
        username = agent.username
        db.session.delete(agent)
        db.session.commit()
        return redirect(f'/admin/agents?deleted={username}')
    else:
        return redirect('/admin/agents?error=delete')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        admin_user = os.getenv('ADMIN_USER')
        admin_pass = os.getenv('ADMIN_PASS')

        if username == admin_user and password == admin_pass:
            session['username'] = admin_user
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

            if date and (first or second or third or special or consolation):
                result = DrawResult4D(
                    date=date,
                    market=market,
                    first=first or None,
                    second=second or None,
                    third=third or None,
                    special=special or None,
                    consolation=consolation or None
                )
                db.session.add(result)
        db.session.commit()
        flash("✅ 成功上传开奖成绩")
        return redirect('/admin/draw_input')

    return render_template('admin_draw_input.html')

@app.route('/delete_bet/<int:bet_id>', methods=['POST'])
@login_required
def delete_bet(bet_id):
    bet = FourDBet.query.get_or_404(bet_id)

    # ✅ 判断是否本人或管理员
    if session.get('role') != 'admin' and bet.agent_id != session.get('username'):
        return redirect('/history?error=unauthorized')

    # ✅ 判断是否锁注
    if bet.status == 'locked':
        return redirect('/history?error=locked')

    bet.status = 'delete'
    db.session.commit()

    start = request.args.get("start_date") or request.form.get("start_date")
    end = request.args.get("end_date") or request.form.get("end_date")
    agent = request.args.get("agent_id") or request.form.get("agent_id")

    query = f"?deleted=1"
    if start:
        query += f"&start_date={start}"
    if end:
        query += f"&end_date={end}"
    if agent:
        query += f"&agent_id={agent}"

    return redirect(f"/history{query}")

if __name__ == '__main__':
    app.run(debug=True)
