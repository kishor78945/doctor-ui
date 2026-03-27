import sqlite3
import os
import bcrypt
import re
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hospital.db")

def validate_password(password: str) -> tuple[bool, str]:
    """
    Validate password strength.
    Returns (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number"
    
    if not re.search(r"[@$!%*?&#]", password):
        return False, "Password must contain at least one special character (@$!%*?&#)"
    
    return True, ""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(
        password.encode("utf-8"),
        hashed.encode("utf-8")
    )


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # ---- USERS ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        must_change_password INTEGER DEFAULT 1,
        failed_login_attempts INTEGER DEFAULT 0,
        locked_until DATETIME DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---- AUDIT LOGS ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # ---- PATIENTS (with user tracking) ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        age TEXT,
        sex TEXT,
        hospital_id TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users(id),
        UNIQUE(hospital_id, created_by)
    )
    """)

    # ---- DISCHARGE SUMMARIES (with user tracking) ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS discharge_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        diagnosis TEXT,
        admission_date TEXT,
        discharge_date TEXT,
        pdf_path TEXT,
        created_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES patients(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
    )
    """)

    # ---- SUMMARY TEMPLATES (for quick entry) ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS summary_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        diagnosis TEXT,
        chief_complaints TEXT,
        treatment_given TEXT,
        medications TEXT,
        follow_up_instructions TEXT,
        created_by INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users(id)
    )
    """)

    # Check if created_by column exists in patients table
    cur.execute("PRAGMA table_info(patients)")
    columns = [column[1] for column in cur.fetchall()]
    
    if 'created_by' not in columns:
        print("Migrating patients table to add created_by column...")
        try:
            # Add created_by column with default value of 1 (admin)
            cur.execute("ALTER TABLE patients ADD COLUMN created_by INTEGER DEFAULT 1")
            print("[OK] Added created_by column to patients table")
        except Exception as e:
            print(f"Note: {e}")

    # Check if created_by column exists in discharge_summaries table
    cur.execute("PRAGMA table_info(discharge_summaries)")
    columns = [column[1] for column in cur.fetchall()]
    
    if 'created_by' not in columns:
        print("Migrating discharge_summaries table to add created_by column...")
        try:
            # Add created_by column with default value of 1 (admin)
            cur.execute("ALTER TABLE discharge_summaries ADD COLUMN created_by INTEGER DEFAULT 1")
            print("[OK] Added created_by column to discharge_summaries table")
        except Exception as e:
            print(f"Note: {e}")

    # Add new columns for extended content
    new_columns = [
        ("chief_complaints", "TEXT"),
        ("treatment_given", "TEXT"),
        ("medications", "TEXT"),
        ("follow_up_instructions", "TEXT"),
        ("investigations", "TEXT"),
        ("condition_at_discharge", "TEXT"),
        ("history_of_illness", "TEXT")
    ]
    
    cur.execute("PRAGMA table_info(discharge_summaries)")
    existing_columns = [column[1] for column in cur.fetchall()]
    
    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            try:
                cur.execute(f"ALTER TABLE discharge_summaries ADD COLUMN {col_name} {col_type}")
                print(f"[OK] Added {col_name} column to discharge_summaries table")
            except Exception as e:
                print(f"Note: {e}")

    conn.commit()
    conn.close()
    print("[OK] Database initialized with user workspaces support")


def create_default_admin():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE username = ?", ("admin",))
    if cur.fetchone() is None:
        password_hash = hash_password("admin123")

        cur.execute("""
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (?, ?, ?, 0)
        """, ("admin", password_hash, "admin"))

        conn.commit()
        print("[OK] Default admin user created (username: admin, password: admin123)")

    conn.close()


def log_action(user_id, action, details=""):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO audit_logs (user_id, action, details)
        VALUES (?, ?, ?)
    """, (user_id, action, details))

    conn.commit()
    conn.close()


def check_account_locked(username: str) -> tuple[bool, int]:
    """
    Check if the account is currently locked.
    Returns (is_locked, remaining_minutes)
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT locked_until FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()

    if row and row["locked_until"]:
        try:
            locked_until = datetime.strptime(row["locked_until"], "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                locked_until = datetime.strptime(row["locked_until"], "%Y-%m-%d %H:%M:%S")
            except:
                return False, 0
                
        if locked_until > datetime.now():
            remaining = int((locked_until - datetime.now()).total_seconds() / 60)
            return True, max(1, remaining)
            
    return False, 0


def record_failed_login(username: str):
    """
    Increment failed login attempts. Lock account if attempts > limit.
    Returns (is_locked, attempts_remaining, lockout_minutes)
    """
    MAX_ATTEMPTS = 5
    LOCKOUT_MINUTES = 15

    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT failed_login_attempts FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    
    if not row:
        conn.close()
        return False, MAX_ATTEMPTS, 0
        
    attempts = row["failed_login_attempts"] + 1
    
    if attempts >= MAX_ATTEMPTS:
        locked_until = datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)
        cur.execute("""
            UPDATE users 
            SET failed_login_attempts = ?, locked_until = ? 
            WHERE username = ?
        """, (attempts, locked_until, username))
        conn.commit()
        conn.close()
        return True, 0, LOCKOUT_MINUTES
    else:
        cur.execute("""
            UPDATE users 
            SET failed_login_attempts = ? 
            WHERE username = ?
        """, (attempts, username))
        conn.commit()
        conn.close()
        return False, MAX_ATTEMPTS - attempts, 0


def reset_failed_login(username: str):
    """
    Reset failed login attempts and clear lock on successful login.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
        SET failed_login_attempts = 0, locked_until = NULL 
        WHERE username = ?
    """, (username,))
    conn.commit()
    conn.close()