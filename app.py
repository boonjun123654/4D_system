from flask import Flask, render_template, request, redirect, flash, session,jsonify, send_file
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from odds_config import odds
from datetime import datetime, timedelta,time
from utils import calculate_payout
from sqlalchemy import func,any_,text,or_
from models import db, FourDBet, Agent4D,DrawResult4D,WinningRecord4D,LoginAttempt,Orders4D
from collections import defaultdict
from itertools import permutations
from werkzeug.security import generate_password_hash,check_password_hash
from functools import wraps
from decimal import Decimal
import pytesseract
from pytz import timezone as pytz_tz
from PIL import Image
from werkzeug.utils import secure_filename
from captcha.image import ImageCaptcha
from random import random
from flask_talisman import Talisman
import string
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = True        # 只通过 HTTPS 发送 Cookie
app.config['SESSION_COOKIE_HTTPONLY'] = True      # JavaScript 无法访问 Cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'     # 防止 CSRF 的辅助策略
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

login_attempts = {}

csp = {
    'default-src': "'self'",
    'script-src': [
        "'self'",
        "'unsafe-inline'",      # 你模板里有内联 <script>
        "'wasm-unsafe-eval'",   # WebAssembly 需要
    ],
    'worker-src': ["'self'", "blob:"],  # Tesseract 用 WebWorker
    'connect-src': ["'self'", "blob:", "data:"],  # 语言包/wasm 都走本域
    'img-src': ["'self'", "data:", "blob:"],
    'style-src': ["'self'", "'unsafe-inline'"],
    'frame-ancestors': "'none'",
}

Talisman(app,
    content_security_policy=csp,
    force_https=True,
    strict_transport_security=True,
    strict_transport_security_preload=True,
    strict_transport_security_include_subdomains=True,
    frame_options='DENY'
)

MY_TZ = pytz_tz('Asia/Kuala_Lumpur')

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 10

db.init_app(app)
with app.app_context():
    db.create_all()

def lock_today_bets():
    now = datetime.now(MY_TZ)
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

@app.before_request
def enforce_https_in_render():
    if app.debug or os.environ.get("FLASK_ENV") == "development":
        return  # 本地不跳
    if request.headers.get('X-Forwarded-Proto', 'http') != 'https':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

@app.route("/")
@login_required
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

@app.route('/captcha')
def generate_captcha():
    import random, string
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    session['captcha_code'] = code  # 存入 session

    image = ImageCaptcha()
    data = image.generate(code)
    return send_file(data, mimetype='image/png')

@app.route('/bet', methods=['GET', 'POST'])
@login_required
def bet():
    malaysia = pytz_tz('Asia/Kuala_Lumpur')
    now = datetime.now(malaysia)
    date_today = now.date()
    results = []

    if session.get('role') == 'admin':
        agents = Agent4D.query.all()
    else:
        agents = []

    if request.method == 'POST':
        now = datetime.now(pytz_tz('Asia/Kuala_Lumpur'))
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
        order_code = generate_order_code_and_create_order(agent_id)

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
                    if bet_type == '正字':
                        # 检查数据库中是否有正字类型的相同号码
                        existing_bets = db.session.query(FourDBet).filter(
                            FourDBet.number == save_number,  # 只检查完全相同的号码
                            FourDBet.type == '正字',  # 只查正字类型
                            FourDBet.markets.any(market),
                            FourDBet.dates.any(date_str),
                            FourDBet.status == 'active'
                        ).all()

                        # 如果已有正字类型的相同号码，并且金额超过限制，则拦截
                        if existing_bets:
                            existing_total = sum(float(eb.win_amount) for eb in existing_bets)
                            if existing_total + win_amount > 10000:
                                return redirect(f"/bet?error=limit&number={number}&market={market}&date={date_str}")

                        # 检查数据库里是否有IBox/Box类型的号码
                        all_perms = get_box_permutations(number)  # 获取所有排列组合
                        existing_bets = db.session.query(FourDBet).filter(
                            FourDBet.number.in_(all_perms),  # 检查所有排列组合
                            FourDBet.type.in_(['Box', 'IBox']),  # 查找IBox/Box类型
                            FourDBet.markets.any(market),
                            FourDBet.dates.any(date_str),
                            FourDBet.status == 'active'
                        ).all()

                        # 如果数据库中已有IBox/Box类型相同的号码组合，且金额超过限制，则拦截
                        if existing_bets:
                            existing_total = sum(float(eb.win_amount) for eb in existing_bets)
                            if existing_total + win_amount > 10000:
                                return redirect(f"/bet?error=limit&number={number}&market={market}&date={date_str}")
        
                    # 对于IBox或Box类型下注，继续检查所有排列组合
                    else:
                        all_perms = get_box_permutations(number)  # 获取所有排列组合
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
                status='active',
                order_code=order_code
            )
            db.session.add(bet)

        db.session.commit()
        return redirect('/bet')

    return render_template('bet.html', date_today=date_today, timedelta=timedelta, results=results, agents=agents)

@app.route('/admin/process_winning', methods=['POST'])
@login_required
def process_winning():
    if session.get('role') != 'admin':
        return redirect('/')

    date_str = request.form.get('date')  # e.g., '2025-07-22'
    if not date_str:
        flash("❌ 缺少日期参数")
        return redirect('/admin/agents')

    selected_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    target_date = selected_dt.strftime("%d/%m")

    # 删除旧记录，避免重复
    WinningRecord4D.query.filter_by(draw_date=selected_dt).delete()

    all_results = (
        DrawResult4D.query
        .filter(func.date(DrawResult4D.date) == selected_dt)  # 关键
        .all()
    )
    result_map = defaultdict(dict)
    for r in all_results:
        key = r.date.strftime("%Y-%m-%d")
        result_map[key][r.market] = {
            "1st": r.first,
            "2nd": r.second,
            "3rd": r.third,
            "special": r.special.split(',') if r.special else [],
            "consolation": r.consolation.split(',') if r.consolation else []
        }

    bets = FourDBet.query.filter(
        FourDBet.status == 'locked',
        FourDBet.dates.any(target_date)
    ).all()

    for bet in bets:
        number = bet.number
        type_ = bet.type
        combo_numbers = get_box_combinations(number) if type_ in ['Box', 'IBox'] else [number]
        date_key = date_str

        for market in bet.markets:
            market_result = result_map.get(date_key, {}).get(market)
            if not market_result:
                continue

            for combo in combo_numbers:
                # A 类型
                if float(bet.a) > 0 and market_result["1st"][-3:] == combo[-3:]:
                    win_amt = float(bet.a) * odds[market]["A"].get("1st", 0)
                    db.session.add(WinningRecord4D(
                        bet_id=bet.id,
                        agent_id=bet.agent_id,
                        number=number,
                        market=market,
                        draw_date=selected_dt,
                        prize_type="1st",
                        bet_mode=type_,
                        bet_type="A",
                        amount=bet.a,
                        win_amount=win_amt if type_ != 'IBox' else win_amt / len(combo_numbers)
                    ))

                # C 类型
                if float(bet.c) > 0:
                    for prize in ["1st", "2nd", "3rd"]:
                        if market_result[prize][-3:] == combo[-3:]:
                            win_amt = float(bet.c) * odds[market]["C"].get(prize, 0)
                            db.session.add(WinningRecord4D(
                                bet_id=bet.id,
                                agent_id=bet.agent_id,
                                number=number,
                                market=market,
                                draw_date=selected_dt,
                                prize_type=prize,
                                bet_mode=type_,
                                bet_type="C",
                                amount=bet.c,
                                win_amount=win_amt if type_ != 'IBox' else win_amt / len(combo_numbers)
                            ))

                # S 类型
                if float(bet.s) > 0:
                    for prize in ["1st", "2nd", "3rd"]:
                        if market_result[prize] == combo:
                            win_amt = float(bet.s) * odds[market]["S"].get(prize, 0)
                            db.session.add(WinningRecord4D(
                                bet_id=bet.id,
                                agent_id=bet.agent_id,
                                number=number,
                                market=market,
                                draw_date=selected_dt,
                                prize_type=prize,
                                bet_mode=type_,
                                bet_type="S",
                                amount=bet.s,
                                win_amount=win_amt if type_ != 'IBox' else win_amt / len(combo_numbers)
                            ))

                # B 类型（含特慰奖）
                if float(bet.b) > 0:
                    for prize in ["1st", "2nd", "3rd"]:
                        if market_result[prize] == combo:
                            win_amt = float(bet.b) * odds[market]["B"].get(prize, 0)
                            db.session.add(WinningRecord4D(
                                bet_id=bet.id,
                                agent_id=bet.agent_id,
                                number=number,
                                market=market,
                                draw_date=selected_dt,
                                prize_type=prize,
                                bet_mode=type_,
                                bet_type="B",
                                amount=bet.b,
                                win_amount=win_amt if type_ != 'IBox' else win_amt / len(combo_numbers)
                            ))

                    for prize in market_result.get("special", []):
                        if prize == combo:
                            win_amt = float(bet.b) * odds[market]["B"].get("special", 0)
                            db.session.add(WinningRecord4D(
                                bet_id=bet.id,
                                agent_id=bet.agent_id,
                                number=number,
                                market=market,
                                draw_date=selected_dt,
                                prize_type="special",
                                bet_mode=type_,
                                bet_type="B",
                                amount=bet.b,
                                win_amount=win_amt if type_ != 'IBox' else win_amt / len(combo_numbers)
                            ))

                    for prize in market_result.get("consolation", []):
                        if prize == combo:
                            win_amt = float(bet.b) * odds[market]["B"].get("consolation", 0)
                            db.session.add(WinningRecord4D(
                                bet_id=bet.id,
                                agent_id=bet.agent_id,
                                number=number,
                                market=market,
                                draw_date=selected_dt,
                                prize_type="consolation",
                                bet_mode=type_,
                                bet_type="B",
                                amount=bet.b,
                                win_amount=win_amt if type_ != 'IBox' else win_amt / len(combo_numbers)
                            ))

    db.session.commit()
    flash(f"✅ 成功处理中奖记录 {date_str}")
    return redirect('/admin/agents')

@app.route('/winning')
def winning_view():
    if 'username' not in session:
        return redirect('/login')

    role = session.get('role')
    username = session.get('username')
    selected_date = request.args.get('date')  # e.g., '2025-07-22'

    results = None

    if selected_date:
        selected_dt = datetime.strptime(selected_date, "%Y-%m-%d").date()

        # 查询中奖记录（根据身份过滤）
        if role == 'admin':
            records = WinningRecord4D.query.filter_by(draw_date=selected_dt).all()
        else:
            records = WinningRecord4D.query.filter_by(draw_date=selected_dt, agent_id=username).all()

        # 整理展示数据
        grouped = defaultdict(lambda: {
            "agent_id": "",
            "number": "",
            "type": "",
            "market": "",
            "date": "",
            "b": 0.00,
            "s": 0.00,
            "a": 0.00,
            "c": 0.00,
            "win_amount": 0.00
        })

        for r in records:
            key = (r.bet_id, r.market)  # 防止重复
            g = grouped[key]
            g["agent_id"] = r.agent_id
            g["number"] = r.number
            g["type"] = r.bet_mode
            g["market"] = r.market
            g["date"] = selected_dt.strftime("%d/%m")

            # 累加每种类型下注金额
            if r.bet_type == "A":
                g["a"] += float(r.amount)
            elif r.bet_type == "B":
                g["b"] += float(r.amount)
            elif r.bet_type == "C":
                g["c"] += float(r.amount)
            elif r.bet_type == "S":
                g["s"] += float(r.amount)

            g["win_amount"] += float(r.win_amount)

        # 转为列表传给模板
        results = list(grouped.values())

    return render_template("winning.html", results=results, selected_date=selected_date)

def get_box_combinations(number):
    if len(number) != 4 or not number.isdigit():
        return []
    return sorted(set([''.join(p) for p in permutations(number)]))

def is_number_match(prize_number, bet_number, type_, prize_name):
    if type_ == "A":
        return prize_name == "1st" and prize_number[-3:] == bet_number[-3:]
    elif type_ == "C":
        return prize_name in ["1st", "2nd", "3rd"] and prize_number[-3:] == bet_number[-3:]
    elif type_ == "S":
        return prize_name in ["1st", "2nd", "3rd"] and prize_number == bet_number
    else:  # B, Box, IBox
        return prize_number == bet_number

def get_odds(market, prize_name, bet, type_):
    try:
        market_odds = odds[market]
        total = 0
        if float(bet.b) > 0 and "B" in market_odds:
            total += float(bet.b) * market_odds["B"].get(prize_name, 0)
        if float(bet.s) > 0 and "S" in market_odds:
            total += float(bet.s) * market_odds["S"].get(prize_name, 0)
        if float(bet.a) > 0 and "A" in market_odds:
            total += float(bet.a) * market_odds["A"].get(prize_name, 0)
        if float(bet.c) > 0 and "C" in market_odds:
            total += float(bet.c) * market_odds["C"].get(prize_name, 0)
        if type_ == "IBox":
            return total / len(get_box_combinations(bet.number))
        return total
    except:
        return 0

@app.route('/report')
@login_required
def report():
    role = session.get('role')
    username = session.get('username')

    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date   = datetime.strptime(end_date_str,   "%Y-%m-%d") if end_date_str   else datetime.today()

    # 下注记录：仅 active/locked
    bets_q = FourDBet.query.filter(FourDBet.status.in_(['active', 'locked']))
    if role == 'agent':
        bets_q = bets_q.filter(FourDBet.agent_id == username)
    all_bets = bets_q.all()

    # 代理清单（用于拿佣金组别）；若是代理只取自己
    agents_q = Agent4D.query
    if role == 'agent':
        agents_q = agents_q.filter(Agent4D.username == username)
    agents = agents_q.all()
    agent_map = {a.username: a for a in agents}

    # Ground A/B 佣金率（保持不变）
    ground_commission = {
        "A": {"M": Decimal("0.26"), "P": Decimal("0.26"), "T": Decimal("0.26"), "S": Decimal("0.26"),
              "B": Decimal("0.26"), "W": Decimal("0.26"), "K": Decimal("0.26"),
              "H": Decimal("0.19"), "E": Decimal("0.19")},
        "B": {"M": Decimal("0.22"), "P": Decimal("0.22"), "T": Decimal("0.22"), "S": Decimal("0.22"),
              "H": Decimal("0.22"), "E": Decimal("0.22"), "B": Decimal("0.22"),
              "W": Decimal("0.22"), "K": Decimal("0.22")}
    }

    # 中奖记录（按 draw_date + 身份过滤）
    wins_q = WinningRecord4D.query.filter(
        WinningRecord4D.draw_date >= start_date.date(),
        WinningRecord4D.draw_date <= end_date.date()
    )
    if role == 'agent':
        wins_q = wins_q.filter(WinningRecord4D.agent_id == username)
    all_wins = wins_q.all()

    # 每代理中奖金额
    win_amount_map = defaultdict(lambda: Decimal("0.00"))
    for w in all_wins:
        win_amount_map[w.agent_id] += Decimal(str(w.win_amount or "0.00"))

    report_data = defaultdict(lambda: {
        "username": "",
        "sales": Decimal("0.00"),
        "commission": Decimal("0.00"),
        "win_amount": Decimal("0.00"),
        "net": Decimal("0.00")
    })

    for r in all_bets:
        # 过滤下注所涉开奖日是否在范围内（r.dates 为 dd/mm，映射到当年）
        match = False
        for d in (r.dates or []):
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
            # 当 admin 查看全部时，agent_map 里可能缺某些代理；补查一次避免丢数据
            agent = Agent4D.query.filter_by(username=r.agent_id).first()
            if not agent:
                continue

        group = agent.commission_group or 'A'
        per_market_total = r.total / Decimal(len(r.markets) or 1)

        commission_total = Decimal("0.00")
        for m in (r.markets or []):
            rate = ground_commission.get(group, {}).get(m, Decimal("0.00"))
            commission_total += per_market_total * rate

        report_data[r.agent_id]["username"] = r.agent_id
        report_data[r.agent_id]["sales"] += r.total
        report_data[r.agent_id]["commission"] += commission_total
        report_data[r.agent_id]["win_amount"] = win_amount_map.get(r.agent_id, Decimal("0.00"))
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
    tz = pytz_tz('Asia/Kuala_Lumpur')
    now = datetime.now(tz)
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    selected_agent = request.args.get('agent_id')

    # 日期范围（默认今天）
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.today()
    end_date   = datetime.strptime(end_date_str,   "%Y-%m-%d") if end_date_str   else datetime.today()

    # === 关键点：构造本次查询要匹配的 dd/mm 列表（用 bet.dates 来筛选，而不是 order_date）
    ddmm_list = []
    cur = start_date
    while cur <= end_date:
        ddmm_list.append(cur.strftime("%d/%m"))
        cur += timedelta(days=1)

    # ==== 1) 先把“符合日期范围”的明细行拉出来（用 dates.any(...) 做 OR 过滤），并按权限筛选 ====
    rows_q = FourDBet.query.filter(FourDBet.order_code.isnot(None))
    if ddmm_list:
        rows_q = rows_q.filter(or_(*[FourDBet.dates.any(d) for d in ddmm_list]))

    # 权限/代理筛选（明细）
    if session.get('role') == 'agent':
        rows_q = rows_q.filter(FourDBet.agent_id == session['username'])
    elif selected_agent:
        rows_q = rows_q.filter(FourDBet.agent_id == selected_agent)

    rows = rows_q.order_by(FourDBet.created_at.asc()).all()

    # 取这些明细对应的订单头
    order_codes = sorted({r.order_code for r in rows if r.order_code})
    orders_q = Orders4D.query.filter(Orders4D.order_code.in_(order_codes))
    # 订单头也做权限筛选（防止越权）
    if session.get('role') == 'agent':
        orders_q = orders_q.filter(Orders4D.agent_id == session['username'])
    elif selected_agent:
        orders_q = orders_q.filter(Orders4D.agent_id == selected_agent)
    orders = {o.order_code: o for o in orders_q.all()}

    # 先按 order_code 汇总行
    from collections import defaultdict
    from decimal import Decimal
    lines_by_order = defaultdict(list)
    for r in rows:
        lines_by_order[r.order_code].append(r)

    # === 2) 组装订单卡片，并“按 bet 的 dates”分发到每一个 dd/mm 分组 ===
    orders_by_date = defaultdict(list)
    MARKET_ORDER = "MPTSHEBKW"

    for oc, lines in lines_by_order.items():
        o = orders.get(oc)
        # 整单合计与状态
        gt_total = sum(Decimal(str(r.total or "0")) for r in lines) if lines else Decimal("0")
        win_total = sum(Decimal(str(r.win_amount or "0")) for r in lines) if lines else Decimal("0")
        statuses = {r.status for r in lines}
        if 'locked' in statuses:
            order_status = 'locked'
        elif statuses and all(s == 'delete' for s in statuses):
            order_status = 'delete'
        else:
            order_status = 'active'

        # 汇总 dates / markets
        dates_set = sorted({d for r in lines for d in (r.dates or [])})
        markets_set = {m for r in lines for m in (r.markets or [])}
        markets_str = "".join(ch for ch in MARKET_ORDER if ch in markets_set)

        order_card_base = {
            "order_code": oc,
            "order_date": o.order_date.strftime("%d/%m") if o and o.order_date else "",
            "agent_id": (o.agent_id if o else (lines[0].agent_id if lines else "")),
            "status": order_status,
            "gt_total": float(gt_total),
            "win_total": float(win_total),
            "dates": dates_set,
            "markets": markets_str,
            "lines": lines,  # 模板里循环显示号码行
        }

        # 按“下注日期”分发到对应的 dd/mm 分组，并且只放入当前选择范围内的日期
        for d in dates_set:
            try:
                d_date = datetime.strptime(d, "%d/%m").replace(year=start_date.year)
            except ValueError:
                continue
            if not (start_date <= d_date <= end_date):
                continue

            # 放一个浅拷贝，避免同一对象被多天共享导致模板修改互相影响
            od = dict(order_card_base)
            orders_by_date[d].append(od)

    # 分组日期倒序
    orders_by_date = dict(sorted(
        orders_by_date.items(),
        key=lambda x: datetime.strptime(x[0], "%d/%m"),
        reverse=True
    ))

    # ==== 3) 兼容旧数据：没有 order_code 的历史行 ====
    legacy_grouped = defaultdict(list)
    legacy_q = FourDBet.query.filter(FourDBet.order_code.is_(None))
    if session.get('role') == 'agent':
        legacy_q = legacy_q.filter(FourDBet.agent_id == session['username'])
    elif selected_agent:
        legacy_q = legacy_q.filter(FourDBet.agent_id == selected_agent)

    if ddmm_list:
        legacy_q = legacy_q.filter(or_(*[FourDBet.dates.any(d) for d in ddmm_list]))

    legacy_rows = legacy_q.order_by(FourDBet.created_at.desc()).all()
    for r in legacy_rows:
        for d in (r.dates or []):
            try:
                d_date = datetime.strptime(d, "%d/%m").replace(year=start_date.year)
                if start_date <= d_date <= end_date:
                    legacy_grouped[d].append(r)
            except:
                continue

    legacy_grouped = dict(sorted(
        legacy_grouped.items(),
        key=lambda x: datetime.strptime(x[0], "%d/%m"),
        reverse=True
    ))

    agents = Agent4D.query.all() if session.get('role') == 'admin' else []

    return render_template(
        "history.html",
        orders_by_date=orders_by_date,
        legacy_grouped=legacy_grouped,
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
                hashed_password = generate_password_hash(password)
                agent = Agent4D(
                    username=username,
                    password=hashed_password,
                    commission_group=commission_group  # 修改点：保存为 A/B
                )
                db.session.add(agent)
                db.session.commit()
                return redirect(f'/admin/agents?success={username}')

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
        agent.password = generate_password_hash(new_pw)
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
    ip = request.remote_addr
    now = datetime.utcnow()
    MAX_ATTEMPTS = 5
    CAPTCHA_THRESHOLD = 3  # 第 3 次失败后要求验证码
    LOCKOUT_MINUTES = 10

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        captcha_input = request.form.get('captcha')

        # 从数据库中查找登录尝试记录
        attempt = LoginAttempt.query.filter_by(username=username).first()

        # 如果已经封锁
        if attempt and attempt.locked_until and now < attempt.locked_until:
            flash(f"⚠️ 登录次数过多，请稍后再试（{LOCKOUT_MINUTES}分钟）")
            return render_template('login.html', show_captcha=True, random=random)

        # 若失败次数 ≥ 3，要求验证验证码
        if attempt and attempt.attempt_count >= CAPTCHA_THRESHOLD:
            if not captcha_input or captcha_input.lower() != session.get("captcha_code", "").lower():
                flash("❌ 验证码错误")
                return render_template('login.html', show_captcha=True, random=random)

        # 登录成功处理
        admin_user = os.getenv('ADMIN_USER')
        admin_pass = os.getenv('ADMIN_PASS')

        if username == admin_user and password == admin_pass:
            session['username'] = admin_user
            session['role'] = 'admin'
            if attempt:
                db.session.delete(attempt)
                db.session.commit()
            session.pop("captcha_code", None)
            return redirect('/')

        agent = Agent4D.query.filter_by(username=username).first()
        if agent and check_password_hash(agent.password, password):
            session['username'] = agent.username
            session['role'] = 'agent'
            if attempt:
                db.session.delete(attempt)
                db.session.commit()
            session.pop("captcha_code", None)
            return redirect('/')

        # 登录失败处理
        if not attempt:
            attempt = LoginAttempt(
                username=username,
                ip_address=ip,
                attempt_count=1,
                last_attempt=now
            )
            db.session.add(attempt)
        else:
            attempt.attempt_count += 1
            attempt.last_attempt = now
            if attempt.attempt_count >= MAX_ATTEMPTS:
                attempt.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)

        db.session.commit()

        # 给出提示信息
        remain = MAX_ATTEMPTS - attempt.attempt_count
        if remain <= 0:
            flash(f"登录失败次数过多，请等待 {LOCKOUT_MINUTES} 分钟")
        else:
            flash(f"登录失败，还有 {remain} 次机会")

        show_captcha = attempt.attempt_count >= CAPTCHA_THRESHOLD
        return render_template('login.html', show_captcha=show_captcha)

    # GET 请求，检查是否需要显示验证码
    attempt = None
    username = request.args.get('username')
    if username:
        attempt = LoginAttempt.query.filter_by(username=username).first()
    show_captcha = attempt and attempt.attempt_count >= CAPTCHA_THRESHOLD
    return render_template('login.html', show_captcha=show_captcha)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/ocr')
@login_required
def ocr_page():
    if session.get('role') != 'admin':
        return redirect('/')
    return render_template('ocr.html')

@app.route('/admin/save_draw', methods=['POST'])
@login_required
def save_draw():
    if session.get('role') != 'admin':
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    date_str = (data.get('date') or '').strip()            # 'YYYY-MM-DD'
    market   = (data.get('market') or 'M').strip().upper()
    first    = (data.get('first') or '').strip()
    second   = (data.get('second') or '').strip()
    third    = (data.get('third') or '').strip()
    special  = (data.get('special') or '')
    consol   = (data.get('consolation') or '')

    # 1) 基础与白名单校验
    MARKET_SET = set(list("MPTSHEBKW"))
    if market not in MARKET_SET:
        return jsonify({"ok": False, "error": "invalid market"}), 400

    is4 = lambda s: s.isdigit() and len(s) == 4
    if not (date_str and is4(first) and is4(second) and is4(third)):
        return jsonify({"ok": False, "error": "invalid 1st/2nd/3rd"}), 400

    # 2) 规范化 & 强校验（全角->半角，去空格，去重，必须10个不重复4位数）
    def canon_list(s: str):
        # 全角逗号/空白 -> 半角逗号；移除空格；拆分
        s = s.replace('，', ',').replace('、', ',').replace('；', ',').replace(';', ',')
        s = s.replace(' ', '')
        items = [x for x in s.split(',') if x]
        # 只保留4位数字，去重并按原序保留前10个
        seen, out = set(), []
        for x in items:
            x = ''.join(ch for ch in x if ch.isdigit())
            if is4(x) and x not in seen:
                seen.add(x); out.append(x)
            if len(out) == 10:
                break
        return out

    sp = canon_list(special)
    co = canon_list(consol)
    if len(sp) != 10 or len(co) != 10:
        return jsonify({"ok": False, "error": "special/consolation need 10 unique 4-digit each"}), 400

    # 不允许将 1/2/3 放进特/慰（避免重复）
    top3 = {first, second, third}
    if any(x in top3 for x in sp) or any(x in top3 for x in co):
        return jsonify({"ok": False, "error": "top prizes must not appear in special/consolation"}), 400

    # 3) 组装 datetime（MY_TZ 本地化）
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        dt_naive = datetime(d.year, d.month, d.day)
        dt_val = MY_TZ.localize(dt_naive)
    except Exception:
        return jsonify({"ok": False, "error": "invalid date"}), 400

    # 4) 幂等写入（推荐在 DB 层有唯一约束: UNIQUE (date, market)）
    try:
        rec = DrawResult4D.query.filter(
            func.date(DrawResult4D.date) == d,
            DrawResult4D.market == market
        ).with_for_update(read=True).first()  # 防并发：需要在事务中

        if rec:
            rec.first = first
            rec.second = second
            rec.third = third
            rec.special = ",".join(sp)
            rec.consolation = ",".join(co)
        else:
            rec = DrawResult4D(
                date=dt_val,
                market=market,
                first=first,
                second=second,
                third=third,
                special=",".join(sp),
                consolation=",".join(co)
            )
            db.session.add(rec)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"db error: {type(e).__name__}"}), 500

    # 5) 返回落库结果
    return jsonify({
        "ok": True,
        "data": {
            "date": date_str,
            "market": market,
            "first": first,
            "second": second,
            "third": third,
            "special": ",".join(sp),
            "consolation": ",".join(co),
        }
    })

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
                try:
                    d = datetime.strptime(date, "%Y-%m-%d").date()
                    dt_naive = datetime(d.year, d.month, d.day)
                    dt_val = MY_TZ.localize(dt_naive)
                except Exception:
                    continue

                # 若已有同日同 market，改为更新（避免重复）
                rec = DrawResult4D.query.filter(
                    func.date(DrawResult4D.date) == d,
                    DrawResult4D.market == market
                ).first()

                if rec:
                    rec.first = first or rec.first
                    rec.second = second or rec.second
                    rec.third = third or rec.third
                    rec.special = special or rec.special
                    rec.consolation = consolation or rec.consolation
                else:
                    rec = DrawResult4D(
                        date=dt_val,
                        market=market,
                        first=first or None,
                        second=second or None,
                        third=third or None,
                        special=special or None,
                        consolation=consolation or None
                    )
                    db.session.add(rec)

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

@app.route('/delete_order/<path:order_code>', methods=['POST'])
@login_required
def delete_order(order_code):
    # 仅管理员或该订单所属代理可以删除（如果你的订单跨多个代理，这里按需放宽）
    role = session.get('role')
    username = session.get('username')

    # 找到该订单下的所有下注
    bets = FourDBet.query.filter_by(order_code=order_code).all()
    if not bets:
        return redirect('/history?error=not_found')

    # 权限校验：若非管理员，则只能删除自己的订单（全部下注都必须是本人）
    if role != 'admin':
        if any(b.agent_id != username for b in bets):
            return redirect('/history?error=unauthorized')

    # 锁注校验：若其中任意一条已锁注，则整单不允许删除
    if any(b.status == 'locked' for b in bets):
        return redirect('/history?error=locked')

    # 执行整单删除（标记为 delete）
    for b in bets:
        b.status = 'delete'
    db.session.commit()

    # 保留查询参数
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    agent = request.args.get("agent_id")

    query = "?deleted=1"
    if start:
        query += f"&start_date={start}"
    if end:
        query += f"&end_date={end}"
    if agent:
        query += f"&agent_id={agent}"

    return redirect(f"/history{query}")

def generate_order_code_and_create_order(agent_id: str) -> str:
    """原子获取当日唯一流水号，返回订单号，并写入 orders_4d。"""
    tz = pytz_tz('Asia/Kuala_Lumpur')
    today = datetime.now(tz).date()  # 注意用马来西亚日期做日序号
    # 原子自增 last_seq 并返回
    seq_sql = text("""
        INSERT INTO order_counter_4d (order_date, last_seq)
        VALUES (:d, 1)
        ON CONFLICT (order_date)
        DO UPDATE SET last_seq = order_counter_4d.last_seq + 1
        RETURNING last_seq;
    """)
    seq = db.session.execute(seq_sql, {"d": today}).scalar_one()
    if seq > 9999:
        raise ValueError("今日订单号已达上限（9999）。")

    order_code = today.strftime("%y%m%d") + f"/{seq:04d}"

    db.session.add(Orders4D(
        order_date=today,
        order_seq=seq,
        order_code=order_code,
        agent_id=agent_id
    ))
    # 不在这里 commit，由调用方统一提交（保证整单原子性）
    return order_code

if __name__ == '__main__':
    app.run(debug=True)
