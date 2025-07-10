from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()

class FourDBet(db.Model):
    __tablename__ = '4DBet'

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer)  # 可留空，后续可绑定
    number = db.Column(db.String(4), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    B = db.Column(db.Numeric(10, 2), default=0)
    S = db.Column(db.Numeric(10, 2), default=0)
    A = db.Column(db.Numeric(10, 2), default=0)
    C = db.Column(db.Numeric(10, 2), default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    win_amount = db.Column(db.Numeric(10, 2), default=0)
    dates = db.Column(db.ARRAY(db.Text), nullable=False)
    markets = db.Column(db.ARRAY(db.Text), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
