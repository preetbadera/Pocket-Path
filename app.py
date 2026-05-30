import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "pocket_path_secure_encryption_token"  # Required for browser session cookies
DB_FILE = "database.db"

# Setup Flask-Login
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users Credentials Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    # Transactions Ledger tied to individual user
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    # Autopay configuration linked to user
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS autopay_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            start_date TEXT NOT NULL,
            frequency TEXT NOT NULL,
            duration_cycles INTEGER NOT NULL,
            cycles_processed INTEGER DEFAULT 0,
            last_processed_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user_data = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if user_data:
        return User(user_data['id'], user_data['username'])
    return None

def process_autopayments():
    """Automation engine calculating cycles only for the logged-in user session."""
    if not current_user.is_authenticated:
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    today = datetime.strptime(today_str, "%Y-%m-%d")
    
    rules = cursor.execute('''
        SELECT * FROM autopay_rules 
        WHERE user_id = ? AND cycles_processed < duration_cycles
    ''', (current_user.id,)).fetchall()
    
    for rule in rules:
        start_date = datetime.strptime(rule['start_date'], "%Y-%m-%d")
        cycles = rule['cycles_processed']
        
        if rule['frequency'] == 'Monthly':
            month_offset = start_date.month - 1 + cycles
            next_year = start_date.year + (month_offset // 12)
            next_month = (month_offset % 12) + 1
            next_run_date = datetime(next_year, next_month, min(start_date.day, 28)) 
        else:
            next_run_date = datetime(start_date.year + cycles, start_date.month, min(start_date.day, 28))
        
        if today >= next_run_date and rule['last_processed_date'] != today_str:
            cursor.execute('''
                INSERT INTO transactions (user_id, date, type, category, amount, description)
                VALUES (?, ?, 'Expense', ?, ?, ?)
            ''', (current_user.id, today_str, rule['category'], rule['amount'], f"Autopay ({cycles + 1}/{rule['duration_cycles']}): {rule['title']}"))
            
            cursor.execute('''
                UPDATE autopay_rules SET cycles_processed = cycles_processed + 1, last_processed_date = ? 
                WHERE id = ?
            ''', (today_str, rule['id']))
            
    conn.commit()
    conn.close()

# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            user_obj = User(user['id'], user['username'])
            login_user(user_obj)
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password credentials configuration.")
    return render_template('layout.html', auth_mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
            flash("Account generated successfully! Please sign in.")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Username already claimed inside server tracking logs.")
        finally:
            conn.close()
    return render_template('layout.html', auth_mode='register')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- LEDGER APP ROUTES ---
@app.route('/')
@login_required
def index():
    process_autopayments()
    conn = get_db_connection()
    
    total_deposit = conn.execute("SELECT SUM(amount) FROM transactions WHERE user_id=? AND type='Deposit'", (current_user.id,)).fetchone()[0] or 0.0
    total_expense = conn.execute("SELECT SUM(amount) FROM transactions WHERE user_id=? AND type='Expense'", (current_user.id,)).fetchone()[0] or 0.0
    remaining_balance = total_deposit - total_expense
    
    transactions = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC, id DESC", (current_user.id,)).fetchall()
    autopays = conn.execute("SELECT * FROM autopay_rules WHERE user_id=?", (current_user.id,)).fetchall()
    conn.close()
    
    return render_template('index.html', transactions=transactions, autopays=autopays, deposit=total_deposit, expense=total_expense, balance=remaining_balance)

@app.route('/add_transaction', methods=['POST'])
@login_required
def add_transaction():
    t_type = request.form['type']
    category = request.form['category']
    amount = float(request.form['amount'])
    date = request.form['date'] or datetime.now().strftime("%Y-%m-%d")
    description = request.form['description']

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO transactions (user_id, date, type, category, amount, description)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (current_user.id, date, t_type, category, amount, description))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/add_autopay', methods=['POST'])
@login_required
def add_autopay():
    title = request.form['title']
    amount = float(request.form['amount'])
    category = request.form['category']
    start_date = request.form['start_date'] or datetime.now().strftime("%Y-%m-%d")
    frequency = request.form['frequency']
    duration = int(request.form['duration'])

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO autopay_rules (user_id, title, amount, category, start_date, frequency, duration_cycles, cycles_processed)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    ''', (current_user.id, title, amount, category, start_date, frequency, duration))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

# Secure Deletion Route verifying account boundaries
@app.route('/delete/<int:id>')
@login_required
def delete_transaction(id):
    conn = get_db_connection()
    # Confirming the transaction matches current logged-in identity context
    conn.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/delete_autopay/<int:id>')
@login_required
def delete_autopay(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM autopay_rules WHERE id = ? AND user_id = ?", (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/download_statement')
@login_required
def download_statement():
    conn = get_db_connection()
    transactions = conn.execute("SELECT date, type, category, amount, description FROM transactions WHERE user_id=? ORDER BY date DESC", (current_user.id,)).fetchall()
    conn.close()

    csv_data = "Date,Type,Category,Amount,Description\n"
    for row in transactions:
        csv_data += f"{row['date']},{row['type']},{row['category']},{row['amount']},{row['description']}\n"
    
    response = make_response(csv_data)
    response.headers["Content-Disposition"] = f"attachment; filename={current_user.username}_statement.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)