from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
db = SQLAlchemy()

class FourDBet(db.Model):
    __tablename__ = '4DBet'

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.String, nullable=False)
    number = db.Column(db.String(4), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    b = db.Column(db.Numeric(10, 2), default=0)
    s = db.Column(db.Numeric(10, 2), default=0)
    a = db.Column(db.Numeric(10, 2), default=0)
    c = db.Column(db.Numeric(10, 2), default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    win_amount = db.Column(db.Numeric(10, 2), default=0)
    dates = db.Column(db.ARRAY(db.Text), nullable=False)
    markets = db.Column(db.ARRAY(db.Text), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    status = db.Column(db.String(10), default='active')  # 'active' / 'locked'

class Agent4D(db.Model):
    __tablename__ = 'agent_4d'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    commission_group = db.Column(db.String(1), default='A')  # 'A' 或 'B'

class DrawResult4D(db.Model):
    __tablename__ = 'draw_result4d'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    market = db.Column(db.String(1), nullable=False)
    first = db.Column(db.String(4))
    second = db.Column(db.String(4))
    third = db.Column(db.String(4))
    special = db.Column(db.String(100))
    consolation = db.Column(db.String(100))

class WinningRecord4D(db.Model):
    __tablename__ = 'winning_record_4d'

    id = db.Column(db.Integer, primary_key=True)
    bet_id = db.Column(db.Integer, db.ForeignKey('4DBet.id', ondelete='CASCADE'), nullable=False)
    agent_id = db.Column(db.String(50), nullable=False)
    number = db.Column(db.String(4), nullable=False)
    market = db.Column(db.String(10), nullable=False)
    draw_date = db.Column(db.Date, nullable=False)
    prize_type = db.Column(db.String(20), nullable=False)  # 1st / 2nd / 3rd / special / consolation
    bet_mode = db.Column(db.String(10), nullable=False)    # 正字 / Box / IBox
    bet_type = db.Column(db.String(10), nullable=False)    # A / B / C / S
    amount = db.Column(db.Numeric(10, 2), nullable=False)  # 对应下注金额
    win_amount = db.Column(db.Numeric(10, 2), nullable=False)  # 对应中奖金额
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    ip_address = db.Column(db.String(50))
    attempt_count = db.Column(db.Integer, default=0)
    last_attempt = db.Column(db.DateTime, default=datetime.utcnow)
    locked_until = db.Column(db.DateTime, nullable=True)
