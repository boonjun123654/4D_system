from flask_sqlalchemy import SQLAlchemy
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

class Agent4D(db.Model):
    __tablename__ = 'agent_4d'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    commission = db.Column(db.Text, nullable=False)
