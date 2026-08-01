from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify, session, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import bcrypt
import re
import os
import csv
import io
import requests
import time
import json
import smtplib
import secrets
import string
import qrcode
import base64
import pyotp
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import yfinance as yf

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///summit.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---------- CONFIG ----------
WALLET_ADDRESS = os.environ.get('WALLET_ADDRESS', '')
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
BCON_API_KEY = os.environ.get('BCON_API_KEY', '')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

# ---------- EMAIL CONFIG ----------
MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@summit.app')
FEEDBACK_RECIPIENT = os.environ.get('FEEDBACK_RECIPIENT', 'support@summit.app')

MARKET_STOCKS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META',
    'NFLX', 'AMD', 'INTC', 'IBM', 'ORCL', 'CSCO', 'ADBE',
    'CRM', 'PYPL', 'UBER', 'DIS', 'V', 'MA', 'JPM', 'BAC',
    'WMT', 'HD', 'NKE', 'SBUX', 'COST', 'TMO', 'ABT', 'PFE'
]

# ---------- CACHING ----------
market_cache = {'data': None, 'timestamp': 0}
CACHE_DURATION = 60

def get_market_data():
    now = time.time()
    if market_cache['data'] and (now - market_cache['timestamp']) < CACHE_DURATION:
        return market_cache['data']
    
    data = {'stocks': [], 'crypto': [], 'forex': [], 'indices': []}
    
    try:
        tickers = yf.Tickers(' '.join(MARKET_STOCKS))
        for symbol in MARKET_STOCKS:
            try:
                info = tickers.tickers[symbol].info
                price = info.get('currentPrice', info.get('regularMarketPrice', None))
                data['stocks'].append({
                    'symbol': symbol,
                    'name': info.get('longName', symbol),
                    'price': price
                })
            except:
                data['stocks'].append({'symbol': symbol, 'name': symbol, 'price': None})
    except:
        for symbol in MARKET_STOCKS:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                price = info.get('currentPrice', info.get('regularMarketPrice', None))
                data['stocks'].append({
                    'symbol': symbol,
                    'name': info.get('longName', symbol),
                    'price': price
                })
            except:
                data['stocks'].append({'symbol': symbol, 'name': symbol, 'price': None})
    
    crypto_symbols = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'XRP-USD', 'ADA-USD', 'DOGE-USD']
    for symbol in crypto_symbols:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = hist['Close'].iloc[-1]
                change = ((price - hist['Open'].iloc[0]) / hist['Open'].iloc[0]) * 100 if hist['Open'].iloc[0] != 0 else 0
                data['crypto'].append({
                    'symbol': symbol.replace('-USD', ''),
                    'price': price,
                    'change': change
                })
        except:
            pass
    
    forex_pairs = ['USDZAR=X', 'EURZAR=X', 'GBPZAR=X']
    for pair in forex_pairs:
        try:
            ticker = yf.Ticker(pair)
            hist = ticker.history(period="1d")
            if not hist.empty:
                data['forex'].append({
                    'pair': pair.replace('=X', ''),
                    'price': hist['Close'].iloc[-1]
                })
        except:
            pass
    
    indices_map = {'^GSPC': 'S&P 500', '^DJI': 'Dow Jones', '^IXIC': 'NASDAQ'}
    for symbol, name in indices_map.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                prev = hist['Close'].iloc[-2]
                curr = hist['Close'].iloc[-1]
                change = ((curr - prev) / prev) * 100 if prev != 0 else 0
                data['indices'].append({
                    'name': name,
                    'price': curr,
                    'change': change
                })
        except:
            pass
    
    market_cache['data'] = data
    market_cache['timestamp'] = now
    return data

# ---------- HELPER FUNCTIONS ----------
def convert_currency(amount_zar, target_currency):
    """Convert an amount from ZAR to target currency using live forex rates."""
    if target_currency == 'ZAR' or amount_zar == 0:
        return amount_zar
    
    data = get_market_data()
    for forex in data.get('forex', []):
        if forex['pair'] == f"{target_currency}ZAR":
            rate = forex['price']
            if rate and rate > 0:
                return amount_zar / rate
        if forex['pair'] == f"ZAR{target_currency}":
            rate = forex['price']
            if rate and rate > 0:
                return amount_zar * rate
    return amount_zar

# ---------- DATABASE MODELS ----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_premium = db.Column(db.Boolean, default=False)
    premium_until = db.Column(db.DateTime, nullable=True)
    is_owner = db.Column(db.Boolean, default=False)
    referral_code = db.Column(db.String(20), unique=True, nullable=True)
    referred_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    referral_count = db.Column(db.Integer, default=0)
    referred_by = db.relationship('User', remote_side=[id], backref='referrals')
    avatar_color = db.Column(db.String(7), default='#3b82f6')
    totp_secret = db.Column(db.String(32), nullable=True)
    is_2fa_enabled = db.Column(db.Boolean, default=False)
    is_public = db.Column(db.Boolean, default=False)

class UserPreference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True)
    currency = db.Column(db.String(10), default='ZAR')
    language = db.Column(db.String(10), default='en')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='active')
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    progress = db.Column(db.Integer, default=0)

class Income(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    source = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    is_recurring = db.Column(db.Boolean, default=False)
    frequency = db.Column(db.String(20), default='monthly')

class Crypto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    coin_name = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    value_zar = db.Column(db.Float, nullable=False)

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    symbol = db.Column(db.String(20), nullable=False)
    shares = db.Column(db.Float, nullable=False)
    purchase_price = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_watchlisted = db.Column(db.Boolean, default=False)
    dividend_yield = db.Column(db.Float, nullable=True)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    tx_hash = db.Column(db.String(100), nullable=True)
    invoice_id = db.Column(db.String(100), nullable=True)
    amount = db.Column(db.Float)
    currency = db.Column(db.String(10), default='USDC')
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_at = db.Column(db.DateTime, nullable=True)

class PriceAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    symbol = db.Column(db.String(10), nullable=False)
    target_price = db.Column(db.Float, nullable=False)
    condition = db.Column(db.String(10), nullable=False)
    is_triggered = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='price_alerts')

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    category = db.Column(db.String(50), nullable=True)
    user = db.relationship('User', backref='expenses')
    is_recurring = db.Column(db.Boolean, default=False)
    frequency = db.Column(db.String(20), default='monthly')

class Milestone(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    project = db.relationship('Project', backref='milestones')

class Liability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    interest_rate = db.Column(db.Float, nullable=True)
    date = db.Column(db.Date, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    user = db.relationship('User', backref='liabilities')

class MonthlyReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='reports')

class TelegramUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    chat_id = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='telegram')

class Shoutout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='shoutouts')

# ---------- HELPER FUNCTIONS ----------
def generate_referral_code():
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"SUMMIT-{random_part}"

def generate_totp_secret():
    return pyotp.random_base32()

def update_project_progress(project_id):
    project = Project.query.get(project_id)
    if not project:
        return
    milestones = Milestone.query.filter_by(project_id=project_id).all()
    if milestones:
        completed = sum(1 for m in milestones if m.is_completed)
        project.progress = int((completed / len(milestones)) * 100)
    else:
        project.progress = 0
    db.session.commit()

def check_price_alerts(user_id):
    alerts = PriceAlert.query.filter_by(user_id=user_id, is_triggered=False).all()
    triggered = []
    for alert in alerts:
        try:
            stock = yf.Ticker(alert.symbol)
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = hist['Close'].iloc[-1]
                if (alert.condition == 'above' and current_price >= alert.target_price) or \
                   (alert.condition == 'below' and current_price <= alert.target_price):
                    alert.is_triggered = True
                    db.session.commit()
                    triggered.append(f"{alert.symbol} hit R{alert.target_price}!")
        except:
            pass
    return triggered

def process_recurring_transactions(user_id):
    today = datetime.utcnow().date()
    for income in Income.query.filter_by(user_id=user_id, is_recurring=True).all():
        if income.date.day == today.day:
            new = Income(
                user_id=user_id,
                source=income.source,
                amount=income.amount,
                notes=f"Recurring: {income.source}",
                date=today,
                is_recurring=False
            )
            db.session.add(new)
    
    for expense in Expense.query.filter_by(user_id=user_id, is_recurring=True).all():
        if expense.date.day == today.day:
            new = Expense(
                user_id=user_id,
                description=expense.description,
                amount=expense.amount,
                category=expense.category,
                date=today,
                is_recurring=False
            )
            db.session.add(new)
    
    db.session.commit()

def send_monthly_report(user):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        return
    
    total_income = sum(i.amount for i in Income.query.filter_by(user_id=user.id).all())
    total_expenses = sum(e.amount for e in Expense.query.filter_by(user_id=user.id).all())
    net_savings = total_income - total_expenses
    total_crypto = sum(c.value_zar for c in Crypto.query.filter_by(user_id=user.id).all())
    total_stocks = 0
    for s in Stock.query.filter_by(user_id=user.id).all():
        price = get_stock_price(s.symbol)
        if price:
            total_stocks += price * s.shares
    
    subject = f"📊 Your Summit Report - {datetime.utcnow().strftime('%B %Y')}"
    body = f"""
Hi {user.full_name},

Here's your monthly Summit summary:

📈 Income: R{total_income:.2f}
📉 Expenses: R{total_expenses:.2f}
💰 Net Savings: R{net_savings:.2f}
₿ Crypto Value: R{total_crypto:.2f}
📊 Stock Value: R{total_stocks:.2f}

Keep climbing to the summit! 🏔️

- Summit Team
"""
    try:
        send_feedback_email(user.email, subject, body, user.full_name)
    except:
        pass

def check_monthly_reports():
    today = datetime.utcnow().date()
    if today.day != 1:
        return
    
    for user in User.query.all():
        existing = MonthlyReport.query.filter_by(
            user_id=user.id,
            month=today.month,
            year=today.year
        ).first()
        if not existing:
            send_monthly_report(user)
            report = MonthlyReport(user_id=user.id, month=today.month, year=today.year)
            db.session.add(report)
    db.session.commit()

# ---------- CREATE OWNER ACCOUNT ----------
def create_owner_account():
    owner_email = "owner@summit.app"
    owner_password = "Summit2026!"
    owner = User.query.filter_by(email=owner_email).first()
    if not owner:
        hashed = bcrypt.hashpw(owner_password.encode(), bcrypt.gensalt()).decode()
        owner = User(
            full_name="Summit Owner",
            email=owner_email,
            password=hashed,
            is_premium=True,
            is_owner=True,
            premium_until=datetime.utcnow() + timedelta(days=365*100),
            referral_code=generate_referral_code()
        )
        db.session.add(owner)
        db.session.commit()
        pref = UserPreference(user_id=owner.id, currency='ZAR', language='en')
        db.session.add(pref)
        db.session.commit()
        print("✅ Owner account created: owner@summit.app / Summit2026!")
    else:
        if not owner.is_premium:
            owner.is_premium = True
            owner.premium_until = datetime.utcnow() + timedelta(days=365*100)
            db.session.commit()
            print("🔒 Owner account updated to Premium")

with app.app_context():
    db.create_all()
    create_owner_account()

# ---------- LANGUAGES ----------
LANGUAGES = {
    'en': {
        'dashboard': 'Dashboard',
        'projects': 'Projects',
        'income': 'Income',
        'crypto': 'Crypto',
        'stocks': 'Stocks',
        'market': 'Market',
        'analytics': 'Analytics',
        'charts': 'Charts',
        'settings': 'Settings',
        'upgrade': 'Upgrade',
        'logout': 'Logout',
        'login': 'Login',
        'signup': 'Sign Up',
        'welcome': 'Welcome Back',
        'create_account': 'Create Account',
        'email': 'Email',
        'password': 'Password',
        'full_name': 'Full Name',
        'save': 'Save',
        'cancel': 'Cancel',
        'delete': 'Delete',
        'edit': 'Edit',
        'add': 'Add',
        'total': 'Total',
        'value': 'Value',
        'price': 'Price',
        'change': 'Change',
        'gain': 'Gain',
        'loss': 'Loss',
        'profit': 'Profit',
        'portfolio': 'Portfolio',
        'holdings': 'Holdings',
        'performance': 'Performance',
        'plan': 'Plan',
        'free': 'Free',
        'premium': 'Premium',
        'month': 'month',
        'year': 'year',
        'upgrade_now': 'Upgrade Now',
        'current_plan': 'Current Plan',
        'most_popular': 'Most Popular',
        'payment': 'Payment',
        'pay_with': 'Pay with',
        'send': 'Send',
        'copy': 'Copy',
        'verify': 'Verify',
        'status': 'Status',
        'pending': 'Pending',
        'completed': 'Completed',
        'failed': 'Failed',
        'date': 'Date',
        'notes': 'Notes',
        'description': 'Description',
        'amount': 'Amount',
        'symbol': 'Symbol',
        'shares': 'Shares',
        'purchase_price': 'Purchase Price',
        'current_price': 'Current Price',
        'gain_loss': 'Gain/Loss',
        'add_stock': 'Add Stock',
        'add_project': 'Add Project',
        'add_income': 'Add Income',
        'add_crypto': 'Add Crypto',
        'recent_activity': 'Recent Activity',
        'total_projects': 'Total Projects',
        'total_income': 'Total Income',
        'crypto_value': 'Crypto Value',
        'stock_value': 'Stock Value',
        'live_prices': 'Live Prices',
        'market_overview': 'Market Overview',
        'index_tracker': 'Index Tracker',
        'top_movers': 'Top Movers',
        'forex_rates': 'Forex Rates',
        'crypto_prices': 'Crypto Prices',
        'portfolio_summary': 'Portfolio Summary',
        'export_data': 'Export Data',
        'export_csv': 'Export CSV',
        'search': 'Search',
        'no_results': 'No results found',
        'loading': 'Loading...',
        'error': 'Error',
        'success': 'Success',
        'warning': 'Warning',
        'info': 'Info',
        'all_rights_reserved': 'All rights reserved',
        'terms': 'Terms of Service',
        'privacy': 'Privacy Policy',
        'contact': 'Contact',
        'help': 'Help',
        'support': 'Support',
        'about': 'About',
        'language': 'Language',
        'currency': 'Currency',
        'profile': 'Profile',
        'security': 'Security',
        'notifications': 'Notifications',
        'dark_mode': 'Dark Mode',
        'light_mode': 'Light Mode',
        'welcome_user': 'Welcome, {name}!',
        'your_tech_empire': 'Your tech empire at a glance',
        'preferences': 'Preferences',
        'choose_plan': 'Choose your plan',
        'free_features': '2 projects • Basic analytics • Crypto & stocks tracking',
        'premium_features': 'Unlimited projects • Deadlines • Advanced analytics • Export CSV • Priority support',
        'payment_note': 'All payments are processed securely via blockchain. Your account is upgraded immediately.',
        'no_projects': 'No projects yet.',
        'no_income': 'No income yet.',
        'no_crypto': 'No crypto yet.',
        'no_stocks': 'No stocks yet.',
        'allocation': 'Allocation',
        'over_time': 'Over Time',
        'stock_history': 'Stock Price History',
        'select_stock': 'Select a stock to see its price history (last 30 days).',
        'source': 'Source',
        'coin': 'Coin',
        'current_password': 'Current Password',
        'new_password': 'New Password',
        'confirm_password': 'Confirm Password',
        'change_password': 'Change Password',
        'account': 'Account',
        'member_since': 'Member since',
        'manage_subscription': 'Manage Subscription',
        'progress': 'Progress',
        'active': 'Active',
        'paused': 'Paused',
        'completed': 'Completed',
        'back': 'Back',
        'to': 'to',
        'name': 'Name',
        'already_have_account': 'Already have an account?',
        'no_account': 'Don\'t have an account?',
        'free_limit': 'You have reached the free limit.',
        'privacy_policy': 'Privacy Policy',
        'terms_of_service': 'Terms of Service',
        'contact_us': 'Contact Us',
        'address': 'Address',
        'feedback': 'Feedback',
        'feedback_subject': 'Summit Feedback',
        'your_email': 'Your Email',
        'subject': 'Subject',
        'message': 'Message',
        'send_feedback': 'Send Feedback',
        'thank_you_feedback': 'Thank you for your feedback! We\'ll get back to you soon.',
        'watchlist': 'Watchlist',
        'expenses': 'Expenses',
        'milestones': 'Milestones',
        'expense': 'Expense',
        'category': 'Category',
        'food': 'Food',
        'transport': 'Transport',
        'entertainment': 'Entertainment',
        'bills': 'Bills',
        'shopping': 'Shopping',
        'other': 'Other',
        'add_expense': 'Add Expense',
        'no_expenses': 'No expenses yet.',
        'total_expenses': 'Total Expenses',
        'target_price': 'Target Price',
        'alert_condition': 'Condition',
        'above': 'Above',
        'below': 'Below',
        'set_alert': 'Set Alert',
        'no_alerts': 'No price alerts.',
        'referral_link': 'Your Referral Link',
        'referral_friends': 'Friends Referred',
        'referral_reward': 'Get 1 month FREE Premium for every 5 friends who sign up!',
        'copy_link': 'Copy Link',
        'linked_share': 'Share Your Link',
        'faq': 'FAQ',
        'faq_question_1': 'Is Summit free?',
        'faq_answer_1': 'Yes! Free plan includes 2 projects and basic tracking.',
        'faq_question_2': 'How much is Premium?',
        'faq_answer_2': 'Only R30/month. Pay with USDC via Trust Wallet.',
        'faq_question_3': 'Is my data safe?',
        'faq_answer_3': 'Yes! Passwords are hashed and we never sell your data.',
        'faq_question_4': 'What does Premium unlock?',
        'faq_answer_4': 'Unlimited projects, advanced analytics, and priority support.',
        'faq_question_5': 'How do I delete my account?',
        'faq_answer_5': 'Go to Settings → Delete Account (or email us).',
        'faq_question_6': 'Do you give financial advice?',
        'faq_answer_6': 'No! We just show your numbers. Always do your own research.',
        'faq_question_7': 'What currencies do you support?',
        'faq_answer_7': 'ZAR, USD, EUR, and GBP.',
        'faq_question_8': 'I have a feature request...',
        'faq_answer_8': 'Use the Feedback form! We read every submission.'
    },
    'af': {
        'dashboard': 'Paneel',
        'projects': 'Projekte',
        'income': 'Inkomste',
        'crypto': 'Kripto',
        'stocks': 'Aandele',
        'market': 'Mark',
        'analytics': 'Analise',
        'charts': 'Grafieke',
        'settings': 'Instellings',
        'upgrade': 'Opgradeer',
        'logout': 'Teken af',
        'login': 'Teken in',
        'signup': 'Registreer',
        'welcome': 'Welkom Terug',
        'create_account': 'Skep Rekening',
        'email': 'E-pos',
        'password': 'Wagwoord',
        'full_name': 'Volle Naam',
        'save': 'Stoor',
        'cancel': 'Kanselleer',
        'delete': 'Verwyder',
        'edit': 'Wysig',
        'add': 'Voeg by',
        'total': 'Totaal',
        'value': 'Waarde',
        'price': 'Prys',
        'change': 'Verandering',
        'gain': 'Wins',
        'loss': 'Verlies',
        'profit': 'Wins',
        'portfolio': 'Portefeulje',
        'holdings': 'Besit',
        'performance': 'Prestasie',
        'plan': 'Plan',
        'free': 'Gratis',
        'premium': 'Premium',
        'month': 'maand',
        'year': 'jaar',
        'upgrade_now': 'Gradeer Nou Op',
        'current_plan': 'Huidige Plan',
        'most_popular': 'Mees Gewild',
        'payment': 'Betaling',
        'pay_with': 'Betaal met',
        'send': 'Stuur',
        'copy': 'Kopieer',
        'verify': 'Verifieer',
        'status': 'Status',
        'pending': 'Hangend',
        'completed': 'Voltooid',
        'failed': 'Misluk',
        'date': 'Datum',
        'notes': 'Notas',
        'description': 'Beskrywing',
        'amount': 'Bedrag',
        'symbol': 'Simbool',
        'shares': 'Aandele',
        'purchase_price': 'Aankoopprys',
        'current_price': 'Huidige Prys',
        'gain_loss': 'Wins/Verlies',
        'add_stock': 'Voeg Aandeel by',
        'add_project': 'Voeg Projek by',
        'add_income': 'Voeg Inkomste by',
        'add_crypto': 'Voeg Kripto by',
        'recent_activity': 'Onlangse Aktiwiteit',
        'total_projects': 'Totale Projekte',
        'total_income': 'Totale Inkomste',
        'crypto_value': 'Kripto Waarde',
        'stock_value': 'Aandeel Waarde',
        'live_prices': 'Regstreekse Pryse',
        'market_overview': 'Mark Oorsig',
        'index_tracker': 'Indeks Tracker',
        'top_movers': 'Top Bewegers',
        'forex_rates': 'Forex Koerse',
        'crypto_prices': 'Kripto Pryse',
        'portfolio_summary': 'Portefeulje Opsomming',
        'export_data': 'Eksporteer Data',
        'export_csv': 'Eksporteer CSV',
        'search': 'Soek',
        'no_results': 'Geen resultate gevind nie',
        'loading': 'Laai...',
        'error': 'Fout',
        'success': 'Sukses',
        'warning': 'Waarskuwing',
        'info': 'Inligting',
        'all_rights_reserved': 'Alle regte voorbehou',
        'terms': 'Diensbepalings',
        'privacy': 'Privaatheidsbeleid',
        'contact': 'Kontak',
        'help': 'Hulp',
        'support': 'Ondersteuning',
        'about': 'Oor',
        'language': 'Taal',
        'currency': 'Geldeenheid',
        'profile': 'Profiel',
        'security': 'Sekuriteit',
        'notifications': 'Kennisgewings',
        'dark_mode': 'Donker Modus',
        'light_mode': 'Lig Modus',
        'welcome_user': 'Welkom, {name}!',
        'your_tech_empire': 'Jou tegnologie-ryk op \'n oogopslag',
        'preferences': 'Voorkeure',
        'choose_plan': 'Kies jou plan',
        'free_features': '2 projekte • Basiese analise • Kripto en aandele dop',
        'premium_features': 'Ongelimiteerde projekte • Sperdatums • Gevorderde analise • Eksporteer CSV • Prioriteit ondersteuning',
        'payment_note': 'Alle betalings word veilig via blockchain verwerk. Jou rekening word onmiddellik opgegradeer.',
        'no_projects': 'Nog geen projekte nie.',
        'no_income': 'Nog geen inkomste nie.',
        'no_crypto': 'Nog geen kripto nie.',
        'no_stocks': 'Nog geen aandele nie.',
        'allocation': 'Toewysing',
        'over_time': 'Oor Tyd',
        'stock_history': 'Aandeel Prys Geskiedenis',
        'select_stock': 'Kies \'n aandeel om sy prysgeskiedenis te sien (laaste 30 dae).',
        'source': 'Bron',
        'coin': 'Munt',
        'current_password': 'Huidige Wagwoord',
        'new_password': 'Nuwe Wagwoord',
        'confirm_password': 'Bevestig Wagwoord',
        'change_password': 'Verander Wagwoord',
        'account': 'Rekening',
        'member_since': 'Lid sedert',
        'manage_subscription': 'Bestuur Inskrywing',
        'progress': 'Vordering',
        'active': 'Aktief',
        'paused': 'Gepouseer',
        'completed': 'Voltooi',
        'back': 'Terug',
        'to': 'na',
        'name': 'Naam',
        'already_have_account': 'Reeds \'n rekening?',
        'no_account': 'Nie \'n rekening nie?',
        'free_limit': 'Jy het die gratis limiet bereik.',
        'privacy_policy': 'Privaatheidsbeleid',
        'terms_of_service': 'Diensbepalings',
        'contact_us': 'Kontak Ons',
        'address': 'Adres',
        'feedback': 'Terugvoer',
        'feedback_subject': 'Summit Terugvoer',
        'your_email': 'Jou E-pos',
        'subject': 'Onderwerp',
        'message': 'Boodskap',
        'send_feedback': 'Stuur Terugvoer',
        'thank_you_feedback': 'Dankie vir jou terugvoer! Ons sal binnekort by jou uitkom.',
        'watchlist': 'Dophoulys',
        'expenses': 'Uitgawes',
        'milestones': 'Mylpale',
        'expense': 'Uitgawe',
        'category': 'Kategorie',
        'food': 'Kos',
        'transport': 'Vervoer',
        'entertainment': 'Vermaak',
        'bills': 'Rekeninge',
        'shopping': 'Inkopies',
        'other': 'Ander',
        'add_expense': 'Voeg Uitgawe by',
        'no_expenses': 'Nog geen uitgawes nie.',
        'total_expenses': 'Totale Uitgawes',
        'target_price': 'Teikenprys',
        'alert_condition': 'Voorwaarde',
        'above': 'Bo',
        'below': 'Onder',
        'set_alert': 'Stel Waarskuwing',
        'no_alerts': 'Geen prys waarskuwings nie.',
        'referral_link': 'Jou Verwysingsskakel',
        'referral_friends': 'Vriende Verwys',
        'referral_reward': 'Kry 1 maand GRATIS Premium vir elke 5 vriende wat registreer!',
        'copy_link': 'Kopieer Skakel',
        'linked_share': 'Deel Jou Skakel',
        'faq': 'FAQ',
        'faq_question_1': 'Is Summit gratis?',
        'faq_answer_1': 'Ja! Gratis plan sluit 2 projekte en basiese dop in.',
        'faq_question_2': 'Hoeveel kos Premium?',
        'faq_answer_2': 'Slegs R30/maand. Betaal met USDC via Trust Wallet.',
        'faq_question_3': 'Is my data veilig?',
        'faq_answer_3': 'Ja! Wagwoorde is gehasj en ons verkoop nooit jou data nie.',
        'faq_question_4': 'Wat sluit Premium in?',
        'faq_answer_4': 'Ongelimiteerde projekte, gevorderde analise en prioriteit ondersteuning.',
        'faq_question_5': 'Hoe skrap ek my rekening?',
        'faq_answer_5': 'Gaan na Instellings → Skrap Rekening (of e-pos ons).',
        'faq_question_6': 'Gee julle finansiële advies?',
        'faq_answer_6': 'Nee! Ons wys net jou syfers. Doen altyd jou eie navorsing.',
        'faq_question_7': 'Watter geldeenhede ondersteun julle?',
        'faq_answer_7': 'ZAR, USD, EUR en GBP.',
        'faq_question_8': 'Ek het \'n voorstel vir \'n funksie...',
        'faq_answer_8': 'Gebruik die Terugvoer-vorm! Ons lees elke insending.'
    },
    'zu': {
        'dashboard': 'Iphaneli',
        'projects': 'Amaphrojekthi',
        'income': 'Imali engenayo',
        'crypto': 'I-Crypto',
        'stocks': 'Amasheya',
        'market': 'Imakethe',
        'analytics': 'Ukuhlaziya',
        'charts': 'Amashadi',
        'settings': 'Izilungiselelo',
        'upgrade': 'Thuthukisa',
        'logout': 'Phuma',
        'login': 'Ngena',
        'signup': 'Bhalisa',
        'welcome': 'Siyakwamukela',
        'create_account': 'Yenza I-akhawunti',
        'email': 'I-imeyili',
        'password': 'Iphasiwedi',
        'full_name': 'Igama Eligcwele',
        'save': 'Londoloza',
        'cancel': 'Khansela',
        'delete': 'Susa',
        'edit': 'Hlela',
        'add': 'Engeza',
        'total': 'Ingqikithi',
        'value': 'Inani',
        'price': 'Intengo',
        'change': 'Ushintsho',
        'gain': 'Inzuzo',
        'loss': 'Ukulahlekelwa',
        'profit': 'Inzuzo',
        'portfolio': 'Iphothifoliyo',
        'holdings': 'Okuphathwayo',
        'performance': 'Ukusebenza',
        'plan': 'Icebo',
        'free': 'Mahhala',
        'premium': 'I-Premium',
        'month': 'inyanga',
        'year': 'unyaka',
        'upgrade_now': 'Thuthukisa Manje',
        'current_plan': 'Icebo Lamanje',
        'most_popular': 'Ethandwa Kakhulu',
        'payment': 'Inkokhelo',
        'pay_with': 'Khokha nge',
        'send': 'Thumela',
        'copy': 'Kopisha',
        'verify': 'Qinisekisa',
        'status': 'Isimo',
        'pending': 'Kusalindile',
        'completed': 'Kuphelile',
        'failed': 'Yehlulekile',
        'date': 'Usuku',
        'notes': 'Amanothi',
        'description': 'Incazelo',
        'amount': 'Inani',
        'symbol': 'Uphawu',
        'shares': 'Amasheya',
        'purchase_price': 'Intengo Yokuthenga',
        'current_price': 'Intengo Yamanje',
        'gain_loss': 'Inzuzo/Ukulahlekelwa',
        'add_stock': 'Engeza Isheya',
        'add_project': 'Engeza Iphrojekthi',
        'add_income': 'Engeza Imali engenayo',
        'add_crypto': 'Engeza I-Crypto',
        'recent_activity': 'Umsebenzi Wakamuva',
        'total_projects': 'Amaphrojekthi Aphelele',
        'total_income': 'Imali Engeyayo Ephelele',
        'crypto_value': 'Inani le-Crypto',
        'stock_value': 'Inani Lamasheya',
        'live_prices': 'Izintengo Ezibukhoma',
        'market_overview': 'Ukubuka Kwemakethe',
        'index_tracker': 'Umkhondo Wezinkomba',
        'top_movers': 'Abahamba Phambili',
        'forex_rates': 'Amanani E-Forex',
        'crypto_prices': 'Izintengo Ze-Crypto',
        'portfolio_summary': 'Isifinyezo Sephothifoliyo',
        'export_data': 'Khipha Idatha',
        'export_csv': 'Khipha I-CSV',
        'search': 'Sesha',
        'no_results': 'Ayikho imiphumela etholakalayo',
        'loading': 'Iyalayisha...',
        'error': 'Iphutha',
        'success': 'Impumelelo',
        'warning': 'Isixwayiso',
        'info': 'Ulwazi',
        'all_rights_reserved': 'Wonke amalungelo agodliwe',
        'terms': 'Imigomo Yesevisi',
        'privacy': 'Inqubomgomo Yobumfihlo',
        'contact': 'Xhumana nathi',
        'help': 'Usizo',
        'support': 'Ukusekela',
        'about': 'Mayelana',
        'language': 'Ulimi',
        'currency': 'Uhlobo Lwemali',
        'profile': 'Iphrofayili',
        'security': 'Ukuphepha',
        'notifications': 'Izaziso',
        'dark_mode': 'Imodi Emnyama',
        'light_mode': 'Imodi Ekhanyayo',
        'welcome_user': 'Siyakwamukela, {name}!',
        'your_tech_empire': 'Umbuso wakho wezobuchwepheshe ngokubuka okukodwa',
        'preferences': 'Izintandokazi',
        'choose_plan': 'Khetha icebo lakho',
        'free_features': 'Amaphrojekthi angu-2 • Ukuhlaziya okuyisisekelo • Ukulandelela i-Crypto namasheya',
        'premium_features': 'Amaphrojekthi angenamkhawulo • Imihlathana • Ukuhlaziya okuthuthukisiwe • Khipha i-CSV • Ukusekela okuphambili',
        'payment_note': 'Zonke izinkokhelo zicubungulwa ngokuphepha nge-blockchain. I-akhawunti yakho ithuthukiswa ngokushesha.',
        'no_projects': 'Ayikho imiphakathi.',
        'no_income': 'Ayikho imali engenayo.',
        'no_crypto': 'Ayikho i-Crypto.',
        'no_stocks': 'Awekho amasheya.',
        'allocation': 'Isabelo',
        'over_time': 'Ngokuhamba Kwesikhathi',
        'stock_history': 'Umlando Wentengo Yamasheya',
        'select_stock': 'Khetha isheya ukuze ubone umlando wentengo (izinsuku ezingama-30 ezedlule).',
        'source': 'Umthombo',
        'coin': 'Uhlamvu',
        'current_password': 'Iphasiwedi Yamanje',
        'new_password': 'Iphasiwedi Entsha',
        'confirm_password': 'Qinisekisa Iphasiwedi',
        'change_password': 'Shintsha Iphasiwedi',
        'account': 'I-akhawunti',
        'member_since': 'Uyilungu kusukela',
        'manage_subscription': 'Phatha Ukubhalisa',
        'progress': 'Inqubekela',
        'active': 'Iyasebenza',
        'paused': 'Imisiwe',
        'completed': 'Iphethiwe',
        'back': 'Buyela emuva',
        'to': 'kuya',
        'name': 'Igama',
        'already_have_account': 'Usunayo i-akhawunti?',
        'no_account': 'Awunayo i-akhawunti?',
        'free_limit': 'Ufinyelele umkhawulo wamahhala.',
        'privacy_policy': 'Inqubomgomo Yobumfihlo',
        'terms_of_service': 'Imigomo Yesevisi',
        'contact_us': 'Xhumana Nathi',
        'address': 'Ikheli',
        'feedback': 'Impembelelo',
        'feedback_subject': 'Impembelelo ye-Summit',
        'your_email': 'I-imeyili Yakho',
        'subject': 'Isihloko',
        'message': 'Umyalezo',
        'send_feedback': 'Thumela Impembelelo',
        'thank_you_feedback': 'Siyabonga ngempembelelo yakho! Sizokuthinta maduzane.',
        'watchlist': 'Uhlu Lokubuka',
        'expenses': 'Izindleko',
        'milestones': 'Amagatsha',
        'expense': 'Izindleko',
        'category': 'Isigaba',
        'food': 'Ukudla',
        'transport': 'Ezokuthutha',
        'entertainment': 'Ukuzijabulisa',
        'bills': 'Izikweletu',
        'shopping': 'Ukuthenga',
        'other': 'Okunye',
        'add_expense': 'Engeza Izindleko',
        'no_expenses': 'Azikho izindleko.',
        'total_expenses': 'Izindleko Zonke',
        'target_price': 'Intengo Ehlosiwe',
        'alert_condition': 'Umbandela',
        'above': 'Ngaphezulu',
        'below': 'Ngaphansi',
        'set_alert': 'Hlela Isaziso',
        'no_alerts': 'Azikho izaziso zentengo.',
        'referral_link': 'Isixhumanisi Sakho Sokudlulisela',
        'referral_friends': 'Abangane Abadluliselwe',
        'referral_reward': 'Thola 1 inyanga MAHALA ye-Premium uma abangane abangu-5 bebhalisa!',
        'copy_link': 'Kopisha Isixhumanisi',
        'linked_share': 'Yabelana Ngesixhumanisi Sakho',
        'faq': 'IMIBUZO',
        'faq_question_1': 'Ingabe i-Summit yamahhala?',
        'faq_answer_1': 'Yebo! Uhlelo lwamahhala luhlanganisa amaphrojekthi ama-2 nokulandelela okuyisisekelo.',
        'faq_question_2': 'Ibiza malini i-Premium?',
        'faq_answer_2': 'R30 ngenyanga kuphela. Khokha nge-USDC nge-Trust Wallet.',
        'faq_question_3': 'Ingabe idatha yami iphephile?',
        'faq_answer_3': 'Yebo! Amaphasiwedi ahlanziwe futhi asiwathengisi idatha yakho.',
        'faq_question_4': 'Yini evulekayo nge-Premium?',
        'faq_answer_4': 'Amaphrojekthi angenamkhawulo, ukuhlaziya okuthuthukisiwe, nokusekelwa okuphambili.',
        'faq_question_5': 'Ngiyisusa kanjani i-akhawunti yami?',
        'faq_answer_5': 'Iya ku-Settings → Susa I-akhawunti (noma usithumelele i-imeyili).',
        'faq_question_6': 'Ingabe ninikeza iseluleko sezezimali?',
        'faq_answer_6': 'Cha! Sibonisa nje izinombolo zakho. Hlale wenza ucwaningo lwakho.',
        'faq_question_7': 'Yimaphi amaphepha-mali eniwasekelayo?',
        'faq_answer_7': 'ZAR, USD, EUR, ne-GBP.',
        'faq_question_8': 'Nginomqondo wokwengeza isici...',
        'faq_answer_8': 'Sebenzisa ifomu le-Impembelelo! Sifunda konke okuthunyelwe.'
    }
}

# ---------- HELPER FUNCTIONS ----------
def get_user_preference(user_id, key):
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        return 'ZAR' if key == 'currency' else 'en'
    return pref.currency if key == 'currency' else pref.language

def get_currency_symbol(currency):
    symbols = {
        'ZAR': 'R',
        'USD': '$',
        'EUR': '€',
        'GBP': '£'
    }
    return symbols.get(currency, 'R')

def t(key, lang='en'):
    return LANGUAGES.get(lang, LANGUAGES['en']).get(key, key)

def get_stock_price(symbol):
    try:
        ticker = yf.Ticker(symbol.upper())
        info = ticker.info
        price = info.get('currentPrice', info.get('regularMarketPrice', None))
        if price:
            return price
        data = ticker.history(period="1d", timeout=5)
        if data.empty:
            return None
        return data['Close'].iloc[-1]
    except:
        return None

def get_stock_info(symbol):
    try:
        ticker = yf.Ticker(symbol.upper())
        info = ticker.info
        return {
            'name': info.get('longName', symbol.upper()),
            'price': info.get('currentPrice', info.get('regularMarketPrice', 0))
        }
    except:
        return None

def check_usdc_payment():
    if not ETHERSCAN_API_KEY or not WALLET_ADDRESS:
        return {'success': False, 'message': 'Etherscan not configured'}
    USDC_CONTRACT = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'
    url = 'https://api.etherscan.io/api'
    params = {
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': USDC_CONTRACT,
        'address': WALLET_ADDRESS,
        'page': 1,
        'offset': 100,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get('status') != '1':
            return {'success': False, 'message': 'Etherscan API error'}
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        for tx in data.get('result', []):
            amount = int(tx['value']) / 10**6
            if 1.58 <= amount <= 1.62:
                tx_time = datetime.fromtimestamp(int(tx['timeStamp']))
                if tx_time > one_hour_ago:
                    return {
                        'success': True,
                        'amount': amount,
                        'tx_hash': tx['hash'],
                        'from': tx['from']
                    }
        return {'success': False, 'message': 'No recent $1.60 USDC payment found'}
    except Exception as e:
        return {'success': False, 'message': str(e)}

# ---------- EMAIL HELPER ----------
def send_feedback_email(user_email, subject, message, user_name='User'):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print(f"📧 FEEDBACK from {user_email}: {subject}\n{message}")
        return
    
    try:
        msg = MIMEMultipart()
        msg['From'] = MAIL_DEFAULT_SENDER
        msg['To'] = FEEDBACK_RECIPIENT
        msg['Subject'] = f"[Summit] {subject}"
        
        body = f"""
Hi {user_name},

{message}

---
Sent via Summit
User: {user_name}
Email: {user_email}
"""
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            if MAIL_USE_TLS:
                server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"Email error: {e}")
        raise

# ---------- TELEGRAM BOT ----------
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
        requests.post(url, json=payload, timeout=5)
    except:
        pass

# ---------- BASE HTML ----------
BASE_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>Summit – {% block title %}Dashboard{% endblock %}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="manifest" href="/static/manifest.json">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
    <style>
        :root {
            --bg-primary: #0a0a0f;
            --bg-secondary: rgba(255,255,255,0.03);
            --bg-card: rgba(255,255,255,0.03);
            --text-primary: #e8edf5;
            --text-secondary: #9ca3af;
            --text-muted: #6b7280;
            --border-color: rgba(255,255,255,0.06);
            --card-hover: rgba(255,255,255,0.1);
            --shadow-color: rgba(0,0,0,0.4);
        }
        [data-theme="light"] {
            --bg-primary: #f0f2f5;
            --bg-secondary: #ffffff;
            --bg-card: #ffffff;
            --text-primary: #1a1a2e;
            --text-secondary: #4b5563;
            --text-muted: #6b7280;
            --border-color: rgba(0,0,0,0.08);
            --card-hover: rgba(0,0,0,0.05);
            --shadow-color: rgba(0,0,0,0.1);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            transition: background 0.3s ease, color 0.3s ease;
            background-image: radial-gradient(ellipse at 10% 20%, rgba(59,130,246,0.08) 0%, transparent 50%),
                              radial-gradient(ellipse at 90% 80%, rgba(139,92,246,0.06) 0%, transparent 50%);
        }
        [data-theme="light"] body {
            background-image: none;
        }

        .app-container {
            display: flex;
            min-height: 100vh;
        }

        .sidebar {
            width: 220px;
            background: rgba(20,20,30,0.95);
            backdrop-filter: blur(12px);
            border-right: 1px solid var(--border-color);
            padding: 20px 12px;
            display: flex;
            flex-direction: column;
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            height: 100vh;
            overflow-y: auto;
            z-index: 1000;
            transform: translateX(-100%);
            transition: transform 0.3s ease;
        }
        [data-theme="light"] .sidebar {
            background: rgba(255,255,255,0.95);
        }
        .sidebar.open {
            transform: translateX(0);
        }

        .sidebar-overlay {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.6);
            z-index: 999;
            backdrop-filter: blur(2px);
        }
        .sidebar-overlay.active {
            display: block;
        }

        .sidebar .logo {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
            font-size: 22px;
            font-weight: 800;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 16px;
        }
        .sidebar .logo-icon {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 18px;
            color: white;
            -webkit-text-fill-color: white;
        }
        .sidebar nav ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .sidebar nav ul li {
            margin-bottom: 2px;
        }
        .sidebar nav ul li a {
            display: block;
            padding: 8px 14px;
            border-radius: 10px;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .sidebar nav ul li a:hover {
            background: var(--bg-secondary);
            color: var(--text-primary);
        }
        .sidebar nav ul li a.active {
            background: rgba(59,130,246,0.15);
            color: #60a5fa;
        }
        .sidebar nav ul li hr {
            border: none;
            border-top: 1px solid var(--border-color);
            margin: 8px 12px;
        }
        .sidebar-footer {
            margin-top: auto;
            padding-top: 16px;
            border-top: 1px solid var(--border-color);
        }
        .sidebar-footer a {
            display: block;
            padding: 8px 14px;
            border-radius: 10px;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .sidebar-footer a:hover {
            background: rgba(239,68,68,0.15);
            color: #ef4444;
        }

        .theme-toggle {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 14px;
            border-radius: 10px;
            background: var(--bg-secondary);
            margin-bottom: 12px;
            cursor: pointer;
            border: 1px solid var(--border-color);
            transition: all 0.2s;
        }
        .theme-toggle:hover {
            background: var(--card-hover);
        }
        .theme-toggle span {
            color: var(--text-secondary);
            font-size: 13px;
        }

        .main {
            flex: 1;
            padding: 16px;
            margin-left: 0;
            width: 100%;
            transition: margin-left 0.3s;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        .hamburger {
            display: flex;
            flex-direction: column;
            gap: 5px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            padding: 10px 12px;
            border-radius: 10px;
            cursor: pointer;
            margin-bottom: 16px;
            width: fit-content;
        }
        .hamburger span {
            display: block;
            width: 24px;
            height: 2px;
            background: var(--text-primary);
            border-radius: 2px;
            transition: all 0.3s;
        }
        .hamburger.open span:nth-child(1) { transform: rotate(45deg) translate(5px, 5px); }
        .hamburger.open span:nth-child(2) { opacity: 0; }
        .hamburger.open span:nth-child(3) { transform: rotate(-45deg) translate(5px, -5px); }

        .card {
            background: var(--bg-card);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 18px;
            margin-bottom: 16px;
            transition: all 0.3s;
        }
        .card:hover {
            border-color: var(--card-hover);
            box-shadow: 0 8px 40px var(--shadow-color);
        }
        .card h3 {
            color: var(--text-primary);
            margin-bottom: 12px;
            font-size: 17px;
            font-weight: 600;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }
        .stat {
            text-align: center;
            padding: 16px 10px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            transition: all 0.3s;
        }
        .stat h2 {
            font-size: 24px;
            font-weight: 700;
            color: var(--text-primary);
        }
        .stat p {
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 500;
            margin-top: 4px;
        }

        .btn {
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.2s;
            text-decoration: none;
            display: inline-block;
            text-align: center;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 30px rgba(59,130,246,0.3);
        }
        .btn-ghost {
            background: var(--bg-secondary);
            color: var(--text-secondary);
            border: 1px solid var(--border-color);
        }
        .btn-ghost:hover {
            background: var(--card-hover);
            transform: translateY(-2px);
        }

        input, select, textarea {
            width: 100%;
            padding: 11px 14px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            color: var(--text-primary);
            font-size: 14px;
            transition: all 0.2s;
            margin-bottom: 12px;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59,130,246,0.15);
        }

        .table-wrapper {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }
        table {
            width: 100%;
            min-width: 400px;
            border-collapse: collapse;
        }
        th {
            text-align: left;
            padding: 10px 8px;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        td {
            padding: 10px 8px;
            border-bottom: 1px solid var(--border-color);
            font-size: 13px;
        }
        tr:last-child td { border-bottom: none; }

        .badge {
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 500;
            display: inline-block;
            white-space: nowrap;
        }
        .badge-active { background: rgba(34,197,94,0.15); color: #22c55e; }
        .badge-paused { background: rgba(234,179,8,0.15); color: #facc15; }
        .badge-completed { background: rgba(59,130,246,0.15); color: #60a5fa; }
        .badge-premium { background: linear-gradient(135deg, rgba(245,158,11,0.2), rgba(239,68,68,0.2)); color: #f59e0b; }
        .badge-free { background: var(--bg-secondary); color: var(--text-muted); }
        .green { color: #22c55e; }
        .red { color: #ef4444; }

        .flash {
            padding: 12px 16px;
            border-radius: 12px;
            margin-bottom: 14px;
            font-size: 14px;
            font-weight: 500;
            animation: slideDown 0.3s ease;
        }
        @keyframes slideDown { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
        .flash-success { background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.15); color: #22c55e; }
        .flash-danger { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.15); color: #ef4444; }
        .flash-warning { background: rgba(234,179,8,0.1); border: 1px solid rgba(234,179,8,0.15); color: #facc15; }
        .flash-info { background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.15); color: #60a5fa; }

        .progress-bar {
            width: 100%;
            height: 5px;
            background: var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            margin: 4px 0;
        }
        .progress-bar .fill {
            height: 100%;
            border-radius: 10px;
            transition: width 0.3s;
        }

        .flex {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .text-muted { color: var(--text-muted); }
        .mt-10 { margin-top: 10px; }
        .mt-20 { margin-top: 20px; }

        .chart-container {
            display: grid;
            grid-template-columns: 1fr;
            gap: 16px;
            margin: 16px 0;
        }
        .chart-box {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 16px;
            min-height: 250px;
        }
        .chart-box h4 {
            color: var(--text-primary);
            margin-bottom: 12px;
            font-size: 15px;
            font-weight: 600;
        }
        .chart-box canvas {
            max-height: 200px;
            width: 100% !important;
        }

        .address-box {
            background: rgba(0,0,0,0.3);
            padding: 12px;
            border-radius: 12px;
            word-break: break-all;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            margin: 10px 0;
            border: 1px solid var(--border-color);
        }

        .pricing-card {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px 16px;
            text-align: center;
        }
        .pricing-card.featured {
            background: linear-gradient(135deg, rgba(59,130,246,0.05), rgba(139,92,246,0.05));
            border-color: rgba(59,130,246,0.3);
        }
        .pricing-card .price {
            font-size: 32px;
            font-weight: 700;
            color: var(--text-primary);
            margin: 12px 0;
        }
        .pricing-card .price span {
            font-size: 14px;
            color: var(--text-muted);
            font-weight: 400;
        }
        .pricing-card ul {
            list-style: none;
            padding: 0;
            margin: 12px 0;
        }
        .pricing-card ul li {
            padding: 4px 0;
            color: var(--text-secondary);
            font-size: 13px;
        }
        .pricing-card ul li::before {
            content: "✓ ";
            color: #22c55e;
        }

        .owner-badge {
            background: linear-gradient(135deg, #f59e0b, #ef4444);
            color: white;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: 600;
            margin-left: 6px;
        }

        .app-footer {
            margin-top: auto;
            padding-top: 16px;
            border-top: 1px solid var(--border-color);
            display: flex;
            justify-content: center;
            gap: 16px;
            flex-wrap: wrap;
        }
        .app-footer a {
            color: var(--text-muted);
            text-decoration: none;
            font-size: 12px;
            transition: color 0.2s;
        }
        .app-footer a:hover {
            color: var(--text-secondary);
        }
        .app-footer span {
            color: var(--text-muted);
            font-size: 12px;
        }

        .ad-container {
            background: var(--bg-secondary);
            border: 1px dashed var(--border-color);
            border-radius: 12px;
            padding: 16px;
            margin: 16px 0;
            text-align: center;
            min-height: 70px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--text-muted);
            font-size: 13px;
        }

        .share-card {
            background: var(--bg-primary);
            border-radius: 20px;
            padding: 30px;
            text-align: center;
            border: 1px solid var(--border-color);
        }
        .share-card h1 {
            font-size: 32px;
            font-weight: 800;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .share-card .gain {
            font-size: 48px;
            font-weight: 700;
            color: #22c55e;
            margin: 10px 0;
        }
        .share-card .sub {
            color: var(--text-muted);
            font-size: 14px;
        }
        .share-card .stats {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin: 16px 0;
        }
        .share-card .stats div {
            background: var(--bg-secondary);
            padding: 12px;
            border-radius: 12px;
        }
        .share-card .stats div .num {
            font-size: 20px;
            font-weight: 700;
            color: var(--text-primary);
        }

        @media (min-width: 769px) {
            .sidebar {
                transform: translateX(0);
                position: sticky;
                top: 0;
                left: 0;
                height: 100vh;
                width: 220px;
                flex-shrink: 0;
            }
            .hamburger, .sidebar-overlay {
                display: none !important;
            }
            .main {
                margin-left: 0;
                padding: 24px 32px;
            }
            .grid {
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            }
            .chart-container {
                grid-template-columns: 1fr 1fr;
            }
        }

        @media (max-width: 768px) {
            .main {
                padding: 12px;
            }
            .grid {
                grid-template-columns: 1fr 1fr;
                gap: 10px;
            }
            .stat {
                padding: 12px 8px;
            }
            .stat h2 {
                font-size: 20px;
            }
            .stat p {
                font-size: 11px;
            }
            .chart-container {
                grid-template-columns: 1fr;
            }
            .card {
                padding: 14px;
            }
            .pricing-card {
                padding: 16px;
            }
            .pricing-card .price {
                font-size: 28px;
            }
            .app-footer {
                gap: 12px;
            }
            .app-footer a {
                font-size: 11px;
            }
        }

        @media (max-width: 480px) {
            .grid {
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }
            .stat {
                padding: 10px 6px;
            }
            .stat h2 {
                font-size: 18px;
            }
            .stat p {
                font-size: 10px;
            }
            .btn {
                font-size: 13px;
                padding: 8px 16px;
            }
            .card {
                padding: 12px;
            }
            .main {
                padding: 8px;
            }
        }
    </style>
</head>
<body>
    <div class="app-container">
        <div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>

        <div class="sidebar" id="sidebar">
            <div class="logo">
                <span class="logo-icon">S</span>
                <span class="logo-text">Summit</span>
            </div>
            <div class="theme-toggle" onclick="toggleTheme()">
                <span id="themeIcon">🌙</span>
                <span id="themeLabel">Dark Mode</span>
            </div>
            <nav>
                <ul>
                    <li><a href="{{ url_for('dashboard') }}" {% if request.endpoint == 'dashboard' %}class="active"{% endif %}><span>{{ t('dashboard', lang) }}</span></a></li>
                    <li><hr></li>
                    <li><a href="{{ url_for('projects') }}" {% if request.endpoint == 'projects' %}class="active"{% endif %}><span>{{ t('projects', lang) }}</span></a></li>
                    <li><a href="{{ url_for('income') }}" {% if request.endpoint == 'income' %}class="active"{% endif %}><span>{{ t('income', lang) }}</span></a></li>
                    <li><a href="{{ url_for('expenses') }}" {% if request.endpoint == 'expenses' %}class="active"{% endif %}><span>{{ t('expenses', lang) }}</span></a></li>
                    <li><a href="{{ url_for('liabilities') }}" {% if request.endpoint == 'liabilities' %}class="active"{% endif %}><span>💳 Liabilities</span></a></li>
                    <li><a href="{{ url_for('crypto') }}" {% if request.endpoint == 'crypto' %}class="active"{% endif %}><span>{{ t('crypto', lang) }}</span></a></li>
                    <li><a href="{{ url_for('stocks') }}" {% if request.endpoint == 'stocks' %}class="active"{% endif %}><span>{{ t('stocks', lang) }}</span></a></li>
                    <li><hr></li>
                    <li><a href="{{ url_for('market') }}" {% if request.endpoint == 'market' %}class="active"{% endif %}><span>{{ t('market', lang) }}</span></a></li>
                    <li><a href="{{ url_for('crypto_prices') }}" {% if request.endpoint == 'crypto_prices' %}class="active"{% endif %}><span>{{ t('crypto_prices', lang) }}</span></a></li>
                    <li><a href="{{ url_for('forex_rates') }}" {% if request.endpoint == 'forex_rates' %}class="active"{% endif %}><span>{{ t('forex_rates', lang) }}</span></a></li>
                    <li><a href="{{ url_for('top_movers') }}" {% if request.endpoint == 'top_movers' %}class="active"{% endif %}><span>{{ t('top_movers', lang) }}</span></a></li>
                    <li><a href="{{ url_for('index_tracker') }}" {% if request.endpoint == 'index_tracker' %}class="active"{% endif %}><span>{{ t('index_tracker', lang) }}</span></a></li>
                    <li><hr></li>
                    <li><a href="{{ url_for('analytics') }}" {% if request.endpoint == 'analytics' %}class="active"{% endif %}><span>{{ t('analytics', lang) }}</span></a></li>
                    <li><a href="{{ url_for('charts') }}" {% if request.endpoint == 'charts' %}class="active"{% endif %}><span>{{ t('charts', lang) }}</span></a></li>
                    <li><a href="{{ url_for('referral') }}" {% if request.endpoint == 'referral' %}class="active"{% endif %}><span>🔗 {{ t('referral_link', lang) }}</span></a></li>
                    <li><a href="{{ url_for('community') }}" {% if request.endpoint == 'community' %}class="active"{% endif %}><span>👥 Community</span></a></li>
                    <li><a href="{{ url_for('faq') }}" {% if request.endpoint == 'faq' %}class="active"{% endif %}><span>❓ {{ t('faq', lang) }}</span></a></li>
                    <li><a href="{{ url_for('upgrade') }}" {% if request.endpoint == 'upgrade' %}class="active"{% endif %}><span>{{ t('upgrade', lang) }}</span></a></li>
                    <li><a href="{{ url_for('settings') }}" {% if request.endpoint == 'settings' %}class="active"{% endif %}><span>{{ t('settings', lang) }}</span></a></li>
                    <li><a href="{{ url_for('feedback') }}" {% if request.endpoint == 'feedback' %}class="active"{% endif %}><span>{{ t('feedback', lang) }}</span></a></li>
                </ul>
            </nav>
            <div class="sidebar-footer">
                <a href="{{ url_for('logout') }}"><span>{{ t('logout', lang) }}</span></a>
            </div>
        </div>

        <div class="main">
            <button class="hamburger" id="hamburgerBtn" onclick="toggleSidebar()" aria-label="Toggle menu">
                <span></span>
                <span></span>
                <span></span>
            </button>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash flash-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <div class="ad-container" id="ad-container">
                <span>📢 Ad Space – Media.net (Pending Approval)</span>
            </div>

            {{ content|safe }}

            <div class="app-footer">
                <a href="{{ url_for('privacy') }}">{{ t('privacy', lang) }}</a>
                <a href="{{ url_for('terms') }}">{{ t('terms', lang) }}</a>
                <a href="{{ url_for('contact') }}">{{ t('contact', lang) }}</a>
                <a href="{{ url_for('feedback') }}">{{ t('feedback', lang) }}</a>
                <span>© 2026 Summit</span>
            </div>
        </div>
    </div>

    <script>
        // PWA Service Worker
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/static/sw.js').then(function(reg) {
                console.log('✅ Service Worker registered');
            }).catch(function(err) {
                console.log('❌ Service Worker error:', err);
            });
        }

        // Theme toggle
        function toggleTheme() {
            const html = document.documentElement;
            const current = html.getAttribute('data-theme');
            const newTheme = current === 'light' ? 'dark' : 'light';
            html.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateThemeUI(newTheme);
        }
        function updateThemeUI(theme) {
            const icon = document.getElementById('themeIcon');
            const label = document.getElementById('themeLabel');
            if (theme === 'light') {
                icon.textContent = '☀️';
                label.textContent = 'Light Mode';
            } else {
                icon.textContent = '🌙';
                label.textContent = 'Dark Mode';
            }
        }
        document.addEventListener('DOMContentLoaded', function() {
            const saved = localStorage.getItem('theme') || 'dark';
            document.documentElement.setAttribute('data-theme', saved);
            updateThemeUI(saved);
        });

        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('sidebarOverlay');
            const btn = document.getElementById('hamburgerBtn');
            sidebar.classList.toggle('open');
            overlay.classList.toggle('active');
            btn.classList.toggle('open');
        }

        document.querySelectorAll('.sidebar nav ul li a').forEach(function(link) {
            link.addEventListener('click', function() {
                if (window.innerWidth <= 768) {
                    toggleSidebar();
                }
            });
        });

        window.addEventListener('resize', function() {
            if (window.innerWidth > 768) {
                const sidebar = document.getElementById('sidebar');
                const overlay = document.getElementById('sidebarOverlay');
                const btn = document.getElementById('hamburgerBtn');
                sidebar.classList.remove('open');
                overlay.classList.remove('active');
                btn.classList.remove('open');
            }
        });

        // Social Share Function
        function shareProgress() {
            const element = document.getElementById('share-card');
            if (!element) return;
            
            html2canvas(element, {
                scale: 2,
                backgroundColor: '#0a0a0f',
                useCORS: true
            }).then(canvas => {
                const link = document.createElement('a');
                link.download = 'summit-progress.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
                alert('📤 Share your progress on social media!');
            }).catch(err => {
                alert('❌ Error generating image. Please try again.');
                console.error(err);
            });
        }
    </script>
</body>
</html>
"""

# ---------- ROUTES ----------
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    
    lang = 'en'
    user_count = User.query.count()
    
    page = f"""
    <div style="text-align:center;padding:60px 20px;background:linear-gradient(135deg,rgba(59,130,246,0.05),rgba(139,92,246,0.05));border-radius:24px;margin-bottom:40px;">
        <h1 style="font-size:52px;font-weight:900;background:linear-gradient(135deg,#3b82f6,#8b5cf6,#ec4899);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">Track. Grow. Reach the Summit.</h1>
        <p style="font-size:20px;color:var(--text-secondary);max-width:600px;margin:16px auto 30px;">All your finances. One view. Built for the next generation.</p>
        <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
            <a href="/signup" class="btn">Sign Up</a>
            <a href="/login" class="btn btn-ghost">Login</a>
        </div>
        <p style="color:var(--text-muted);font-size:14px;margin-top:16px;">⭐ Trusted by {user_count} users</p>
    </div>
    
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px;margin-bottom:40px;">
        <div class="card" style="text-align:center;"><div style="font-size:40px;margin-bottom:8px;">📁</div><h3>Projects</h3><p style="color:var(--text-muted);">Track deadlines and progress</p></div>
        <div class="card" style="text-align:center;"><div style="font-size:40px;margin-bottom:8px;">💰</div><h3>Income</h3><p style="color:var(--text-muted);">All sources in one place</p></div>
        <div class="card" style="text-align:center;"><div style="font-size:40px;margin-bottom:8px;">🪙</div><h3>Crypto</h3><p style="color:var(--text-muted);">Live prices and portfolio</p></div>
        <div class="card" style="text-align:center;"><div style="font-size:40px;margin-bottom:8px;">📊</div><h3>Analytics</h3><p style="color:var(--text-muted);">Insights and export</p></div>
    </div>
    
    <div style="text-align:center;padding:40px 20px;background:var(--bg-secondary);border-radius:24px;border:1px solid var(--border-color);margin-bottom:40px;">
        <h2 style="font-size:28px;font-weight:700;margin-bottom:12px;color:var(--text-primary);">Start for Free. Upgrade Anytime.</h2>
        <p style="color:var(--text-secondary);font-size:16px;max-width:500px;margin:0 auto 20px auto;">R30/month for Premium. Unlimited projects, advanced analytics, and priority support.</p>
        <a href="/signup" class="btn">Sign Up</a>
    </div>
    
    <div style="text-align:center;border-top:1px solid var(--border-color);padding-top:30px;">
        <a href="/privacy" style="color:var(--text-muted);text-decoration:none;margin:0 12px;">Privacy</a>
        <a href="/terms" style="color:var(--text-muted);text-decoration:none;margin:0 12px;">Terms</a>
        <a href="/contact" style="color:var(--text-muted);text-decoration:none;margin:0 12px;">Contact</a>
        <p style="color:var(--text-muted);margin-top:12px;">&copy; 2026 Summit</p>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/privacy')
def privacy():
    lang = 'en'
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            if pref:
                lang = pref.language
    page = f"""
    <h2 style="font-size:28px;font-weight:700;margin-bottom:20px;">{t('privacy_policy', lang)}</h2>
    <div class="card">
        <h3>1. Information We Collect</h3>
        <p style="color:var(--text-secondary);">We collect your email address, full name, and any data you enter (projects, income, crypto, stocks). This data is stored securely and used only to provide the Summit service.</p>
        <h3>2. How We Use Your Data</h3>
        <p style="color:var(--text-secondary);">Your data is used to display your portfolio, track your investments, and improve your experience. We do not sell your data to third parties.</p>
        <h3>3. Data Security</h3>
        <p style="color:var(--text-secondary);">We use industry-standard encryption and hashing to protect your password and personal information.</p>
        <h3>4. Third-Party Services</h3>
        <p style="color:var(--text-secondary);">Summit uses Yahoo Finance for stock data and Etherscan for blockchain verification. These services have their own privacy policies.</p>
        <h3>5. Cookies</h3>
        <p style="color:var(--text-secondary);">We use session cookies to keep you logged in. No tracking cookies are used.</p>
        <h3>6. Contact</h3>
        <p style="color:var(--text-secondary);">If you have any questions, <a href="/contact" style="color:#60a5fa;">contact us</a>.</p>
    </div>
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/terms')
def terms():
    lang = 'en'
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            if pref:
                lang = pref.language
    page = f"""
    <h2 style="font-size:28px;font-weight:700;margin-bottom:20px;">{t('terms_of_service', lang)}</h2>
    <div class="card">
        <h3>1. Acceptance of Terms</h3>
        <p style="color:var(--text-secondary);">By using Summit, you agree to these Terms of Service.</p>
        <h3>2. User Accounts</h3>
        <p style="color:var(--text-secondary);">You are responsible for your account security. Do not share your password.</p>
        <h3>3. User Data</h3>
        <p style="color:var(--text-secondary);">You own your data. You can export or delete it at any time.</p>
        <h3>4. Acceptable Use</h3>
        <p style="color:var(--text-secondary);">Do not use Summit for illegal activities. Do not abuse the platform.</p>
        <h3>5. Disclaimer</h3>
        <p style="color:var(--text-secondary);">Summit provides tracking and information tools. We do not provide financial advice. Always do your own research.</p>
        <h3>6. Changes to Terms</h3>
        <p style="color:var(--text-secondary);">We may update these terms. Continued use means acceptance of the new terms.</p>
        <h3>7. Contact</h3>
        <p style="color:var(--text-secondary);">Questions? <a href="/contact" style="color:#60a5fa;">Contact us</a>.</p>
    </div>
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/contact')
def contact():
    lang = 'en'
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            if pref:
                lang = pref.language
    page = f"""
    <h2 style="font-size:28px;font-weight:700;margin-bottom:20px;">{t('contact_us', lang)}</h2>
    <div class="card">
        <p style="color:var(--text-secondary);font-size:16px;">📧 <strong>Email:</strong> support@summit.app</p>
        <p style="color:var(--text-secondary);font-size:16px;">🌐 <strong>Website:</strong> summit.onrender.com</p>
        <p style="color:var(--text-secondary);font-size:16px;">📍 <strong>{t('address', lang)}:</strong> South Africa</p>
        <hr style="border-color:var(--border-color);margin:20px 0;">
        <p style="color:var(--text-muted);">We aim to respond within 24 hours.</p>
    </div>
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    lang = 'en'
    user_email = ''
    user_name = ''
    
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            if pref:
                lang = pref.language
            user_email = user.email
            user_name = user.full_name
    
    if request.method == 'POST':
        email = request.form.get('email', user_email)
        subject = request.form.get('subject', t('feedback_subject', lang))
        message = request.form.get('message', '')
        
        if not message:
            flash('Please enter a message.', 'danger')
            return render_template_string(render_feedback_page(lang, user_email, user_name), lang=lang, t=t)
        
        try:
            send_feedback_email(email, subject, message, user_name)
            flash(t('thank_you_feedback', lang), 'success')
            return redirect(url_for('feedback'))
        except Exception as e:
            flash(f'Error sending feedback: {str(e)}', 'danger')
    
    return render_template_string(render_feedback_page(lang, user_email, user_name), lang=lang, t=t)

def render_feedback_page(lang, user_email, user_name):
    page = f"""
    <h2 style="font-size:28px;font-weight:700;margin-bottom:20px;">{t('feedback', lang)}</h2>
    <p style="color:var(--text-secondary);margin-bottom:20px;">We value your feedback. Help us make Summit better.</p>
    
    <div class="card" style="max-width:600px;margin:0 auto;">
        <form method="POST">
            <div class="mb-3">
                <label style="color:var(--text-primary);font-weight:500;display:block;margin-bottom:4px;">{t('email', lang)}</label>
                <input type="email" name="email" placeholder="{t('your_email', lang)}" value="{user_email}" required>
            </div>
            <div class="mb-3">
                <label style="color:var(--text-primary);font-weight:500;display:block;margin-bottom:4px;">{t('subject', lang)}</label>
                <input type="text" name="subject" placeholder="{t('feedback_subject', lang)}" value="{t('feedback_subject', lang)}" required>
            </div>
            <div class="mb-3">
                <label style="color:var(--text-primary);font-weight:500;display:block;margin-bottom:4px;">{t('message', lang)}</label>
                <textarea name="message" rows="5" placeholder="Write your feedback here..." required></textarea>
            </div>
            <button type="submit" class="btn" style="width:100%;">{t('send_feedback', lang)}</button>
        </form>
    </div>
    
    <div class="card" style="text-align:center;border-color:var(--border-color);margin-top:16px;">
        <p style="color:var(--text-muted);font-size:14px;">💡 Your feedback helps us improve Summit for everyone.</p>
    </div>
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return page

@app.route('/ads.txt')
def ads_txt():
    return """google.com, pub-XXXXXXXXXXXXX, DIRECT, f08c47fec0942fa0
media.net, YOUR_MEDIA_NET_PUBLISHER_ID, DIRECT, YOUR_MEDIA_NET_VERIFICATION_CODE
"""

# ---------- TELEGRAM WEBHOOK ----------
@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    if not TELEGRAM_TOKEN:
        return jsonify({'status': 'error', 'message': 'Bot not configured'}), 500
    
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'status': 'ok'}), 200
    
    chat_id = data['message']['chat']['id']
    text = data['message'].get('text', '').lower()
    
    if text == '/start':
        reply = "🏔️ Welcome to Summit Bot!\n\nSend /link your_email to connect your account.\nOr /help for commands."
    elif text.startswith('/link'):
        email = text.replace('/link', '').strip()
        if not email:
            reply = "Please provide your email: /link your@email.com"
        else:
            user = User.query.filter_by(email=email).first()
            if user:
                existing = TelegramUser.query.filter_by(chat_id=str(chat_id)).first()
                if existing:
                    existing.user_id = user.id
                else:
                    new_tele = TelegramUser(user_id=user.id, chat_id=str(chat_id))
                    db.session.add(new_tele)
                db.session.commit()
                reply = f"✅ Account linked! Welcome {user.full_name}.\n\nCommands: /portfolio, /referral, /alerts"
            else:
                reply = "❌ No user found with that email."
    elif text == '/portfolio':
        tele_user = TelegramUser.query.filter_by(chat_id=str(chat_id)).first()
        if not tele_user:
            reply = "Please link your account first: /link your@email.com"
        else:
            user = User.query.get(tele_user.user_id)
            total_income = sum(i.amount for i in Income.query.filter_by(user_id=user.id).all())
            total_crypto = sum(c.value_zar for c in Crypto.query.filter_by(user_id=user.id).all())
            total_stocks = 0
            for s in Stock.query.filter_by(user_id=user.id).all():
                price = get_stock_price(s.symbol)
                if price:
                    total_stocks += price * s.shares
            total_liab = sum(l.amount for l in Liability.query.filter_by(user_id=user.id).all())
            net_worth = total_income + total_crypto + total_stocks - total_liab
            reply = f"🏔️ *Summit Portfolio*\n\n💰 Income: R{total_income:.2f}\n₿ Crypto: R{total_crypto:.2f}\n📈 Stocks: R{total_stocks:.2f}\n💳 Liabilities: R{total_liab:.2f}\n📊 Net Worth: R{net_worth:.2f}"
    elif text == '/referral':
        tele_user = TelegramUser.query.filter_by(chat_id=str(chat_id)).first()
        if not tele_user:
            reply = "Please link your account first: /link your@email.com"
        else:
            user = User.query.get(tele_user.user_id)
            base_url = request.url_root.rstrip('/')
            reply = f"🔗 Your referral link:\n{base_url}/signup?ref={user.referral_code}\n\nShare it to earn free Premium!"
    elif text == '/help':
        reply = "Commands:\n/link email - Link your Summit account\n/portfolio - View your portfolio\n/referral - Get your referral link\n/help - This message"
    else:
        reply = "Unknown command. Try /help"
    
    send_telegram_message(str(chat_id), reply)
    return jsonify({'status': 'ok'}), 200

# ---------- AUTH ROUTES ----------
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    lang = 'en'
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        errors = []
        if len(full_name) < 2:
            errors.append('Full name must be at least 2 characters')
        if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
            errors.append('Invalid email')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters')
        if password != confirm:
            errors.append('Passwords do not match')
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered')
        if errors:
            for e in errors:
                flash(e, 'danger')
            login_url = url_for('login')
            page = f"""
            <div style="max-width:420px;margin:0 auto;">
                <div style="text-align:center;margin-bottom:24px;"><h2 style="font-size:28px;font-weight:700;color:var(--text-primary);">{t('create_account', lang)}</h2><p class="text-muted">{t('your_tech_empire', lang)}</p></div>
                <div class="card">
                    <form method="POST">
                        <input type="text" name="full_name" placeholder="{t('full_name', lang)}" value="{full_name}">
                        <input type="email" name="email" placeholder="{t('email', lang)}" value="{email}">
                        <input type="password" name="password" placeholder="{t('password', lang)}">
                        <input type="password" name="confirm_password" placeholder="{t('confirm_password', lang)}">
                        <button type="submit" class="btn" style="width:100%;">{t('signup', lang)}</button>
                    </form>
                    <p class="text-muted text-center mt-10" style="font-size:14px;">{t('already_have_account', lang)} <a href="{login_url}" style="color:#60a5fa;text-decoration:none;">{t('login', lang)}</a></p>
                </div>
            </div>
            """
            return render_template_string(BASE_HTML, content=page, lang=lang, t=t)
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        new_user = User(
            full_name=full_name,
            email=email,
            password=hashed,
            referral_code=generate_referral_code()
        )
        ref_code = request.args.get('ref')
        if ref_code:
            referrer = User.query.filter_by(referral_code=ref_code).first()
            if referrer:
                new_user.referred_by_id = referrer.id
                referrer.referral_count = User.query.filter_by(referred_by_id=referrer.id).count() + 1
                flash("🎉 You were referred by a friend! You both get rewards!", "success")
        db.session.add(new_user)
        db.session.commit()
        pref = UserPreference(user_id=new_user.id, currency='ZAR', language='en')
        db.session.add(pref)
        db.session.commit()
        flash('Account created! Please login.', 'success')
        return redirect(url_for('login'))
    login_url = url_for('login')
    page = f"""
    <div style="max-width:420px;margin:0 auto;">
        <div style="text-align:center;margin-bottom:24px;"><h2 style="font-size:28px;font-weight:700;color:var(--text-primary);">{t('create_account', lang)}</h2><p class="text-muted">{t('your_tech_empire', lang)}</p></div>
        <div class="card">
            <form method="POST">
                <input type="text" name="full_name" placeholder="{t('full_name', lang)}" required>
                <input type="email" name="email" placeholder="{t('email', lang)}" required>
                <input type="password" name="password" placeholder="{t('password', lang)}" required>
                <input type="password" name="confirm_password" placeholder="{t('confirm_password', lang)}" required>
                <button type="submit" class="btn" style="width:100%;">{t('signup', lang)}</button>
            </form>
            <p class="text-muted text-center mt-10" style="font-size:14px;">{t('already_have_account', lang)} <a href="{login_url}" style="color:#60a5fa;text-decoration:none;">{t('login', lang)}</a></p>
        </div>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    lang = 'en'
    
    twofa_user_id = session.get('2fa_user_id')
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and bcrypt.checkpw(password.encode(), user.password.encode()):
            if user.is_2fa_enabled and user.totp_secret:
                session['2fa_user_id'] = user.id
                flash('Please enter your 2FA code.', 'info')
                return redirect(url_for('login_2fa'))
            else:
                session['user_id'] = user.id
                session['user_name'] = user.full_name
                pref = UserPreference.query.filter_by(user_id=user.id).first()
                if pref:
                    lang = pref.language
                flash('Welcome back!', 'success')
                return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'danger')
    
    signup_url = url_for('signup')
    page = f"""
    <div style="max-width:420px;margin:0 auto;">
        <div style="text-align:center;margin-bottom:24px;"><h2 style="font-size:28px;font-weight:700;color:var(--text-primary);">{t('welcome', lang)}</h2><p class="text-muted">{t('your_tech_empire', lang)}</p></div>
        <div class="card">
            <form method="POST">
                <input type="email" name="email" placeholder="{t('email', lang)}" required>
                <input type="password" name="password" placeholder="{t('password', lang)}" required>
                <button type="submit" class="btn" style="width:100%;">{t('login', lang)}</button>
            </form>
            <p class="text-muted text-center mt-10" style="font-size:14px;">{t('no_account', lang)} <a href="{signup_url}" style="color:#60a5fa;text-decoration:none;">{t('signup', lang)}</a></p>
        </div>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/login/2fa', methods=['GET', 'POST'])
def login_2fa():
    if '2fa_user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['2fa_user_id'])
    if not user:
        session.pop('2fa_user_id', None)
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        totp = pyotp.TOTP(user.totp_secret)
        
        if totp.verify(code):
            session['user_id'] = user.id
            session['user_name'] = user.full_name
            session.pop('2fa_user_id', None)
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid 2FA code. Please try again.', 'danger')
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    
    page = f"""
    <div style="max-width:420px;margin:0 auto;">
        <div style="text-align:center;margin-bottom:24px;">
            <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);">🔐 2FA Verification</h2>
            <p class="text-muted">Enter the code from your authenticator app.</p>
        </div>
        <div class="card">
            <form method="POST">
                <input type="text" name="code" placeholder="6-digit code" maxlength="6" required>
                <button type="submit" class="btn" style="width:100%;">Verify</button>
            </form>
        </div>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

# ---------- DASHBOARD ----------
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    check_monthly_reports()
    process_recurring_transactions(user.id)
    
    triggered = check_price_alerts(user.id)
    if triggered:
        for msg in triggered:
            flash(f"🔔 Price Alert: {msg}", 'success')
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    if not pref:
        pref = UserPreference(user_id=user.id, currency='ZAR', language='en')
        db.session.add(pref)
        db.session.commit()
    currency = pref.currency
    lang = pref.language
    currency_symbol = get_currency_symbol(currency)
    
    projects = Project.query.filter_by(user_id=user.id).all()
    incomes = Income.query.filter_by(user_id=user.id).all()
    cryptos = Crypto.query.filter_by(user_id=user.id).all()
    stocks = Stock.query.filter_by(user_id=user.id).all()
    expenses = Expense.query.filter_by(user_id=user.id).all()
    liabilities = Liability.query.filter_by(user_id=user.id).all()
    
    total_projects = len(projects)
    total_income_zar = sum(i.amount for i in incomes)
    total_expenses_zar = sum(e.amount for e in expenses)
    total_crypto_zar = sum(c.value_zar for c in cryptos)
    total_liabilities_zar = sum(l.amount for l in liabilities)
    total_stock_value_zar = 0
    total_dividend_income_zar = 0
    for s in stocks:
        price = get_stock_price(s.symbol)
        if price:
            total_stock_value_zar += price * s.shares
            if s.dividend_yield:
                total_dividend_income_zar += (price * s.shares * (s.dividend_yield / 100))
    
    total_income = convert_currency(total_income_zar, currency)
    total_expenses = convert_currency(total_expenses_zar, currency)
    total_crypto = convert_currency(total_crypto_zar, currency)
    total_liabilities = convert_currency(total_liabilities_zar, currency)
    total_stock_value = convert_currency(total_stock_value_zar, currency)
    total_dividend_income = convert_currency(total_dividend_income_zar, currency)
    
    total_assets = total_income + total_crypto + total_stock_value
    net_worth = total_assets - total_liabilities
    net_savings = total_income - total_expenses
    
    is_premium = user.is_premium and (user.premium_until is None or user.premium_until > datetime.utcnow())
    is_owner = user.is_owner
    days_left = (user.premium_until - datetime.utcnow()).days if user.premium_until else 0

    pie_labels = json.dumps(['Income', 'Crypto', 'Stocks'])
    pie_values = json.dumps([total_income, total_crypto, total_stock_value])
    pie_colors = json.dumps(['#22c55e', '#60a5fa', '#8b5cf6'])

    recent_projects = ""
    if projects:
        for p in projects[:5]:
            color = "#22c55e" if p.progress >= 100 else "#facc15" if p.progress > 0 else "#6b7280"
            due_text = f"Due: {p.due_date}" if p.due_date else ""
            recent_projects += f"""
            <li style="padding:10px 0;border-bottom:1px solid var(--border-color);">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span>{p.name} <span class="badge badge-{p.status}">{p.status}</span></span>
                    <span style="font-size:12px;color:var(--text-muted);">{p.progress}%</span>
                </div>
                <div class="progress-bar"><div class="fill" style="width:{p.progress}%;background:{color};"></div></div>
                <div style="font-size:11px;color:var(--text-muted);">{due_text}</div>
            </li>
            """
    else:
        recent_projects = f'<p class="text-muted text-sm">{t("no_projects", lang)} <a href="/projects" style="color:#60a5fa;text-decoration:none;">{t("add_project", lang)}</a></p>'

    recent_incomes = ""
    if incomes:
        for i in incomes[:5]:
            recent_incomes += f'<li style="padding:10px 0;border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;"><span>{i.source}</span><span style="color:#22c55e;">+{currency_symbol}{convert_currency(i.amount, currency):.2f}</span></li>'
    else:
        recent_incomes = f'<p class="text-muted text-sm">{t("no_income", lang)} <a href="/income" style="color:#60a5fa;text-decoration:none;">{t("add_income", lang)}</a></p>'

    plan_badge = '<span class="badge badge-premium">Premium</span>' if is_premium else '<span class="badge badge-free">Free</span>'
    premium_note = f'<p class="text-muted text-xs">{days_left} days remaining</p>' if is_premium and days_left > 0 else ''
    owner_badge = '<span class="owner-badge">👑 Owner</span>' if is_owner else ''

    avatar_url = f"https://ui-avatars.com/api/?name={user.full_name}&background={user.avatar_color}&color=fff&size=40"

    share_card_html = f"""
    <div id="share-card" class="share-card" style="display:none;">
        <h1>🏔️ Summit</h1>
        <div class="gain">+R1,240</div>
        <div class="sub">Total Portfolio Gain • Last 30 Days</div>
        <div class="stats">
            <div><div class="num">{currency_symbol}{total_stock_value:.2f}</div><div class="sub">Stocks</div></div>
            <div><div class="num">{currency_symbol}{total_crypto:.2f}</div><div class="sub">Crypto</div></div>
            <div><div class="num">{total_projects}</div><div class="sub">Projects</div></div>
            <div><div class="num">{currency_symbol}{total_income:.2f}</div><div class="sub">Income</div></div>
        </div>
        <div class="sub">START FREE at summit.onrender.com</div>
    </div>
    """

    page = f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;margin-bottom:8px;">
        <div>
            <div style="display:flex;align-items:center;gap:12px;">
                <img src="{avatar_url}" style="border-radius:50%; width:48px; height:48px; border:2px solid #3b82f6;">
                <div>
                    <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);">{t("welcome_user", lang).format(name=user.full_name)} {owner_badge}</h2>
                    <p class="text-muted text-sm">{t("your_tech_empire", lang)}</p>
                </div>
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:12px;">{plan_badge}{premium_note}</div>
    </div>
    
    <div class="grid">
        <div class="stat"><h2>{total_projects}</h2><p>{t("projects", lang)}</p></div>
        <div class="stat"><h2>{currency_symbol}{total_income:.2f}</h2><p>{t("income", lang)}</p></div>
        <div class="stat"><h2>{currency_symbol}{total_crypto:.2f}</h2><p>{t("crypto", lang)}</p></div>
        <div class="stat"><h2>{currency_symbol}{total_stock_value:.2f}</h2><p>{t("stocks", lang)}</p></div>
        <div class="stat" style="border-color:rgba(239,68,68,0.3);"><h2 style="color:#ef4444;">{currency_symbol}{total_liabilities:.2f}</h2><p>Liabilities</p></div>
        <div class="stat" style="border-color:rgba(34,197,94,0.3);"><h2 style="color:#22c55e;">{currency_symbol}{net_worth:.2f}</h2><p>Net Worth</p></div>
        <div class="stat" style="border-color:{'rgba(245,158,11,0.15)' if is_premium else 'var(--border-color)'};"><h2 style="color:{'#f59e0b' if is_premium else 'var(--text-muted)'};">{'Premium' if is_premium else 'Free'}</h2><p>{t("plan", lang)}</p></div>
        {f'<div class="stat"><h2 style="color:#facc15;">{currency_symbol}{total_dividend_income:.2f}</h2><p>Annual Dividends</p></div>' if total_dividend_income > 0 else ''}
    </div>

    <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
        <div class="card" style="display: flex; flex-direction: column; justify-content: center;">
            <h3>Portfolio Allocation</h3>
            <canvas id="portfolioChart" style="max-height: 200px; max-width: 200px; margin: 0 auto;"></canvas>
        </div>
        <div class="card" style="display: flex; flex-direction: column; justify-content: center;">
            <h3>Snapshot</h3>
            <div style="display: flex; flex-direction: column; gap: 12px;">
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
                    <span>💰 Income</span>
                    <span style="color: #22c55e; font-weight: 600;">{currency_symbol}{total_income:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
                    <span>📉 Expenses</span>
                    <span style="color: #ef4444; font-weight: 600;">{currency_symbol}{total_expenses:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
                    <span>₿ Crypto</span>
                    <span style="color: #60a5fa; font-weight: 600;">{currency_symbol}{total_crypto:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
                    <span>📈 Stocks</span>
                    <span style="color: #8b5cf6; font-weight: 600;">{currency_symbol}{total_stock_value:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; padding-top: 8px; border-top: 1px solid var(--border-color);">
                    <span style="font-weight: 700;">Net Worth</span>
                    <span style="color: {'#22c55e' if net_worth >= 0 else '#ef4444'}; font-weight: 700;">{currency_symbol}{net_worth:.2f}</span>
                </div>
            </div>
        </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
        <div class="card"><div class="flex"><h3>{t("recent_activity", lang)}</h3><a href="{url_for('projects')}" class="btn btn-ghost" style="padding:6px 14px;font-size:12px;">{t("projects", lang)}</a></div><ul style="list-style:none;padding:0;">{recent_projects}</ul></div>
        <div class="card"><div class="flex"><h3>{t("recent_activity", lang)}</h3><a href="{url_for('income')}" class="btn btn-ghost" style="padding:6px 14px;font-size:12px;">{t("income", lang)}</a></div><ul style="list-style:none;padding:0;">{recent_incomes}</ul></div>
    </div>

    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">
        <button onclick="shareProgress()" class="btn" style="background:linear-gradient(135deg, #8b5cf6, #ec4899);">📤 Share My Progress</button>
        <a href="/export/pdf" class="btn btn-ghost">📄 Export to PDF</a>
    </div>

    {f'''
    <div class="card" style="text-align:center;margin-top:20px;border-color:rgba(245,158,11,0.15);">
        <p style="font-size:14px;color:var(--text-secondary);">{t("current_plan", lang)}: <strong>{t("free", lang)}</strong></p>
        <a href="{url_for('upgrade')}" class="btn" style="margin-top:8px;">{t("upgrade_now", lang)} – R30/month</a>
    </div>
    ''' if not is_premium else ''}

    {share_card_html}
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {{
            var ctx = document.getElementById('portfolioChart');
            if (!ctx) return;
            
            var data = {pie_values};
            var labels = {pie_labels};
            var colors = {pie_colors};
            
            if (data.reduce((a, b) => a + b, 0) > 0) {{
                new Chart(ctx, {{
                    type: 'pie',
                    data: {{
                        labels: labels,
                        datasets: [{{
                            data: data,
                            backgroundColor: colors,
                            borderColor: '#0a0e1a',
                            borderWidth: 3
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {{
                            legend: {{
                                labels: {{ color: '#e8edf5', font: {{ size: 12 }} }}
                            }}
                        }}
                    }}
                }});
            }} else {{
                ctx.style.display = 'none';
                ctx.parentElement.innerHTML = '<p class="text-muted" style="text-align:center; color:var(--text-muted);">Add assets to see your allocation.</p>';
            }}
        }});
    </script>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- LIABILITIES ----------
@app.route('/liabilities', methods=['GET', 'POST'])
def liabilities():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    if request.method == 'POST':
        name = request.form.get('name')
        amount = request.form.get('amount')
        interest_rate = request.form.get('interest_rate')
        notes = request.form.get('notes', '')
        
        if not name or not amount:
            flash('Name and amount are required.', 'danger')
            return redirect(url_for('liabilities'))
        
        try:
            amount = float(amount)
            interest_rate = float(interest_rate) if interest_rate else None
        except ValueError:
            flash('Invalid amount.', 'danger')
            return redirect(url_for('liabilities'))
        
        liability = Liability(
            user_id=user.id,
            name=name,
            amount=amount,
            interest_rate=interest_rate,
            notes=notes
        )
        db.session.add(liability)
        db.session.commit()
        flash('Liability added!', 'success')
        return redirect(url_for('liabilities'))
    
    all_liabilities = Liability.query.filter_by(user_id=user.id).order_by(Liability.date.desc()).all()
    total_zar = sum(l.amount for l in all_liabilities)
    total = convert_currency(total_zar, currency)
    
    table_rows = ""
    for l in all_liabilities:
        interest_display = f"{l.interest_rate}%" if l.interest_rate else "-"
        l_amount = convert_currency(l.amount, currency)
        table_rows += f"""
        <tr>
            <td>{l.name}</td>
            <td style="color:#ef4444;">{currency_symbol}{l_amount:.2f}</td>
            <td>{interest_display}</td>
            <td>{l.notes or "-"}</td>
            <td>{l.date.strftime('%Y-%m-%d')}</td>
        </tr>
        """
    
    page = f"""
    <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">💳 Liabilities</h2>
    
    <div class="grid">
        <div class="stat"><h2 style="color:#ef4444;">{currency_symbol}{total:.2f}</h2><p>Total Liabilities</p></div>
        <div class="stat"><h2 style="color:#22c55e;">{currency_symbol}{total:.2f}</h2><p>Total Debt</p></div>
    </div>
    
    <div class="card">
        <h3>Add Liability</h3>
        <form method="POST">
            <input type="text" name="name" placeholder="Name (e.g. Student Loan, Car Loan)" required>
            <input type="number" step="0.01" name="amount" placeholder="Amount" required>
            <input type="number" step="0.01" name="interest_rate" placeholder="Interest Rate % (optional)">
            <textarea name="notes" placeholder="Notes" rows="2"></textarea>
            <button type="submit" class="btn">Add Liability</button>
        </form>
    </div>
    
    {f'''
    <div class="card">
        <table>
            <thead><tr><th>Name</th><th>Amount</th><th>Interest Rate</th><th>Notes</th><th>Date</th></tr></thead>
            <tbody>{table_rows}</tbody>
        </table>
    </div>
    ''' if all_liabilities else '<p class="text-muted" style="color:var(--text-muted);">No liabilities yet.</p>'}
    
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- PROJECTS ----------
@app.route('/projects', methods=['GET', 'POST'])
def projects():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    all_projects = Project.query.filter_by(user_id=user.id).all()
    is_premium = user.is_premium and (user.premium_until is None or user.premium_until > datetime.utcnow())
    if request.method == 'POST':
        name = request.form.get('name')
        status = request.form.get('status', 'active')
        notes = request.form.get('notes', '')
        due_date = request.form.get('due_date')
        progress = request.form.get('progress', 0)
        if name:
            due_date_obj = None
            if due_date:
                try:
                    due_date_obj = datetime.strptime(due_date, '%Y-%m-%d').date()
                except:
                    pass
            new_project = Project(user_id=user.id, name=name, status=status, notes=notes, due_date=due_date_obj, progress=int(progress) if progress else 0)
            db.session.add(new_project)
            db.session.commit()
            flash('Project added!', 'success')
        else:
            flash('Project name is required', 'danger')
        return redirect(url_for('projects'))
    if not is_premium and len(all_projects) >= 2:
        flash('Free limit: 2 projects. Upgrade to Premium for unlimited!', 'warning')
    table_rows = ""
    for p in all_projects:
        due_text = p.due_date.strftime('%Y-%m-%d') if p.due_date else '-'
        color = "#22c55e" if p.progress >= 100 else "#facc15" if p.progress > 0 else "#6b7280"
        milestone_html = ""
        for m in p.milestones:
            milestone_html += f"""
            <li style="display:flex;align-items:center;gap:8px;padding:4px 0;">
                <form method="POST" action="/milestone/toggle/{m.id}" style="display:inline;">
                    <button type="submit" style="background:none;border:none;cursor:pointer;font-size:18px;">
                        {'✅' if m.is_completed else '⬜'}
                    </button>
                </form>
                <span style="color:{'#22c55e' if m.is_completed else 'var(--text-secondary)'};">
                    {m.name}
                </span>
            </li>
            """
        milestone_html += f"""
        <form method="POST" action="/milestone/add/{p.id}" style="display:flex;gap:8px;margin-top:4px;">
            <input type="text" name="name" placeholder="New milestone..." style="flex:1;padding:6px 10px;font-size:12px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:8px;color:var(--text-primary);">
            <button type="submit" class="btn" style="padding:4px 12px;font-size:12px;">+</button>
        </form>
        """
        table_rows += f"""
        <tr>
            <td><strong>{p.name}</strong></td>
            <td><span class="badge badge-{p.status}">{p.status}</span></td>
            <td><div style="display:flex;align-items:center;gap:8px;"><span style="font-size:12px;color:var(--text-muted);">{p.progress}%</span><div class="progress-bar" style="flex:1;max-width:100px;"><div class="fill" style="width:{p.progress}%;background:{color};"></div></div></div></td>
            <td>{due_text}</td>
            <td>{p.notes or "-"}</td>
            <td><ul style="list-style:none;padding:0;font-size:13px;">{milestone_html}</ul></td>
        </tr>
        """
    upgrade_url = url_for('upgrade')
    can_add = is_premium or len(all_projects) < 2
    page = f"""
    <div class="flex"><h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("projects", lang)}</h2><span class="text-muted text-sm">{len(all_projects)} / {'Unlimited' if is_premium else '2'}</span></div>
    <div class="card"><h3>{t("add_project", lang)}</h3>
    {f'''
    <form method="POST">
        <input type="text" name="name" placeholder="{t("name", lang)}" required>
        <select name="status"><option value="active">Active</option><option value="paused">Paused</option><option value="completed">Completed</option></select>
        <input type="date" name="due_date" placeholder="{t("due_date", lang)}">
        <input type="number" name="progress" placeholder="{t("progress", lang)} (0-100%)" min="0" max="100">
        <textarea name="notes" placeholder="{t("notes", lang)}" rows="3"></textarea>
        <button type="submit" class="btn">{t("add_project", lang)}</button>
    </form>
    ''' if can_add else f'<p class="text-muted text-sm">{t("free_limit", lang)} <a href="{upgrade_url}" style="color:#60a5fa;text-decoration:none;">{t("upgrade", lang)}</a></p>'}
    </div>
    <input type="text" id="searchInput" placeholder="{t("search", lang)}..." onkeyup="filterProjects()" style="margin-bottom:12px;">
    {f'''
    <div class="card"><div class="table-wrapper"><table id="projectTable"><thead><tr><th>{t("name", lang)}</th><th>{t("status", lang)}</th><th>{t("progress", lang)}</th><th>{t("due_date", lang)}</th><th>{t("notes", lang)}</th><th>{t("milestones", lang)}</th></tr></thead><tbody>{table_rows}</tbody></table></div></div>
    ''' if all_projects else '<p class="text-muted text-sm">{t("no_projects", lang)}</p>'}
    <script>function filterProjects(){{var input=document.getElementById('searchInput');var filter=input.value.toLowerCase();var rows=document.querySelectorAll('#projectTable tbody tr');rows.forEach(function(row){{var text=row.textContent.toLowerCase();row.style.display=text.includes(filter)?'':'none';}});}}</script>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- INCOME ----------
@app.route('/income', methods=['GET', 'POST'])
def income():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    if request.method == 'POST':
        source = request.form.get('source')
        amount = request.form.get('amount')
        notes = request.form.get('notes', '')
        is_recurring = request.form.get('is_recurring') == 'on'
        frequency = request.form.get('frequency', 'monthly')
        if source and amount:
            try:
                amount = float(amount)
                new_income = Income(user_id=user.id, source=source, amount=amount, notes=notes, is_recurring=is_recurring, frequency=frequency)
                db.session.add(new_income)
                db.session.commit()
                flash('Income added!', 'success')
            except ValueError:
                flash('Invalid amount', 'danger')
        else:
            flash('Source and amount are required', 'danger')
        return redirect(url_for('income'))
    all_incomes = Income.query.filter_by(user_id=user.id).order_by(Income.date.desc()).all()
    total_zar = sum(i.amount for i in all_incomes)
    total = convert_currency(total_zar, currency)
    table_rows = ""
    for i in all_incomes:
        recurring_badge = '🔄' if i.is_recurring else ''
        i_amount = convert_currency(i.amount, currency)
        table_rows += f'<tr><td>{i.source}</td><td style="color:#22c55e;">{currency_symbol}{i_amount:.2f}</td><td>{i.date.strftime("%Y-%m-%d")}</td><td>{i.notes or "-"}</td><td>{recurring_badge}</td></tr>'
    page = f"""
    <div class="flex"><h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("income", lang)}</h2><span class="text-muted text-sm">{t("total", lang)}: <strong style="color:#22c55e;">{currency_symbol}{total:.2f}</strong></span></div>
    <div class="card"><h3>{t("add_income", lang)}</h3>
    <form method="POST">
        <input type="text" name="source" placeholder="{t("source", lang)}" required>
        <input type="number" step="0.01" name="amount" placeholder="{t("amount", lang)}" required>
        <textarea name="notes" placeholder="{t("notes", lang)}" rows="2"></textarea>
        <div style="display:flex;gap:12px;align-items:center;margin:8px 0;">
            <label style="color:var(--text-secondary);font-size:14px;">
                <input type="checkbox" name="is_recurring"> 🔄 Recurring
            </label>
            <select name="frequency">
                <option value="monthly">Monthly</option>
                <option value="yearly">Yearly</option>
            </select>
        </div>
        <button type="submit" class="btn">{t("add_income", lang)}</button>
    </form></div>
    {f'''
    <div class="card"><table><thead><tr><th>{t("source", lang)}</th><th>{t("amount", lang)}</th><th>{t("date", lang)}</th><th>{t("notes", lang)}</th><th>Recurring</th></tr></thead><tbody>{table_rows}</tbody></table></div>
    ''' if all_incomes else '<p class="text-muted text-sm">{t("no_income", lang)}</p>'}
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- CRYPTO ----------
@app.route('/crypto', methods=['GET', 'POST'])
def crypto():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    if request.method == 'POST':
        coin_name = request.form.get('coin_name')
        amount = request.form.get('amount')
        value_zar = request.form.get('value_zar')
        if coin_name and amount and value_zar:
            try:
                amount = float(amount)
                value_zar = float(value_zar)
                new_crypto = Crypto(user_id=user.id, coin_name=coin_name, amount=amount, value_zar=value_zar)
                db.session.add(new_crypto)
                db.session.commit()
                flash('Crypto added!', 'success')
            except ValueError:
                flash('Invalid amount or value', 'danger')
        else:
            flash('All fields are required', 'danger')
        return redirect(url_for('crypto'))
    all_cryptos = Crypto.query.filter_by(user_id=user.id).all()
    total_zar = sum(c.value_zar for c in all_cryptos)
    total = convert_currency(total_zar, currency)
    table_rows = ""
    for c in all_cryptos:
        c_value = convert_currency(c.value_zar, currency)
        table_rows += f'<tr><td><strong>{c.coin_name}</strong></td><td>{c.amount}</td><td style="color:#60a5fa;">{currency_symbol}{c_value:.2f}</td></tr>'
    page = f"""
    <div class="flex"><h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("crypto", lang)}</h2><span class="text-muted text-sm">{t("total", lang)}: <strong style="color:#60a5fa;">{currency_symbol}{total:.2f}</strong></span></div>
    <div class="card"><h3>{t("add_crypto", lang)}</h3><form method="POST"><input type="text" name="coin_name" placeholder="{t("coin", lang)}" required><input type="number" step="0.000001" name="amount" placeholder="{t("amount", lang)}" required><input type="number" step="0.01" name="value_zar" placeholder="{t("value", lang)} (ZAR)" required><button type="submit" class="btn">{t("add_crypto", lang)}</button></form></div>
    {f'''
    <div class="card"><table><thead><tr><th>{t("coin", lang)}</th><th>{t("amount", lang)}</th><th>{t("value", lang)}</th></tr></thead><tbody>{table_rows}</tbody></table></div>
    ''' if all_cryptos else '<p class="text-muted text-sm">{t("no_crypto", lang)}</p>'}
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- STOCKS ----------
@app.route('/stocks', methods=['GET', 'POST'])
def stocks():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    if request.method == 'POST':
        symbol = request.form.get('symbol', '').upper().strip()
        shares = request.form.get('shares')
        purchase_price = request.form.get('purchase_price')
        dividend_yield = request.form.get('dividend_yield')
        if not symbol or not shares:
            flash('Symbol and shares are required', 'danger')
            return redirect(url_for('stocks'))
        try:
            shares = float(shares)
            purchase_price = float(purchase_price) if purchase_price else None
            dividend_yield = float(dividend_yield) if dividend_yield else None
        except ValueError:
            flash('Invalid number format', 'danger')
            return redirect(url_for('stocks'))
        if not get_stock_info(symbol):
            flash(f'Could not find stock symbol: {symbol}', 'danger')
            return redirect(url_for('stocks'))
        new_stock = Stock(user_id=user.id, symbol=symbol, shares=shares, purchase_price=purchase_price, notes=request.form.get('notes', ''), dividend_yield=dividend_yield)
        db.session.add(new_stock)
        db.session.commit()
        flash(f'Added {symbol} to your portfolio!', 'success')
        return redirect(url_for('stocks'))
    
    try:
        all_stocks = Stock.query.filter_by(user_id=user.id).all()
        total_value_zar = 0
        total_cost_zar = 0
        total_dividend_zar = 0
        table_rows = ""
        for stock in all_stocks:
            price = get_stock_price(stock.symbol)
            current_value_zar = price * stock.shares if price else 0
            total_value_zar += current_value_zar
            if stock.purchase_price:
                total_cost_zar += stock.purchase_price * stock.shares
            if stock.dividend_yield and price:
                total_dividend_zar += (price * stock.shares * (stock.dividend_yield / 100))
            gain_zar = (current_value_zar - (stock.purchase_price * stock.shares)) if stock.purchase_price else None
            gain_color = "#22c55e" if gain_zar and gain_zar > 0 else "#ef4444" if gain_zar and gain_zar < 0 else "var(--text-muted)"
            gain_text = f"+{currency_symbol}{convert_currency(gain_zar, currency):.2f}" if gain_zar and gain_zar > 0 else f"-{currency_symbol}{abs(convert_currency(gain_zar, currency)):.2f}" if gain_zar and gain_zar < 0 else "—"
            price_display = f"{currency_symbol}{price:.2f}" if price else "—"
            value_display = f"{currency_symbol}{convert_currency(current_value_zar, currency):.2f}" if price else "—"
            div_display = f"{stock.dividend_yield}%" if stock.dividend_yield else "-"
            table_rows += f"""
            <tr>
                <td><strong>{stock.symbol}</strong></td>
                <td>{stock.shares}</td>
                <td>{price_display}</td>
                <td>{value_display}</td>
                <td style="color:{gain_color};">{gain_text if price else '—'}</td>
                <td>{div_display}</td>
                <td>{stock.notes or "-"}</td>
            </tr>
            """
        total_value = convert_currency(total_value_zar, currency)
        total_cost = convert_currency(total_cost_zar, currency)
        total_dividend = convert_currency(total_dividend_zar, currency)
        total_gain = total_value - total_cost if total_cost > 0 else 0
        total_gain_display = f"+{currency_symbol}{total_gain:.2f}" if total_gain > 0 else f"-{currency_symbol}{abs(total_gain):.2f}" if total_gain < 0 else f"{currency_symbol}0.00"
        total_gain_color = "#22c55e" if total_gain > 0 else "#ef4444" if total_gain < 0 else "var(--text-muted)"
        
        page = f"""
        <div class="flex" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
            <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("stocks", lang)}</h2>
            <span class="text-muted" style="color:var(--text-muted);">{t("total", lang)}: <strong style="color:#60a5fa;">{currency_symbol}{total_value:.2f}</strong></span>
        </div>
        {f'<div class="card"><p style="color:#facc15;font-weight:600;">💰 Annual Dividend Income: {currency_symbol}{total_dividend:.2f}</p></div>' if total_dividend > 0 else ''}
        <div class="card">
            <h3>{t("add_stock", lang)}</h3>
            <form method="POST">
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;">
                    <input type="text" name="symbol" placeholder="{t("symbol", lang)}" required>
                    <input type="number" step="0.01" name="shares" placeholder="{t("shares", lang)}" required>
                    <input type="number" step="0.01" name="purchase_price" placeholder="{t("purchase_price", lang)}">
                    <input type="number" step="0.01" name="dividend_yield" placeholder="Dividend Yield %">
                </div>
                <textarea name="notes" placeholder="{t("notes", lang)}" rows="2"></textarea>
                <button type="submit" class="btn">{t("add_stock", lang)}</button>
            </form>
        </div>
        {f'''
        <div class="card">
            <table>
                <thead><tr><th>{t("symbol", lang)}</th><th>{t("shares", lang)}</th><th>{t("current_price", lang)}</th><th>{t("value", lang)}</th><th>{t("gain_loss", lang)}</th><th>Dividend Yield</th><th>{t("notes", lang)}</th></tr></thead>
                <tbody>{table_rows}</tbody>
            </table>
            <div style="margin-top:12px;display:flex;justify-content:space-between;">
                <span class="text-muted" style="color:var(--text-muted);">{t("total", lang)} {t("gain_loss", lang)}: <strong style="color:{total_gain_color};">{total_gain_display}</strong></span>
            </div>
        </div>
        ''' if all_stocks else '<p class="text-muted" style="color:var(--text-muted);">{t("no_stocks", lang)}</p>'}
        """
        return render_template_string(BASE_HTML, content=page, lang=lang, t=t)
    except Exception as e:
        flash(f'Error loading stocks: {str(e)}', 'danger')
        page = f"""
        <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("stocks", lang)}</h2>
        <div class="card"><p class="text-muted" style="color:var(--text-muted);">{t("error", lang)}</p></div>
        """
        return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- MARKET ----------
@app.route('/market')
def market():
    data = get_market_data()
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            lang = pref.language if pref else 'en'
            user_stocks = Stock.query.filter_by(user_id=user.id).all()
            watchlist_symbols = [s.symbol for s in user_stocks if s.is_watchlisted]
        else:
            lang = 'en'
            watchlist_symbols = []
    else:
        lang = 'en'
        watchlist_symbols = []
    
    filter_type = request.args.get('filter', 'all')
    table_rows = ""
    for stock in data['stocks']:
        if filter_type == 'watchlist' and stock['symbol'] not in watchlist_symbols:
            continue
        price_display = f"R{stock['price']:.2f}" if stock['price'] else "—"
        is_watchlisted = stock['symbol'] in watchlist_symbols
        watch_icon = "⭐" if is_watchlisted else "☆"
        toggle_url = url_for('toggle_watchlist', symbol=stock['symbol'])
        table_rows += f"""
        <tr>
            <td><form method="POST" action="{toggle_url}" style="display:inline;"><button type="submit" style="background:none;border:none;cursor:pointer;font-size:1.5rem;color:var(--text-primary);">{watch_icon}</button></form></td>
            <td><strong>{stock['symbol']}</strong></td>
            <td>{stock['name']}</td>
            <td>{price_display}</td>
        </tr>
        """
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("market", lang)}</h2>
    <p class="text-muted" style="color:var(--text-muted);">{t("live_prices", lang)} (cached 60s)</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
        <a href="?filter=all" class="btn btn-ghost" style="padding:6px 14px;font-size:12px;">📈 All</a>
        <a href="?filter=watchlist" class="btn btn-ghost" style="padding:6px 14px;font-size:12px;">⭐ {t('watchlist', lang)}</a>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px;">
        <a href="/crypto" class="btn btn-ghost" style="text-align:center;">{t("crypto_prices", lang)}</a>
        <a href="/forex" class="btn btn-ghost" style="text-align:center;">{t("forex_rates", lang)}</a>
        <a href="/top-movers" class="btn btn-ghost" style="text-align:center;">{t("top_movers", lang)}</a>
        <a href="/index-tracker" class="btn btn-ghost" style="text-align:center;">{t("index_tracker", lang)}</a>
    </div>
    <div class="card">
        <h3>Set Price Alert</h3>
        <form method="POST" action="/alert/add" style="display:flex;gap:8px;flex-wrap:wrap;">
            <input type="text" name="symbol" placeholder="Symbol (e.g. AAPL)" style="flex:1;min-width:120px;" required>
            <input type="number" step="0.01" name="target_price" placeholder="Target Price" style="flex:1;min-width:120px;" required>
            <select name="condition" style="flex:0.5;min-width:80px;">
                <option value="above">{t('above', lang)}</option>
                <option value="below">{t('below', lang)}</option>
            </select>
            <button type="submit" class="btn" style="padding:6px 16px;">{t('set_alert', lang)}</button>
        </form>
    </div>
    <div class="card">
        <table><thead><tr><th>⭐</th><th>{t("symbol", lang)}</th><th>{t("name", lang)}</th><th>{t("price", lang)}</th></tr></thead><tbody>{table_rows}</tbody></table>
        <p class="text-muted text-xs mt-10" style="color:var(--text-muted);">{t("loading", lang)}</p>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- CRYPTO PRICES ----------
@app.route('/crypto')
def crypto_prices():
    data = get_market_data()
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            lang = pref.language if pref else 'en'
        else:
            lang = 'en'
    else:
        lang = 'en'
    table_rows = ""
    for c in data['crypto']:
        change_class = "green" if c['change'] >= 0 else "red"
        table_rows += f"<tr><td><strong>{c['symbol']}</strong></td><td>${c['price']:.2f}</td><td class='{change_class}'>{c['change']:+.2f}%</td></tr>"
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("crypto_prices", lang)}</h2>
    <div class="card"><table><thead><tr><th>{t("symbol", lang)}</th><th>{t("price", lang)}</th><th>{t("change", lang)}</th></tr></thead><tbody>{table_rows}</tbody></table></div>
    <a href="/market" class="btn btn-ghost mt-10">{t("back", lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- FOREX ----------
@app.route('/forex')
def forex_rates():
    data = get_market_data()
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            lang = pref.language if pref else 'en'
        else:
            lang = 'en'
    else:
        lang = 'en'
    table_rows = ""
    for f in data['forex']:
        table_rows += f"<tr><td><strong>{f['pair'][:3]}/{f['pair'][3:]}</strong></td><td>{f['price']:.4f}</td></tr>"
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("forex_rates", lang)}</h2>
    <div class="card"><table><thead><tr><th>Pair</th><th>Rate</th></tr></thead><tbody>{table_rows}</tbody></table></div>
    <a href="/market" class="btn btn-ghost mt-10">{t("back", lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- TOP MOVERS ----------
@app.route('/top-movers')
def top_movers():
    data = get_market_data()
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            lang = pref.language if pref else 'en'
        else:
            lang = 'en'
    else:
        lang = 'en'
    movers = []
    for stock in data['stocks']:
        if stock['price']:
            try:
                ticker = yf.Ticker(stock['symbol'])
                hist = ticker.history(period="2d")
                if len(hist) >= 2:
                    prev = hist['Close'].iloc[-2]
                    curr = hist['Close'].iloc[-1]
                    change = ((curr - prev) / prev) * 100 if prev != 0 else 0
                    movers.append({'symbol': stock['symbol'], 'price': curr, 'change': change})
            except:
                pass
    movers.sort(key=lambda x: x['change'], reverse=True)
    top_gainers = movers[:5]
    top_losers = movers[-5:][::-1]
    gainer_rows = ""
    for g in top_gainers:
        gainer_rows += f"<tr><td><strong>{g['symbol']}</strong></td><td>${g['price']:.2f}</td><td class='green'>+{g['change']:.2f}%</td></tr>"
    loser_rows = ""
    for l in top_losers:
        loser_rows += f"<tr><td><strong>{l['symbol']}</strong></td><td>${l['price']:.2f}</td><td class='red'>{l['change']:.2f}%</td></tr>"
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("top_movers", lang)}</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
        <div class="card"><h3 style="color:#22c55e;">📈 {t("gain", lang)}</h3><table><thead><tr><th>{t("symbol", lang)}</th><th>{t("price", lang)}</th><th>{t("change", lang)}</th></tr></thead><tbody>{gainer_rows}</tbody></table></div>
        <div class="card"><h3 style="color:#ef4444;">📉 {t("loss", lang)}</h3><table><thead><tr><th>{t("symbol", lang)}</th><th>{t("price", lang)}</th><th>{t("change", lang)}</th></tr></thead><tbody>{loser_rows}</tbody></table></div>
    </div>
    <a href="/market" class="btn btn-ghost mt-10">{t("back", lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- INDEX TRACKER ----------
@app.route('/index-tracker')
def index_tracker():
    data = get_market_data()
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            lang = pref.language if pref else 'en'
        else:
            lang = 'en'
    else:
        lang = 'en'
    table_rows = ""
    for idx in data['indices']:
        color = "#22c55e" if idx['change'] >= 0 else "#ef4444"
        table_rows += f"<tr><td><strong>{idx['name']}</strong></td><td>{idx['price']:.2f}</td><td style='color:{color};'>{idx['change']:+.2f}%</td></tr>"
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);">{t("index_tracker", lang)}</h2>
    <div class="card"><table><thead><tr><th>{t("name", lang)}</th><th>{t("price", lang)}</th><th>{t("change", lang)}</th></tr></thead><tbody>{table_rows}</tbody></table></div>
    <a href="/market" class="btn btn-ghost mt-10">{t("back", lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- ANALYTICS ----------
@app.route('/analytics')
def analytics():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    projects = Project.query.filter_by(user_id=user.id).all()
    incomes = Income.query.filter_by(user_id=user.id).all()
    cryptos = Crypto.query.filter_by(user_id=user.id).all()
    stocks = Stock.query.filter_by(user_id=user.id).all()
    expenses = Expense.query.filter_by(user_id=user.id).all()
    liabilities = Liability.query.filter_by(user_id=user.id).all()
    status_counts = {'active': 0, 'paused': 0, 'completed': 0}
    total_progress = 0
    for p in projects:
        status_counts[p.status] += 1
        total_progress += p.progress
    avg_progress = total_progress / len(projects) if projects else 0
    total_income_zar = sum(i.amount for i in incomes)
    total_expenses_zar = sum(e.amount for e in expenses)
    total_crypto_zar = sum(c.value_zar for c in cryptos)
    total_liabilities_zar = sum(l.amount for l in liabilities)
    total_stock_zar = 0
    for s in stocks:
        price = get_stock_price(s.symbol)
        if price:
            total_stock_zar += price * s.shares
    total_income = convert_currency(total_income_zar, currency)
    total_expenses = convert_currency(total_expenses_zar, currency)
    total_crypto = convert_currency(total_crypto_zar, currency)
    total_liabilities = convert_currency(total_liabilities_zar, currency)
    total_stock = convert_currency(total_stock_zar, currency)
    net_worth = total_income + total_crypto + total_stock - total_liabilities
    
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">{t("analytics", lang)}</h2>
    <div class="grid">
        <div class="stat"><h2>{len(projects)}</h2><p>{t("projects", lang)}</p></div>
        <div class="stat"><h2>{avg_progress:.0f}%</h2><p>{t("progress", lang)}</p></div>
        <div class="stat"><h2>{currency_symbol}{total_income:.2f}</h2><p>{t("income", lang)}</p></div>
        <div class="stat"><h2>{currency_symbol}{total_crypto:.2f}</h2><p>{t("crypto", lang)}</p></div>
        <div class="stat"><h2>{currency_symbol}{total_stock:.2f}</h2><p>{t("stocks", lang)}</p></div>
        <div class="stat"><h2 style="color:#ef4444;">{currency_symbol}{total_liabilities:.2f}</h2><p>Liabilities</p></div>
        <div class="stat" style="border-color:rgba(34,197,94,0.3);"><h2 style="color:#22c55e;">{currency_symbol}{net_worth:.2f}</h2><p>Net Worth</p></div>
        <div class="stat"><h2 style="color:#ef4444;">{currency_symbol}{total_expenses:.2f}</h2><p>Expenses</p></div>
    </div>
    <div class="card"><h3>{t("status", lang)}</h3>
    <div style="display:flex;gap:20px;flex-wrap:wrap;justify-content:center;margin-top:12px;">
        <div style="background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;padding:20px;flex:1;min-width:150px;text-align:center;"><div style="font-size:36px;font-weight:700;color:#22c55e;">{status_counts['active']}</div><div style="color:var(--text-muted);">{t("active", lang)}</div></div>
        <div style="background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;padding:20px;flex:1;min-width:150px;text-align:center;"><div style="font-size:36px;font-weight:700;color:#facc15;">{status_counts['paused']}</div><div style="color:var(--text-muted);">{t("paused", lang)}</div></div>
        <div style="background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;padding:20px;flex:1;min-width:150px;text-align:center;"><div style="font-size:36px;font-weight:700;color:#60a5fa;">{status_counts['completed']}</div><div style="color:var(--text-muted);">{t("completed", lang)}</div></div>
    </div></div>
    <div class="card"><h3>{t("portfolio_summary", lang)}</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;">
        <div><p class="text-muted text-sm">{t("income", lang)}</p><p style="font-size:24px;font-weight:700;color:#22c55e;">{currency_symbol}{total_income:.2f}</p></div>
        <div><p class="text-muted text-sm">{t("crypto", lang)}</p><p style="font-size:24px;font-weight:700;color:#60a5fa;">{currency_symbol}{total_crypto:.2f}</p></div>
        <div><p class="text-muted text-sm">{t("stocks", lang)}</p><p style="font-size:24px;font-weight:700;color:#8b5cf6;">{currency_symbol}{total_stock:.2f}</p></div>
    </div></div>
    <div class="card"><h3>{t("export_data", lang)}</h3>
    <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <a href="{url_for('export_csv', data_type='projects')}" class="btn btn-ghost">{t("export_csv", lang)}</a>
        <a href="{url_for('export_csv', data_type='income')}" class="btn btn-ghost">{t("export_csv", lang)}</a>
        <a href="{url_for('export_csv', data_type='crypto')}" class="btn btn-ghost">{t("export_csv", lang)}</a>
        <a href="{url_for('export_csv', data_type='stocks')}" class="btn btn-ghost">{t("export_csv", lang)}</a>
    </div></div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- EXPORT CSV ----------
@app.route('/export/<data_type>')
def export_csv(data_type):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    if data_type == 'projects':
        projects = Project.query.filter_by(user_id=user.id).all()
        writer.writerow(['Name', 'Status', 'Progress', 'Due Date', 'Notes', 'Last Updated'])
        for p in projects:
            writer.writerow([p.name, p.status, p.progress, p.due_date or '', p.notes or '', p.last_updated])
        filename = 'summit_projects.csv'
    elif data_type == 'income':
        incomes = Income.query.filter_by(user_id=user.id).all()
        writer.writerow(['Source', 'Amount (R)', 'Date', 'Notes'])
        for i in incomes:
            writer.writerow([i.source, i.amount, i.date, i.notes or ''])
        filename = 'summit_income.csv'
    elif data_type == 'crypto':
        cryptos = Crypto.query.filter_by(user_id=user.id).all()
        writer.writerow(['Coin', 'Amount', 'Value (R)'])
        for c in cryptos:
            writer.writerow([c.coin_name, c.amount, c.value_zar])
        filename = 'summit_crypto.csv'
    elif data_type == 'stocks':
        stocks = Stock.query.filter_by(user_id=user.id).all()
        writer.writerow(['Symbol', 'Shares', 'Purchase Price', 'Dividend Yield', 'Notes', 'Added'])
        for s in stocks:
            writer.writerow([s.symbol, s.shares, s.purchase_price or '', s.dividend_yield or '', s.notes or '', s.created_at])
        filename = 'summit_stocks.csv'
    else:
        flash('Invalid export type', 'danger')
        return redirect(url_for('analytics'))
    output.seek(0)
    return Response(output, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})

# ---------- CHARTS ----------
@app.route('/charts')
def charts():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    cryptos = Crypto.query.filter_by(user_id=user.id).all()
    stocks = Stock.query.filter_by(user_id=user.id).all()
    incomes = Income.query.filter_by(user_id=user.id).all()
    expenses = Expense.query.filter_by(user_id=user.id).all()
    
    crypto_labels = [c.coin_name for c in cryptos]
    crypto_values = [convert_currency(c.value_zar, currency) for c in cryptos]
    
    stock_labels = [s.symbol for s in stocks]
    stock_values = []
    for s in stocks:
        price = get_stock_price(s.symbol)
        if price:
            stock_values.append(convert_currency(price * s.shares, currency))
        else:
            stock_values.append(0)
    
    income_labels = []
    income_values = []
    expense_values = []
    today = datetime.utcnow().date()
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        income_labels.append(date.strftime('%Y-%m-%d'))
        daily_income = sum([convert_currency(inc.amount, currency) for inc in incomes if inc.date == date])
        income_values.append(daily_income)
        daily_expense = sum([convert_currency(exp.amount, currency) for exp in expenses if exp.date == date])
        expense_values.append(daily_expense)
    
    crypto_labels_js = json.dumps(crypto_labels)
    crypto_values_js = json.dumps(crypto_values)
    stock_labels_js = json.dumps(stock_labels)
    stock_values_js = json.dumps(stock_values)
    income_labels_js = json.dumps(income_labels)
    income_values_js = json.dumps(income_values)
    expense_values_js = json.dumps(expense_values)
    
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">{t("charts", lang)}</h2>
    
    <div class="chart-container">
        <div class="chart-box">
            <h4>{t("portfolio", lang)} {t("allocation", lang)}</h4>
            <canvas id="allocationChart"></canvas>
        </div>
        <div class="chart-box">
            <h4>Income vs Expenses</h4>
            <canvas id="incomeExpenseChart"></canvas>
        </div>
    </div>
    
    <div class="chart-container">
        <div class="chart-box">
            <h4>{t("stocks", lang)} {t("value", lang)}</h4>
            <canvas id="stockChart"></canvas>
        </div>
        <div class="chart-box">
            <h4>{t("income", lang)} vs {t("crypto", lang)}</h4>
            <canvas id="comparisonChart"></canvas>
        </div>
    </div>
    
    <div class="card">
        <h3>{t("stock_history", lang)}</h3>
        <p class="text-muted" style="color:var(--text-muted);">{t("select_stock", lang)}</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
            {"".join([f'<button onclick="loadStockHistory(\'{s.symbol}\')" class="btn btn-ghost btn-sm">{s.symbol}</button>' for s in stocks[:10]])}
        </div>
        <div style="position:relative;height:200px;">
            <canvas id="stockHistoryChart"></canvas>
        </div>
    </div>
    
    <script>
        var ctx1 = document.getElementById('allocationChart').getContext('2d');
        new Chart(ctx1, {{
            type: 'pie',
            data: {{
                labels: {crypto_labels_js},
                datasets: [{{
                    data: {crypto_values_js},
                    backgroundColor: ['#3b82f6', '#8b5cf6', '#ec4899', '#22c55e', '#facc15', '#ef4444'],
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ labels: {{ color: '#e8edf5' }} }}
                }}
            }}
        }});
        
        var ctx2 = document.getElementById('incomeExpenseChart').getContext('2d');
        new Chart(ctx2, {{
            type: 'bar',
            data: {{
                labels: {income_labels_js},
                datasets: [
                    {{
                        label: 'Income',
                        data: {income_values_js},
                        backgroundColor: '#22c55e',
                        borderColor: '#22c55e',
                        borderWidth: 1
                    }},
                    {{
                        label: 'Expenses',
                        data: {expense_values_js},
                        backgroundColor: '#ef4444',
                        borderColor: '#ef4444',
                        borderWidth: 1
                    }}
                ]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ labels: {{ color: '#e8edf5' }} }}
                }},
                scales: {{
                    y: {{ ticks: {{ color: '#6b7280' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
                    x: {{ ticks: {{ color: '#6b7280' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}
                }}
            }}
        }});
        
        var ctx3 = document.getElementById('stockChart').getContext('2d');
        new Chart(ctx3, {{
            type: 'bar',
            data: {{
                labels: {stock_labels_js},
                datasets: [{{
                    label: '{t("value", lang)}',
                    data: {stock_values_js},
                    backgroundColor: '#60a5fa',
                    borderColor: '#60a5fa',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ labels: {{ color: '#e8edf5' }} }}
                }},
                scales: {{
                    y: {{ ticks: {{ color: '#6b7280' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
                    x: {{ ticks: {{ color: '#6b7280' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}
                }}
            }}
        }});
        
        var totalIncome = {sum(convert_currency(i.amount, currency) for i in incomes)};
        var totalCrypto = {sum(convert_currency(c.value_zar, currency) for c in cryptos)};
        var ctx4 = document.getElementById('comparisonChart').getContext('2d');
        new Chart(ctx4, {{
            type: 'doughnut',
            data: {{
                labels: ['{t("income", lang)}', '{t("crypto", lang)}'],
                datasets: [{{
                    data: [totalIncome, totalCrypto],
                    backgroundColor: ['#22c55e', '#60a5fa'],
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ labels: {{ color: '#e8edf5' }} }}
                }}
            }}
        }});
        
        var stockCtx = document.getElementById('stockHistoryChart').getContext('2d');
        var stockChartInstance = null;
        
        function loadStockHistory(symbol) {{
            fetch('/stock_history/' + symbol)
                .then(response => response.json())
                .then(data => {{
                    if (stockChartInstance) {{
                        stockChartInstance.destroy();
                    }}
                    stockChartInstance = new Chart(stockCtx, {{
                        type: 'line',
                        data: {{
                            labels: data.dates,
                            datasets: [{{
                                label: symbol + ' Price',
                                data: data.prices,
                                borderColor: '#8b5cf6',
                                backgroundColor: 'rgba(139,92,246,0.1)',
                                fill: true,
                                tension: 0.4
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: {{
                                legend: {{ labels: {{ color: '#e8edf5' }} }}
                            }},
                            scales: {{
                                y: {{ ticks: {{ color: '#6b7280' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
                                x: {{ ticks: {{ color: '#6b7280' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}
                            }}
                        }}
                    }});
                }});
        }}
        
        var firstStock = document.querySelector('.btn-sm');
        if (firstStock) {{
            loadStockHistory(firstStock.textContent.trim());
        }}
    </script>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/stock_history/<symbol>')
def stock_history(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1mo")
        dates = hist.index.strftime('%Y-%m-%d').tolist()
        prices = hist['Close'].tolist()
        return jsonify({'dates': dates, 'prices': prices})
    except:
        return jsonify({'dates': [], 'prices': []})

# ---------- SETTINGS ----------
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    if not pref:
        pref = UserPreference(user_id=user.id, currency='ZAR', language='en')
        db.session.add(pref)
        db.session.commit()
    lang = pref.language
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_profile':
            full_name = request.form.get('full_name')
            email = request.form.get('email')
            avatar_color = request.form.get('avatar_color')
            if full_name and email:
                existing = User.query.filter(User.email == email, User.id != user.id).first()
                if existing:
                    flash('Email already taken', 'danger')
                else:
                    user.full_name = full_name
                    user.email = email
                    if avatar_color:
                        user.avatar_color = avatar_color
                    db.session.commit()
                    session['user_name'] = full_name
                    flash('Profile updated!', 'success')
        elif action == 'change_password':
            current = request.form.get('current_password')
            new = request.form.get('new_password')
            confirm = request.form.get('confirm_password')
            if bcrypt.checkpw(current.encode(), user.password.encode()):
                if new == confirm and len(new) >= 6:
                    user.password = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
                    db.session.commit()
                    flash('Password changed!', 'success')
                else:
                    flash('New password must match and be at least 6 characters', 'danger')
            else:
                flash('Current password is incorrect', 'danger')
        elif action == 'update_preferences':
            currency = request.form.get('currency')
            language = request.form.get('language')
            pref.currency = currency
            pref.language = language
            db.session.commit()
            flash('Preferences updated!', 'success')
        elif action == 'setup_2fa':
            secret = generate_totp_secret()
            user.totp_secret = secret
            db.session.commit()
            totp = pyotp.TOTP(secret)
            provisioning_uri = totp.provisioning_uri(user.email, issuer_name="Summit")
            qr = qrcode.QRCode(box_size=10, border=4)
            qr.add_data(provisioning_uri)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            flash('2FA setup initiated. Scan the QR code with Google Authenticator.', 'info')
            return redirect(url_for('settings_2fa', img=img_str))
        elif action == 'enable_2fa':
            code = request.form.get('code', '').strip()
            if user.totp_secret:
                totp = pyotp.TOTP(user.totp_secret)
                if totp.verify(code):
                    user.is_2fa_enabled = True
                    db.session.commit()
                    flash('✅ 2FA enabled!', 'success')
                else:
                    flash('❌ Invalid code. Try again.', 'danger')
        elif action == 'disable_2fa':
            user.is_2fa_enabled = False
            user.totp_secret = None
            db.session.commit()
            flash('2FA disabled.', 'info')
        return redirect(url_for('settings'))
    
    avatar_url = f"https://ui-avatars.com/api/?name={user.full_name}&background={user.avatar_color}&color=fff&size=80"
    
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">{t("settings", lang)}</h2>
    
    <div style="display:flex;align-items:center;gap:20px;margin-bottom:20px;">
        <img src="{avatar_url}" style="border-radius:50%; width:80px; height:80px; border:3px solid #3b82f6;">
        <div>
            <h3 style="color:var(--text-primary);font-size:20px;">{user.full_name}</h3>
            <p style="color:var(--text-muted);">{user.email}</p>
        </div>
    </div>
    
    <div class="card">
        <h3>{t("preferences", lang)}</h3>
        <form method="POST">
            <input type="hidden" name="action" value="update_preferences">
            <label style="color:var(--text-primary);font-weight:500;">{t("currency", lang)}</label>
            <select name="currency">
                <option value="ZAR" {'selected' if pref.currency == 'ZAR' else ''}>R ZAR (South African Rand)</option>
                <option value="USD" {'selected' if pref.currency == 'USD' else ''}>$ USD (US Dollar)</option>
                <option value="EUR" {'selected' if pref.currency == 'EUR' else ''}>€ EUR (Euro)</option>
                <option value="GBP" {'selected' if pref.currency == 'GBP' else ''}>£ GBP (British Pound)</option>
            </select>
            <label style="color:var(--text-primary);font-weight:500;">{t("language", lang)}</label>
            <select name="language">
                <option value="en" {'selected' if pref.language == 'en' else ''}>🇬🇧 English</option>
                <option value="af" {'selected' if pref.language == 'af' else ''}>🇿🇦 Afrikaans</option>
                <option value="zu" {'selected' if pref.language == 'zu' else ''}>🇿🇦 isiZulu</option>
                <option value="es" {'selected' if pref.language == 'es' else ''}>🇪🇸 Español</option>
                <option value="fr" {'selected' if pref.language == 'fr' else ''}>🇫🇷 Français</option>
                <option value="de" {'selected' if pref.language == 'de' else ''}>🇩🇪 Deutsch</option>
                <option value="pt" {'selected' if pref.language == 'pt' else ''}>🇵🇹 Português</option>
                <option value="it" {'selected' if pref.language == 'it' else ''}>🇮🇹 Italiano</option>
                <option value="sw" {'selected' if pref.language == 'sw' else ''}>🇹🇿 Kiswahili</option>
                <option value="hi" {'selected' if pref.language == 'hi' else ''}>🇮🇳 हिन्दी</option>
                <option value="ar" {'selected' if pref.language == 'ar' else ''}>🇸🇦 العربية</option>
            </select>
            <button type="submit" class="btn">{t("save", lang)}</button>
        </form>
    </div>
    
    <div class="card">
        <h3>{t("profile", lang)}</h3>
        <form method="POST">
            <input type="hidden" name="action" value="update_profile">
            <input type="text" name="full_name" placeholder="{t("full_name", lang)}" value="{user.full_name}" required>
            <input type="email" name="email" placeholder="{t("email", lang)}" value="{user.email}" required>
            <label style="color:var(--text-primary);font-weight:500;">Avatar Color</label>
            <input type="color" name="avatar_color" value="{user.avatar_color}">
            <button type="submit" class="btn">{t("save", lang)}</button>
        </form>
    </div>
    
    <div class="card">
        <h3>{t("security", lang)}</h3>
        <form method="POST">
            <input type="hidden" name="action" value="change_password">
            <input type="password" name="current_password" placeholder="{t("current_password", lang)}" required>
            <input type="password" name="new_password" placeholder="{t("new_password", lang)}" required>
            <input type="password" name="confirm_password" placeholder="{t("confirm_password", lang)}" required>
            <button type="submit" class="btn">{t("change_password", lang)}</button>
        </form>
    </div>
    
    <div class="card">
        <h3>🔐 Two-Factor Authentication</h3>
        {'<p style="color:#22c55e;">✅ 2FA is enabled</p>' if user.is_2fa_enabled else '<p style="color:var(--text-muted);">❌ 2FA is disabled</p>'}
        
        {f'''
        <form method="POST">
            <input type="hidden" name="action" value="{'disable_2fa' if user.is_2fa_enabled else 'setup_2fa'}">
            <button type="submit" class="btn {'btn-ghost' if user.is_2fa_enabled else ''}">
                {'Disable 2FA' if user.is_2fa_enabled else 'Setup 2FA'}
            </button>
        </form>
        ''' if not user.is_2fa_enabled else ''}
    </div>
    
    <div class="card">
        <h3>{t("account", lang)}</h3>
        <p style="color:var(--text-muted);">{t("plan", lang)}: <strong>{'Premium' if user.is_premium else 'Free'}</strong></p>
        <p style="color:var(--text-muted);">{t("member_since", lang)}: {user.created_at.strftime('%B %d, %Y')}</p>
        <a href="/upgrade" class="btn btn-ghost">{t("manage_subscription", lang)}</a>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/settings/2fa')
def settings_2fa():
    img = request.args.get('img', '')
    page = f"""
    <h2 style="font-size:24px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">🔐 2FA Setup</h2>
    <div class="card" style="text-align:center;">
        <p style="color:var(--text-secondary);margin-bottom:16px;">Scan this QR code with Google Authenticator, then enter the 6-digit code below.</p>
        <img src="data:image/png;base64,{img}" style="margin:10px auto;display:block;border-radius:12px;border:1px solid var(--border-color);">
        <form method="POST" action="/settings">
            <input type="hidden" name="action" value="enable_2fa">
            <input type="text" name="code" placeholder="Enter 6-digit code" maxlength="6" style="width:200px;text-align:center;">
            <button type="submit" class="btn">Enable 2FA</button>
        </form>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang='en', t=t)

# ---------- UPGRADE ----------
@app.route('/upgrade')
def upgrade():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    is_premium = user.is_premium and (user.premium_until is None or user.premium_until > datetime.utcnow())
    if is_premium:
        flash('You are already a Premium user!', 'success')
        return redirect(url_for('dashboard'))
    page = f"""
    <div style="max-width:800px;margin:0 auto;">
        <div style="text-align:center;margin-bottom:40px;"><h2 style="font-size:32px;font-weight:700;color:var(--text-primary);margin-bottom:8px;">{t("upgrade", lang)}</h2><p class="text-muted">{t("choose_plan", lang)}</p></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;">
            <div class="pricing-card"><h3 style="color:var(--text-primary);font-size:20px;">{t("free", lang)}</h3><div class="price">R0<span>/month</span></div><ul><li>{t("free_features", lang)}</li></ul><div style="margin-top:12px;padding:8px 16px;background:rgba(34,197,94,0.05);border-radius:8px;border:1px solid rgba(34,197,94,0.1);"><p style="color:#22c55e;font-size:14px;">{t("current_plan", lang)}</p></div></div>
            <div class="pricing-card featured"><div style="display:inline-block;padding:4px 12px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);border-radius:20px;font-size:12px;font-weight:600;color:white;margin-bottom:8px;">{t("most_popular", lang)}</div><h3 style="color:var(--text-primary);font-size:20px;">{t("premium", lang)}</h3><div class="price">R30<span>/month</span></div><ul><li>{t("premium_features", lang)}</li></ul><a href="{url_for('payment_beta')}" class="btn" style="width:100%;margin-top:8px;">{t("upgrade_now", lang)}</a><p class="text-muted text-xs mt-10" style="color:var(--text-muted);">$1.60 USDC (~R30)</p></div>
        </div>
        <div class="card" style="text-align:center;border-color:var(--border-color);margin-top:24px;"><p class="text-muted text-sm">{t("payment_note", lang)}</p></div>
    </div>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- PAYMENT ----------
@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    if user.is_premium and user.premium_until and user.premium_until > datetime.utcnow():
        flash('You are already Premium!', 'info')
        return redirect(url_for('dashboard'))
    result = check_usdc_payment()
    if result.get('success'):
        user.is_premium = True
        user.premium_until = datetime.utcnow() + timedelta(days=30)
        db.session.commit()
        payment = Payment(user_id=user.id, tx_hash=result.get('tx_hash'), amount=result.get('amount', 1.60), currency='USDC', status='completed', confirmed_at=datetime.utcnow())
        db.session.add(payment)
        db.session.commit()
        flash('Payment verified! Your account is now Premium.', 'success')
    else:
        flash(result.get('message', 'No payment found.'), 'danger')
    return redirect(url_for('dashboard'))

@app.route('/payment/beta')
def payment_beta():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    is_premium = user.is_premium and (user.premium_until is None or user.premium_until > datetime.utcnow())
    if is_premium:
        flash('You are already a Premium user!', 'success')
        return redirect(url_for('dashboard'))
    
    if not WALLET_ADDRESS:
        flash('Wallet address not configured.', 'danger')
        return redirect(url_for('upgrade'))
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    
    page = f"""
    <div style="max-width:600px;margin:0 auto;">
        <div style="text-align:center;margin-bottom:20px;">
            <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);">{t('payment', lang)}</h2>
            <p style="color:var(--text-secondary);">{t('pay_with', lang)} USDC</p>
        </div>
        
        <div style="background: linear-gradient(145deg, #1a1a2e, #16213e); border-radius: 20px; padding: 30px 20px; text-align: center; margin: 20px 0; border: 1px solid var(--border-color); box-shadow: 0 8px 32px var(--shadow-color);">
            <p style="color: #aaa; font-size: 14px; margin-bottom: 15px;">📱 Scan with Trust Wallet or any crypto wallet</p>
            
            <img src="https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={WALLET_ADDRESS}&format=png" alt="USDC Payment QR Code" style="width:200px;height:200px;background:white;padding:12px;border-radius:16px;margin:10px auto 20px auto;display:block;border:3px solid #00d4aa;">
            
            <div style="background:#0f0f23;padding:12px 20px;border-radius:12px;display:inline-block;margin:10px 0 15px 0;border:1px solid #2a2a5a;">
                <code style="color:#00d4aa;font-size:15px;word-break:break-all;font-family:monospace;">{WALLET_ADDRESS[:10]}...{WALLET_ADDRESS[-6:] if len(WALLET_ADDRESS) > 16 else WALLET_ADDRESS}</code>
            </div>
            <br>
            
            <button onclick="copyAddress()" style="background:#00d4aa;border:none;padding:12px 35px;border-radius:10px;color:#0a0a1a;font-weight:bold;font-size:15px;cursor:pointer;transition:all 0.3s ease;margin:5px 0 15px 0;">📋 Copy Full Address</button>
            
            <div style="background:#0f0f23;border-radius:12px;padding:15px;margin:15px 0 10px 0;border:1px solid #2a2a5a;">
                <p style="color:#00d4aa;font-size:22px;font-weight:bold;margin:0;">R30 = ~$1.60 USDC</p>
                <p style="color:#666;font-size:13px;margin:5px 0 0 0;">Send exactly this amount to the address above</p>
            </div>
            
            <p style="color:#ff4444;font-size:12px;margin-top:15px;">⚠️ Only send <strong>USDC (ERC-20)</strong> to this address. Other assets will be lost.</p>
        </div>
        
        <div style="text-align:center;margin:20px 0;">
            <form method="POST" action="/verify_payment">
                <input type="text" name="tx_hash" placeholder="Paste your transaction hash here..." style="width:100%;padding:11px 14px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;color:var(--text-primary);font-size:14px;margin-bottom:12px;">
                <button type="submit" class="btn" style="width:100%;">🔍 Verify My Payment</button>
                <p style="color:var(--text-muted);font-size:12px;margin-top:8px;">After sending, paste your transaction hash above and click verify</p>
            </form>
        </div>
        
        <a href="{url_for('dashboard')}" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    </div>
    
    <script>
        function copyAddress() {{
            const addr = "{WALLET_ADDRESS}";
            navigator.clipboard.writeText(addr).then(() => {{
                alert("✅ Address copied! Open Trust Wallet → Send → Paste address → Send R30 USDC");
            }}).catch(() => {{
                prompt("Copy this address:", addr);
            }});
        }}
        document.querySelector('img[alt="USDC Payment QR Code"]')?.addEventListener('click', function() {{
            this.style.transform = 'scale(0.95)';
            setTimeout(() => {{ this.style.transform = 'scale(1)'; }}, 200);
        }});
    </script>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- EXPORT PDF ----------
@app.route('/export/pdf')
def export_pdf():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    total_income_zar = sum(i.amount for i in Income.query.filter_by(user_id=user.id).all())
    total_crypto_zar = sum(c.value_zar for c in Crypto.query.filter_by(user_id=user.id).all())
    total_stocks_zar = 0
    for s in Stock.query.filter_by(user_id=user.id).all():
        price = get_stock_price(s.symbol)
        if price:
            total_stocks_zar += price * s.shares
    total_liabilities_zar = sum(l.amount for l in Liability.query.filter_by(user_id=user.id).all())
    
    total_income = convert_currency(total_income_zar, currency)
    total_crypto = convert_currency(total_crypto_zar, currency)
    total_stocks = convert_currency(total_stocks_zar, currency)
    total_liabilities = convert_currency(total_liabilities_zar, currency)
    net_worth = total_income + total_crypto + total_stocks - total_liabilities
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Summit Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; padding: 40px; background: white; color: #333; }}
        h1 {{ color: #3b82f6; }}
        .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 20px; margin: 16px 0; }}
        .stat {{ display: inline-block; width: 30%; text-align: center; padding: 16px; margin: 8px; background: #f5f5f5; border-radius: 8px; }}
        .stat h2 {{ margin: 0; font-size: 24px; }}
        .stat p {{ margin: 4px 0 0; color: #666; }}
        .footer {{ margin-top: 40px; text-align: center; color: #999; font-size: 12px; }}
    </style>
    </head>
    <body>
        <h1>🏔️ Summit Report</h1>
        <p>Generated for {user.full_name} on {datetime.utcnow().strftime('%B %d, %Y')}</p>
        
        <div class="card">
            <h3>Net Worth</h3>
            <div style="text-align:center;padding:20px;">
                <div style="font-size:48px;font-weight:bold;color:{'#22c55e' if net_worth >= 0 else '#ef4444'};">{currency_symbol}{net_worth:.2f}</div>
            </div>
        </div>
        
        <div class="card">
            <h3>Portfolio Breakdown</h3>
            <div class="stat"><h2>{currency_symbol}{total_income:.2f}</h2><p>Income</p></div>
            <div class="stat"><h2>{currency_symbol}{total_crypto:.2f}</h2><p>Crypto</p></div>
            <div class="stat"><h2>{currency_symbol}{total_stocks:.2f}</h2><p>Stocks</p></div>
            <div class="stat"><h2 style="color:#ef4444;">{currency_symbol}{total_liabilities:.2f}</h2><p>Liabilities</p></div>
        </div>
        
        <div class="footer">
            <p>© 2026 Summit — summit.onrender.com</p>
            <p>This report is for informational purposes only. Not financial advice.</p>
        </div>
    </body>
    </html>
    """
    
    try:
        from xhtml2pdf import pisa
        result = io.BytesIO()
        pisa.CreatePDF(html, dest=result)
        result.seek(0)
        return Response(result, mimetype='application/pdf', headers={'Content-Disposition': 'attachment; filename=summit_report.pdf'})
    except ImportError:
        return html

# ---------- PAYMENT WEBHOOK ----------
@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data'}), 400
        invoice_id = data.get('invoice_id')
        status = data.get('status')
        user_id = data.get('user_id')
        if status == 'paid' and user_id:
            user = User.query.get(int(user_id))
            if user and not user.is_premium:
                user.is_premium = True
                user.premium_until = datetime.utcnow() + timedelta(days=30)
                db.session.commit()
                payment = Payment(user_id=user.id, invoice_id=invoice_id, amount=data.get('amount', 1.60), status='completed', confirmed_at=datetime.utcnow())
                db.session.add(payment)
                db.session.commit()
                return jsonify({'status': 'success'}), 200
        return jsonify({'status': 'ignored'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/admin/upgrade/<int:user_id>')
def admin_upgrade(user_id):
    password = request.args.get('password', '')
    if password != 'summit2026':
        return "Unauthorized", 401
    user = User.query.get(user_id)
    if not user:
        flash('User not found', 'danger')
        return redirect(url_for('dashboard'))
    if user.is_premium and user.premium_until and user.premium_until > datetime.utcnow():
        flash(f'{user.full_name} already Premium until {user.premium_until.strftime("%Y-%m-%d")}', 'warning')
        return redirect(url_for('dashboard'))
    user.is_premium = True
    user.premium_until = datetime.utcnow() + timedelta(days=30)
    db.session.commit()
    payment = Payment(user_id=user.id, amount=1.60, currency='USDC', status='completed', confirmed_at=datetime.utcnow())
    db.session.add(payment)
    db.session.commit()
    flash(f'Successfully upgraded {user.full_name} to Premium!', 'success')
    return redirect(url_for('dashboard'))

# ---------- NEW ROUTES ----------
@app.route('/faq')
def faq():
    lang = 'en'
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            pref = UserPreference.query.filter_by(user_id=user.id).first()
            if pref:
                lang = pref.language
    
    page = f"""
    <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">❓ {t('faq', lang)}</h2>
    <div class="card">
        <h3>{t('faq_question_1', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_1', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_2', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_2', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_3', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_3', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_4', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_4', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_5', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_5', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_6', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_6', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_7', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_7', lang)}</p>
        <hr style="border-color:var(--border-color);">
        <h3>{t('faq_question_8', lang)}</h3>
        <p style="color:var(--text-secondary);">{t('faq_answer_8', lang)}</p>
    </div>
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/watchlist/toggle/<symbol>', methods=['POST'])
def toggle_watchlist(symbol):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    stock = Stock.query.filter_by(user_id=user.id, symbol=symbol).first()
    if stock:
        stock.is_watchlisted = not stock.is_watchlisted
        db.session.commit()
        flash(f"{symbol} watchlist updated!", "success")
    else:
        stock = Stock(
            user_id=user.id,
            symbol=symbol,
            shares=0,
            purchase_price=0,
            is_watchlisted=True
        )
        db.session.add(stock)
        db.session.commit()
        flash(f"{symbol} added to watchlist!", "success")
    return redirect(url_for('market'))

@app.route('/referral')
def referral():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    
    base_url = request.url_root.rstrip('/')
    referral_link = f"{base_url}/signup?ref={user.referral_code}"
    referral_count = User.query.filter_by(referred_by_id=user.id).count()
    earned_premium = referral_count >= 5
    
    page = f"""
    <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">🔗 {t('referral_link', lang)}</h2>
    
    <div class="card" style="text-align:center;">
        <p style="color:var(--text-secondary);font-size:16px;">{t('referral_reward', lang)}</p>
        <div style="background:var(--bg-secondary);padding:16px;border-radius:12px;margin:16px 0;word-break:break-all;border:1px solid var(--border-color);">
            <code style="color:#60a5fa;font-size:16px;">{referral_link}</code>
        </div>
        <button class="btn" onclick="copyReferral()" style="width:100%;">{t('copy_link', lang)}</button>
    </div>
    
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin:20px 0;">
        <div class="stat"><h2>{referral_count}</h2><p>{t('referral_friends', lang)}</p></div>
        <div class="stat"><h2>{referral_count // 5}</h2><p>Free Months Earned</p></div>
        <div class="stat" style="border-color:{'rgba(34,197,94,0.3)' if earned_premium else 'var(--border-color)'};">
            <h2 style="color:{'#22c55e' if earned_premium else 'var(--text-muted)'};">{earned_premium}</h2>
            <p>Premium Status</p>
        </div>
    </div>
    
    <div class="card" style="text-align:center;border-color:rgba(59,130,246,0.2);">
        <p style="font-size:14px;color:var(--text-secondary);">💡 Share your link on social media, WhatsApp, or with friends!</p>
    </div>
    
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    
    <script>
    function copyReferral() {{
        var text = "{referral_link}";
        navigator.clipboard.writeText(text).then(function() {{
            alert("Referral link copied!");
        }}, function() {{
            var dummy = document.createElement("input");
            document.body.appendChild(dummy);
            dummy.value = text;
            dummy.select();
            document.execCommand("copy");
            document.body.removeChild(dummy);
            alert("Referral link copied!");
        }});
    }}
    </script>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/alert/add', methods=['POST'])
def add_alert():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    symbol = request.form.get('symbol', '').upper().strip()
    target_price = request.form.get('target_price')
    condition = request.form.get('condition')
    
    if not symbol or not target_price or not condition:
        flash('All fields are required.', 'danger')
        return redirect(url_for('market'))
    
    try:
        target_price = float(target_price)
    except ValueError:
        flash('Invalid target price.', 'danger')
        return redirect(url_for('market'))
    
    alert = PriceAlert(
        user_id=user.id,
        symbol=symbol,
        target_price=target_price,
        condition=condition
    )
    db.session.add(alert)
    db.session.commit()
    flash(f"Alert set for {symbol} when it goes {condition} R{target_price}", "success")
    return redirect(url_for('market'))

@app.route('/alert/delete/<int:alert_id>', methods=['POST'])
def delete_alert(alert_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    alert = PriceAlert.query.get_or_404(alert_id)
    if alert.user_id != user.id:
        flash('Unauthorized!', 'danger')
        return redirect(url_for('dashboard'))
    db.session.delete(alert)
    db.session.commit()
    flash('Alert deleted.', 'info')
    return redirect(url_for('market'))

@app.route('/expenses', methods=['GET', 'POST'])
def expenses():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    if request.method == 'POST':
        description = request.form.get('description')
        amount = request.form.get('amount')
        category = request.form.get('category')
        date_str = request.form.get('date')
        is_recurring = request.form.get('is_recurring') == 'on'
        frequency = request.form.get('frequency', 'monthly')
        
        if not description or not amount:
            flash('Description and amount are required.', 'danger')
            return redirect(url_for('expenses'))
        
        try:
            amount = float(amount)
        except ValueError:
            flash('Invalid amount.', 'danger')
            return redirect(url_for('expenses'))
        
        date_obj = datetime.utcnow().date()
        if date_str:
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            except:
                pass
        
        expense = Expense(
            user_id=user.id,
            description=description,
            amount=amount,
            category=category,
            date=date_obj,
            is_recurring=is_recurring,
            frequency=frequency
        )
        db.session.add(expense)
        db.session.commit()
        flash('Expense added!', 'success')
        return redirect(url_for('expenses'))
    
    all_expenses = Expense.query.filter_by(user_id=user.id).order_by(Expense.date.desc()).all()
    total_expenses_zar = sum(e.amount for e in all_expenses)
    total_expenses = convert_currency(total_expenses_zar, currency)
    total_income_zar = sum(i.amount for i in Income.query.filter_by(user_id=user.id).all())
    total_income = convert_currency(total_income_zar, currency)
    net_savings = total_income - total_expenses
    
    table_rows = ""
    for e in all_expenses:
        category_display = e.category or 'Other'
        recurring_badge = '🔄' if e.is_recurring else ''
        e_amount = convert_currency(e.amount, currency)
        table_rows += f"""
        <tr>
            <td>{e.description}</td>
            <td>{category_display}</td>
            <td style="color:#ef4444;">{currency_symbol}{e_amount:.2f}</td>
            <td>{e.date.strftime('%Y-%m-%d')}</td>
            <td>{recurring_badge}</td>
        </tr>
        """
    
    page = f"""
    <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">💰 {t('expenses', lang)}</h2>
    
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px;">
        <div class="stat"><h2 style="color:#ef4444;">{currency_symbol}{total_expenses:.2f}</h2><p>{t('total_expenses', lang)}</p></div>
        <div class="stat"><h2 style="color:#22c55e;">{currency_symbol}{total_income:.2f}</h2><p>{t('income', lang)}</p></div>
        <div class="stat" style="border-color:{'rgba(34,197,94,0.3)' if net_savings >= 0 else 'rgba(239,68,68,0.3)'};">
            <h2 style="color:{'#22c55e' if net_savings >= 0 else '#ef4444'};">{currency_symbol}{net_savings:.2f}</h2>
            <p>Net Savings</p>
        </div>
    </div>
    
    <div class="card">
        <h3>{t('add_expense', lang)}</h3>
        <form method="POST">
            <input type="text" name="description" placeholder="{t('description', lang)}" required>
            <input type="number" step="0.01" name="amount" placeholder="{t('amount', lang)}" required>
            <select name="category">
                <option value="Food">{t('food', lang)}</option>
                <option value="Transport">{t('transport', lang)}</option>
                <option value="Entertainment">{t('entertainment', lang)}</option>
                <option value="Bills">{t('bills', lang)}</option>
                <option value="Shopping">{t('shopping', lang)}</option>
                <option value="Other">{t('other', lang)}</option>
            </select>
            <input type="date" name="date">
            <div style="display:flex;gap:12px;align-items:center;margin:8px 0;">
                <label style="color:var(--text-secondary);font-size:14px;">
                    <input type="checkbox" name="is_recurring"> 🔄 Recurring
                </label>
                <select name="frequency">
                    <option value="monthly">Monthly</option>
                    <option value="yearly">Yearly</option>
                </select>
            </div>
            <button type="submit" class="btn">{t('add_expense', lang)}</button>
        </form>
    </div>
    
    {f'''
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
            <h3>{t('expenses', lang)}</h3>
            <span class="text-muted text-sm">{t('total', lang)}: <strong style="color:#ef4444;">{currency_symbol}{total_expenses:.2f}</strong></span>
        </div>
        <table>
            <thead><tr><th>{t('description', lang)}</th><th>{t('category', lang)}</th><th>{t('amount', lang)}</th><th>{t('date', lang)}</th><th>Recurring</th></tr></thead>
            <tbody>{table_rows}</tbody>
        </table>
    </div>
    ''' if all_expenses else f'<p class="text-muted" style="color:var(--text-muted);">{t("no_expenses", lang)}</p>'}
    
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

@app.route('/milestone/add/<int:project_id>', methods=['POST'])
def add_milestone(project_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    project = Project.query.get_or_404(project_id)
    if project.user_id != user.id:
        flash('Unauthorized!', 'danger')
        return redirect(url_for('dashboard'))
    
    name = request.form.get('name')
    if not name:
        flash('Milestone name is required.', 'danger')
        return redirect(url_for('projects'))
    
    milestone = Milestone(project_id=project_id, name=name)
    db.session.add(milestone)
    db.session.commit()
    update_project_progress(project_id)
    flash('Milestone added!', 'success')
    return redirect(url_for('projects'))

@app.route('/milestone/toggle/<int:milestone_id>', methods=['POST'])
def toggle_milestone(milestone_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    milestone = Milestone.query.get_or_404(milestone_id)
    project = Project.query.get(milestone.project_id)
    if project.user_id != user.id:
        flash('Unauthorized!', 'danger')
        return redirect(url_for('dashboard'))
    
    milestone.is_completed = not milestone.is_completed
    db.session.commit()
    update_project_progress(project.id)
    return redirect(url_for('projects'))

# ---------- COMMUNITY ----------
@app.route('/community', methods=['GET', 'POST'])
def community():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
    
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    lang = pref.language if pref else 'en'
    currency = pref.currency if pref else 'ZAR'
    currency_symbol = get_currency_symbol(currency)
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            shoutout = Shoutout(user_id=user.id, message=message)
            db.session.add(shoutout)
            db.session.commit()
            flash('🎉 Shoutout posted!', 'success')
        else:
            flash('Message cannot be empty.', 'danger')
        return redirect(url_for('community'))
    
    if request.args.get('toggle_public') == '1':
        user.is_public = not user.is_public
        db.session.commit()
        flash(f'Profile visibility: {"Public" if user.is_public else "Private"}', 'info')
        return redirect(url_for('community'))
    
    all_users = User.query.filter_by(is_public=True).all()
    leaderboard = []
    for u in all_users:
        incomes = Income.query.filter_by(user_id=u.id).all()
        cryptos = Crypto.query.filter_by(user_id=u.id).all()
        stocks = Stock.query.filter_by(user_id=u.id).all()
        liabilities = Liability.query.filter_by(user_id=u.id).all()
        total_zar = sum(i.amount for i in incomes) + sum(c.value_zar for c in cryptos) + sum(get_stock_price(s.symbol) * s.shares if get_stock_price(s.symbol) else 0 for s in stocks) - sum(l.amount for l in liabilities)
        total = convert_currency(total_zar, currency)
        leaderboard.append({'user': u, 'net_worth': total})
    leaderboard.sort(key=lambda x: x['net_worth'], reverse=True)
    leaderboard = leaderboard[:10]
    
    leaderboard_html = ""
    for idx, entry in enumerate(leaderboard):
        medal = "🥇" if idx == 0 else "🥈" if idx == 1 else "🥉" if idx == 2 else f"#{idx+1}"
        leaderboard_html += f"""
        <li style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border-color);">
            <span>{medal} {entry['user'].full_name}</span>
            <span style="color:#22c55e;font-weight:600;">{currency_symbol}{entry['net_worth']:.2f}</span>
        </li>
        """
    
    shoutouts = Shoutout.query.order_by(Shoutout.created_at.desc()).limit(20).all()
    shoutout_html = ""
    for s in shoutouts:
        shoutout_html += f"""
        <div style="padding:12px 0;border-bottom:1px solid var(--border-color);">
            <div style="display:flex;justify-content:space-between;">
                <strong style="color:var(--text-primary);">{s.user.full_name}</strong>
                <span style="color:var(--text-muted);font-size:12px;">{s.created_at.strftime('%b %d')}</span>
            </div>
            <p style="color:var(--text-secondary);font-size:14px;margin-top:4px;">{s.message}</p>
        </div>
        """
    
    page = f"""
    <h2 style="font-size:28px;font-weight:700;color:var(--text-primary);margin-bottom:20px;">👥 Community</h2>
    
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
        <div>
            <p style="color:var(--text-secondary);">Your profile visibility: <strong>{'Public' if user.is_public else 'Private'}</strong></p>
            <p style="color:var(--text-muted);font-size:13px;">Public users appear on the leaderboard.</p>
        </div>
        <a href="?toggle_public=1" class="btn btn-ghost">{'Make Private' if user.is_public else 'Go Public'}</a>
    </div>
    
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
        <div class="card">
            <h3>🏆 Top 10 Savers</h3>
            <ul style="list-style:none;padding:0;">{leaderboard_html}</ul>
            <p style="color:var(--text-muted);font-size:12px;margin-top:12px;">Users with public profiles only.</p>
        </div>
        <div class="card">
            <h3>📢 Shoutouts</h3>
            <div style="max-height:300px;overflow-y:auto;">{shoutout_html}</div>
            <form method="POST" style="margin-top:12px;display:flex;gap:8px;">
                <input type="text" name="message" placeholder="Share your win..." style="flex:1;" required>
                <button type="submit" class="btn">Post</button>
            </form>
        </div>
    </div>
    
    <a href="/dashboard" class="btn btn-ghost mt-10">{t('back', lang)}</a>
    """
    return render_template_string(BASE_HTML, content=page, lang=lang, t=t)

# ---------- RUN ----------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)