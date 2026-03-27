import os
import time
import re
import sys
import json
import csv
import io
from datetime import datetime, timedelta 
from flask import Flask, render_template, request, send_file, jsonify
from flask_wtf.csrf import CSRFProtect
import whisper
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from db import init_db, get_db, create_default_admin, log_action, verify_password, hash_password, validate_password
from db import check_account_locked, record_failed_login, reset_failed_login
from flask import session, redirect, url_for, flash
from dischargesummary import generate_discharge_json_from_transcript

# ----------------- PATHS & FOLDERS -----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
GENERATED_FOLDER = os.path.join(BASE_DIR, "generated")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)

# ffmpeg for Whisper - Update this path to match your system
# For Windows:
os.environ["PATH"] = r"C:\ffmpeg\ffmpeg-2025-12-04-git-d6458f6a8b-full_build\bin;" + os.environ.get("PATH", "")
# For Linux/Mac, ffmpeg should be in PATH already

def get_dashboard_stats(user_id=None):
    """Scan generated/ and build dashboard stats for specific user or all users (admin)."""
    total = 0
    today = 0
    recent = []
    recent_info = []  # Initialize here to ensure it's always defined

    conn = get_db()
    cur = conn.cursor()

    if user_id:
        # User-specific stats from database
        
        # Get total summaries for this user
        cur.execute("""
            SELECT COUNT(*) as total 
            FROM discharge_summaries
            WHERE created_by = ?
        """, (user_id,))
        result = cur.fetchone()
        total = result["total"] if result else 0
        
        # Get today's summaries
        cur.execute("""
            SELECT COUNT(*) as today_count
            FROM discharge_summaries
            WHERE created_by = ? 
            AND DATE(created_at) = DATE('now')
        """, (user_id,))
        result = cur.fetchone()
        today = result["today_count"] if result else 0
        
        # Get recent summaries with patient info
        cur.execute("""
            SELECT 
                ds.pdf_path,
                ds.created_at,
                p.name as patient_name,
                p.hospital_id
            FROM discharge_summaries ds
            JOIN patients p ON ds.patient_id = p.id
            WHERE ds.created_by = ?
            ORDER BY ds.created_at DESC
            LIMIT 5
        """, (user_id,))
        
        recent_rows = cur.fetchall()
        
        recent_info = []
        for row in recent_rows:
            try:
                created_at = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
                
                # Get relative path from generated folder
                pdf_path = row["pdf_path"]
                if os.path.exists(pdf_path):
                    rel_path = os.path.relpath(pdf_path, GENERATED_FOLDER).replace("\\", "/")
                else:
                    rel_path = pdf_path.replace("\\", "/")
                
                recent_info.append({
                    "filename": os.path.basename(pdf_path),
                    "relpath": rel_path,
                    "time": created_at.strftime("%d-%m-%Y %H:%M"),
                    "patient_name": row["patient_name"],
                    "hospital_id": row["hospital_id"]
                })
            except Exception as e:
                import traceback
                print(f"Error parsing recent summary for user {user_id}: {e}")
                traceback.print_exc()
    else:
        # Admin view - show ALL summaries from database
        
        # Get total summaries
        cur.execute("SELECT COUNT(*) as total FROM discharge_summaries")
        result = cur.fetchone()
        total = result["total"] if result else 0
        
        # Get today's summaries
        cur.execute("""
            SELECT COUNT(*) as today_count
            FROM discharge_summaries
            WHERE DATE(created_at) = DATE('now')
        """)
        result = cur.fetchone()
        today = result["today_count"] if result else 0
        
        # Get recent summaries from all users
        cur.execute("""
            SELECT 
                ds.pdf_path,
                ds.created_at,
                p.name as patient_name,
                p.hospital_id,
                u.username as created_by_user
            FROM discharge_summaries ds
            JOIN patients p ON ds.patient_id = p.id
            LEFT JOIN users u ON ds.created_by = u.id
            ORDER BY ds.created_at DESC
            LIMIT 5
        """)
        
        recent_rows = cur.fetchall()
        
        recent_info = []
        for row in recent_rows:
            try:
                created_at = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
                
                # Get relative path from generated folder
                pdf_path = row["pdf_path"]
                if os.path.exists(pdf_path):
                    rel_path = os.path.relpath(pdf_path, GENERATED_FOLDER).replace("\\", "/")
                else:
                    rel_path = pdf_path.replace("\\", "/")
                
                recent_info.append({
                    "filename": os.path.basename(pdf_path),
                    "relpath": rel_path,
                    "time": created_at.strftime("%d-%m-%Y %H:%M"),
                    "patient_name": row["patient_name"],
                    "hospital_id": row["hospital_id"],
                    "created_by": row["created_by_user"] or "Unknown"
                })
            except Exception as e:
                import traceback
                print(f"Error parsing recent summary (admin view): {e}")
                traceback.print_exc()

    conn.close()

    # Generate chart data - last 7 days
    chart_data = []
    conn2 = get_db()
    cur2 = conn2.cursor()
    
    for i in range(6, -1, -1):  # 6 days ago to today
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_label = (datetime.now() - timedelta(days=i)).strftime("%a")  # Mon, Tue, etc.
        
        if user_id:
            cur2.execute("""
                SELECT COUNT(*) as count FROM discharge_summaries 
                WHERE DATE(created_at) = ? AND created_by = ?
            """, (day, user_id))
        else:
            cur2.execute("""
                SELECT COUNT(*) as count FROM discharge_summaries 
                WHERE DATE(created_at) = ?
            """, (day,))
        
        result = cur2.fetchone()
        chart_data.append({
            "day": day_label,
            "count": result["count"] if result else 0
        })
    
    conn2.close()

    stats = {
        "total_summaries": total,
        "today_summaries": today,
        "recent_summaries": recent_info,
        "last_summary_time": recent_info[0]["time"] if recent_info else None,
        "last_summary_filename": recent_info[0]["filename"] if recent_info else None,
        "chart_data": chart_data
    }
    return stats

# ----------------- FLASK APP & MODELS -----------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-to-a-random-string-in-production")
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 1800  # 30 minutes in seconds
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

# Initialize CSRF protection
csrf = CSRFProtect(app)

# Load Whisper once
try:
    model = whisper.load_model("small")
except Exception as e:
    print(f"Warning: Could not load Whisper model: {e}")
    model = None

# Session timeout configuration (in seconds)
SESSION_TIMEOUT = 1800  # 30 minutes

# Session timeout middleware
@app.before_request
def check_session_timeout():
    """Check if session has timed out due to inactivity."""
    # Skip timeout check for login page, logout, static files, and keep_alive
    if request.endpoint in ['login', 'logout', 'static', 'start', 'keep_alive']:
        return
    
    # Check if user is logged in
    if "user_id" in session:
        last_activity = session.get("last_activity")
        
        if last_activity:
            try:
                last_activity_time = datetime.fromisoformat(last_activity)
                current_time = datetime.now()
                inactive_seconds = (current_time - last_activity_time).total_seconds()
                
                # Check if session has exceeded timeout period
                if inactive_seconds > SESSION_TIMEOUT:
                    user_id = session.get("user_id")
                    username = session.get("username", "Unknown")
                    
                    # Log the timeout
                    if user_id:
                        try:
                            log_action(
                                user_id=user_id,
                                action="SESSION_TIMEOUT",
                                details=f"Session expired after {int(inactive_seconds/60)} minutes of inactivity"
                            )
                        except Exception as e:
                            print(f"Error logging session timeout: {e}")
                    
                    # Clear the session
                    session.clear()
                    
                    # Redirect to login with timeout message
                    return render_template(
                        "login.html",
                        error="Your session has expired due to inactivity. Please log in again."
                    )
            except (ValueError, TypeError) as e:
                print(f"Error parsing last_activity: {e}")
                # Reset session if timestamp is corrupted
                session.clear()
                return redirect(url_for("login"))
        else:
            # If no last_activity timestamp exists, set it now
            session["last_activity"] = datetime.now().isoformat()
        
        # Update last activity timestamp on every request
        session["last_activity"] = datetime.now().isoformat()
        session.modified = True  # Ensure session is saved
    else:
        # User not logged in, redirect to login for protected routes
        return redirect(url_for("login"))

@app.route("/admin/toggle_user/<int:user_id>")
def toggle_user(user_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        return "Access denied", 403

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT active, username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return "User not found", 404

    new_status = 0 if row["active"] == 1 else 1

    cur.execute(
        "UPDATE users SET active = ? WHERE id = ?",
        (new_status, user_id)
    )
    conn.commit()

    log_action(
        user_id=session["user_id"],
        action="TOGGLE_USER",
        details=f"Set user '{row['username']}' active={new_status}"
    )

    conn.close()
    return redirect(url_for("manage_users"))

@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not new_password or not confirm_password:
            return render_template(
                "change_password.html",
                error="Both fields are required"
            )

        if new_password != confirm_password:
            return render_template(
                "change_password.html",
                error="Passwords do not match"
            )

        # VALIDATE PASSWORD STRENGTH
        is_valid, error_msg = validate_password(new_password)
        if not is_valid:
            return render_template(
                "change_password.html",
                error=error_msg
            )

        password_hash = hash_password(new_password)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET password_hash = ?, must_change_password = 0
            WHERE id = ?
        """, (password_hash, session["user_id"]))
        conn.commit()
        conn.close()

        log_action(
            user_id=session["user_id"],
            action="PASSWORD_CHANGED",
            details="User changed password on first login"
        )

        return redirect(url_for("index"))

    return render_template("change_password.html")

@app.route("/admin/users")
def manage_users():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        return "Access denied", 403

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, role, active FROM users")
    users = cur.fetchall()
    conn.close()

    return render_template("manage_users.html", users=users)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            return render_template(
                "login.html",
                error="Username and password are required"
            )

        # Check if account is locked
        try:
            is_locked, remaining_minutes = check_account_locked(username)
            if is_locked:
                return render_template(
                    "login.html",
                    error=f"Account is locked due to multiple failed login attempts. Please try again in {remaining_minutes} minute(s)."
                )
        except Exception as e:
            print(f"Error checking account lock: {e}")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, password_hash, role, must_change_password, active
            FROM users
            WHERE username = ?
        """, (username,))

        user = cur.fetchone()
        conn.close()

        # ❌ LOGIN FAILED - user not found or password wrong or inactive
        if not user or user["active"] != 1 or not verify_password(password, user["password_hash"]):
            # Record failed attempt
            try:
                is_locked, attempts_remaining, lockout_minutes = record_failed_login(username)
                
                if is_locked:
                    return render_template(
                        "login.html",
                        error=f"Too many failed login attempts. Account locked for {lockout_minutes} minutes."
                    )
                else:
                    return render_template(
                        "login.html",
                        error=f"Invalid username or password. {attempts_remaining} attempt(s) remaining before account lockout."
                    )
            except Exception as e:
                print(f"Error recording failed login: {e}")
                return render_template(
                    "login.html",
                    error="Invalid username or password."
                )

        # ✅ LOGIN SUCCESS - Reset failed attempts
        try:
            reset_failed_login(username)
        except Exception as e:
            print(f"Error resetting failed login: {e}")

        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["username"] = username
        session["last_activity"] = datetime.now().isoformat()

        try:
            log_action(
                user_id=user["id"],
                action="LOGIN",
                details="User logged in successfully"
            )
        except Exception as e:
            print(f"Error logging login action: {e}")

        # 🔐 Force password change
        if user["must_change_password"] == 1:
            return redirect(url_for("change_password"))

        return redirect(url_for("index"))

    return render_template("login.html")

@app.route("/admin/reset_password/<int:user_id>")
def reset_password(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return "Access denied", 403

    temp_password = "Temp@123"
    password_hash = hash_password(temp_password)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()

    if not user:
        conn.close()
        return "User not found", 404

    cur.execute("""
        UPDATE users
        SET password_hash = ?, must_change_password = 1
        WHERE id = ?
    """, (password_hash, user_id))

    conn.commit()
    conn.close()

    log_action(
        user_id=session["user_id"],
        action="RESET_PASSWORD",
        details=f"Admin reset password for user '{user['username']}' (ID: {user_id})"
    )

    return f"Password reset successfully. Temporary password: {temp_password}"

@app.route("/admin/audit_logs")
def view_audit_logs():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        return "Access denied", 403

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            a.id,
            u.username,
            u.role,
            a.action,
            a.details,
            a.created_at
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC
        LIMIT 500
    """)

    logs = cur.fetchall()
    conn.close()

    # Convert UTC timestamps to IST (UTC+5:30)
    from datetime import timedelta
    logs_with_ist = []
    for log in logs:
        log_dict = dict(log)
        if log_dict.get("created_at"):
            try:
                utc_time = datetime.strptime(log_dict["created_at"], "%Y-%m-%d %H:%M:%S")
                ist_time = utc_time + timedelta(hours=5, minutes=30)
                log_dict["created_at"] = ist_time.strftime("%d-%m-%Y %H:%M:%S")
            except Exception as e:
                print(f"Error converting timestamp: {e}")
        logs_with_ist.append(log_dict)

    return render_template("audit_logs.html", logs=logs_with_ist)

@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        try:
            log_action(
                user_id=user_id,
                action="LOGOUT",
                details="User logged out"
            )
        except Exception as e:
            print(f"Error logging logout: {e}")
    session.clear()
    return redirect(url_for("login"))

@app.route("/keep_alive")
def keep_alive():
    """Endpoint to refresh session on user activity - called by frontend JS."""
    if "user_id" not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
    
    # Update last activity timestamp
    session["last_activity"] = datetime.now().isoformat()
    session.modified = True
    return jsonify({"status": "ok", "message": "Session extended"})

@app.route("/download/<path:filepath>")
def download_pdf(filepath):
    """Securely download a PDF file."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    # Build full path
    full_path = os.path.join(GENERATED_FOLDER, filepath)
    
    # Security: ensure path is within GENERATED_FOLDER
    real_path = os.path.realpath(full_path)
    real_generated = os.path.realpath(GENERATED_FOLDER)
    if not real_path.startswith(real_generated):
        return "Access denied", 403
    
    # Check file exists
    if not os.path.exists(full_path):
        return "File not found", 404
    
    # For non-admin users, verify they own this file
    if session.get("role") != "admin":
        conn = get_db()
        cur = conn.cursor()
        # Get just the filename to match against (more robust than full path matching)
        filename = os.path.basename(full_path)
        cur.execute("""
            SELECT id FROM discharge_summaries 
            WHERE pdf_path LIKE ? AND created_by = ?
        """, (f"%{filename}", session.get("user_id")))
        if not cur.fetchone():
            conn.close()
            return "Access denied", 403
        conn.close()
    
    # Log the download
    try:
        log_action(
            user_id=session.get("user_id"),
            action="DOWNLOAD_PDF",
            details=f"Downloaded: {os.path.basename(full_path)}"
        )
    except:
        pass
    
    return send_file(full_path, as_attachment=True)

@app.route("/start")
def start():
    session.clear()
    return redirect(url_for("login"))

@app.route("/summaries")
def summaries():
    """List all summaries with search/filter capability and pagination."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    # Pagination settings
    per_page = 20
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    
    # Get search parameters
    patient_name = request.args.get("patient", "").strip()
    hospital_id = request.args.get("hospital_id", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    
    conn = get_db()
    cur = conn.cursor()
    
    # Build base query based on role
    if session.get("role") == "admin":
        base_query = """
            FROM discharge_summaries ds
            JOIN patients p ON ds.patient_id = p.id
            LEFT JOIN users u ON ds.created_by = u.id
            WHERE 1=1
        """
        params = []
    else:
        base_query = """
            FROM discharge_summaries ds
            JOIN patients p ON ds.patient_id = p.id
            WHERE ds.created_by = ?
        """
        params = [session.get("user_id")]
    
    # Add search filters
    if patient_name:
        base_query += " AND p.name LIKE ?"
        params.append(f"%{patient_name}%")
    
    if hospital_id:
        base_query += " AND p.hospital_id LIKE ?"
        params.append(f"%{hospital_id}%")
    
    if date_from:
        base_query += " AND DATE(ds.created_at) >= ?"
        params.append(date_from)
    
    if date_to:
        base_query += " AND DATE(ds.created_at) <= ?"
        params.append(date_to)
    
    # Get total count
    count_query = "SELECT COUNT(*) as total " + base_query
    cur.execute(count_query, params)
    total_count = cur.fetchone()["total"]
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
    
    # Adjust page if out of range
    if page > total_pages and total_pages > 0:
        page = total_pages
    
    # Get paginated results
    if session.get("role") == "admin":
        select_query = """
            SELECT 
                ds.id,
                ds.diagnosis,
                ds.created_at,
                ds.pdf_path,
                p.name as patient_name,
                p.hospital_id,
                u.username as created_by_username
        """ + base_query
    else:
        select_query = """
            SELECT 
                ds.id,
                ds.diagnosis,
                ds.created_at,
                ds.pdf_path,
                p.name as patient_name,
                p.hospital_id,
                NULL as created_by_username
        """ + base_query
    
    select_query += f" ORDER BY ds.created_at DESC LIMIT {per_page} OFFSET {(page - 1) * per_page}"
    
    cur.execute(select_query, params)
    rows = cur.fetchall()
    conn.close()
    
    # Process results
    summaries_list = []
    for row in rows:
        pdf_path = row["pdf_path"]
        if os.path.exists(pdf_path):
            relpath = os.path.relpath(pdf_path, GENERATED_FOLDER).replace("\\", "/")
        else:
            relpath = pdf_path.replace("\\", "/")
        
        try:
            created_at = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
            formatted_date = created_at.strftime("%d-%m-%Y %H:%M")
        except:
            formatted_date = row["created_at"]
        
        summaries_list.append({
            "id": row["id"],
            "patient_name": row["patient_name"],
            "hospital_id": row["hospital_id"],
            "diagnosis": row["diagnosis"] or "",
            "created_at": formatted_date,
            "relpath": relpath,
            "created_by_username": row["created_by_username"]
        })
    
    # Pagination info
    pagination = {
        "page": page,
        "per_page": per_page,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages
    }
    
    return render_template("summaries.html", summaries=summaries_list, pagination=pagination)

@app.route("/export/csv")
def export_csv():
    """Export summaries to CSV file."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Build query based on role
    if session.get("role") == "admin":
        query = """
            SELECT 
                ds.id,
                p.name as patient_name,
                p.hospital_id,
                p.age as patient_age,
                p.sex as patient_sex,
                ds.diagnosis,
                ds.admission_date,
                ds.discharge_date,
                ds.chief_complaints,
                ds.treatment_given,
                ds.medications,
                ds.follow_up_instructions,
                ds.created_at,
                u.username as created_by
            FROM discharge_summaries ds
            JOIN patients p ON ds.patient_id = p.id
            LEFT JOIN users u ON ds.created_by = u.id
            ORDER BY ds.created_at DESC
        """
        cur.execute(query)
    else:
        query = """
            SELECT 
                ds.id,
                p.name as patient_name,
                p.hospital_id,
                p.age as patient_age,
                p.sex as patient_sex,
                ds.diagnosis,
                ds.admission_date,
                ds.discharge_date,
                ds.chief_complaints,
                ds.treatment_given,
                ds.medications,
                ds.follow_up_instructions,
                ds.created_at,
                NULL as created_by
            FROM discharge_summaries ds
            JOIN patients p ON ds.patient_id = p.id
            WHERE ds.created_by = ?
            ORDER BY ds.created_at DESC
        """
        cur.execute(query, (session.get("user_id"),))
    
    rows = cur.fetchall()
    conn.close()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header row
    headers = [
        "ID", "Patient Name", "Hospital ID", "Age", "Sex", 
        "Diagnosis", "Admission Date", "Discharge Date",
        "Chief Complaints", "Treatment Given", "Medications",
        "Follow-up Instructions", "Created At"
    ]
    if session.get("role") == "admin":
        headers.append("Created By")
    
    writer.writerow(headers)
    
    # Data rows
    for row in rows:
        data = [
            row["id"],
            row["patient_name"] or "",
            row["hospital_id"] or "",
            row["patient_age"] or "",
            row["patient_sex"] or "",
            row["diagnosis"] or "",
            row["admission_date"] or "",
            row["discharge_date"] or "",
            row["chief_complaints"] or "",
            row["treatment_given"] or "",
            row["medications"] or "",
            row["follow_up_instructions"] or "",
            row["created_at"] or ""
        ]
        if session.get("role") == "admin":
            data.append(row["created_by"] or "")
        writer.writerow(data)
    
    # Log the export
    try:
        log_action(
            user_id=session.get("user_id"),
            action="EXPORT_CSV",
            details=f"Exported {len(rows)} summaries to CSV"
        )
    except:
        pass
    
    # Create response
    output.seek(0)
    filename = f"discharge_summaries_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

@app.route("/summary/<int:summary_id>/edit", methods=["GET", "POST"])
def edit_summary(summary_id):
    """Edit an existing discharge summary."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get summary with patient info (including new fields)
    cur.execute("""
        SELECT 
            ds.id,
            ds.diagnosis,
            ds.admission_date,
            ds.discharge_date,
            ds.pdf_path,
            ds.created_by,
            ds.patient_id,
            ds.chief_complaints,
            ds.history_of_illness,
            ds.investigations,
            ds.treatment_given,
            ds.medications,
            ds.condition_at_discharge,
            ds.follow_up_instructions,
            p.name as patient_name,
            p.age as patient_age,
            p.sex as patient_sex,
            p.hospital_id
        FROM discharge_summaries ds
        JOIN patients p ON ds.patient_id = p.id
        WHERE ds.id = ?
    """, (summary_id,))
    
    row = cur.fetchone()
    
    if not row:
        conn.close()
        return "Summary not found", 404
    
    # Check access
    if session.get("role") != "admin" and row["created_by"] != session.get("user_id"):
        conn.close()
        return "Access denied", 403
    
    error = None
    
    if request.method == "POST":
        # Get form data
        patient_name = request.form.get("patient_name", "").strip()
        hospital_id = request.form.get("hospital_id", "").strip()
        patient_age = request.form.get("patient_age", "").strip()
        patient_sex = request.form.get("patient_sex", "").strip()
        admission_date = request.form.get("admission_date", "").strip()
        discharge_date = request.form.get("discharge_date", "").strip()
        diagnosis = request.form.get("diagnosis", "").strip()
        
        # New fields
        chief_complaints = request.form.get("chief_complaints", "").strip()
        history_of_illness = request.form.get("history_of_illness", "").strip()
        investigations = request.form.get("investigations", "").strip()
        treatment_given = request.form.get("treatment_given", "").strip()
        medications = request.form.get("medications", "").strip()
        condition_at_discharge = request.form.get("condition_at_discharge", "").strip()
        follow_up_instructions = request.form.get("follow_up_instructions", "").strip()
        
        if not patient_name:
            error = "Patient name is required"
        else:
            # Update patient record
            cur.execute("""
                UPDATE patients 
                SET name = ?, age = ?, sex = ?, hospital_id = ?
                WHERE id = ?
            """, (patient_name, patient_age, patient_sex, hospital_id, row["patient_id"]))
            
            # Update discharge summary with all fields
            cur.execute("""
                UPDATE discharge_summaries 
                SET diagnosis = ?, admission_date = ?, discharge_date = ?,
                    chief_complaints = ?, history_of_illness = ?, investigations = ?,
                    treatment_given = ?, medications = ?, condition_at_discharge = ?,
                    follow_up_instructions = ?
                WHERE id = ?
            """, (diagnosis, admission_date, discharge_date,
                  chief_complaints, history_of_illness, investigations,
                  treatment_given, medications, condition_at_discharge,
                  follow_up_instructions, summary_id))
            
            conn.commit()
            
            # Log the edit
            try:
                log_action(
                    user_id=session.get("user_id"),
                    action="EDIT_SUMMARY",
                    details=f"Edited summary #{summary_id} for patient: {patient_name}"
                )
            except:
                pass
            
            conn.close()
            return redirect(url_for("view_summary", summary_id=summary_id))
    
    conn.close()
    
    # Prepare data for template with all fields
    summary = {
        "id": row["id"],
        "patient_name": row["patient_name"],
        "patient_age": row["patient_age"],
        "patient_sex": row["patient_sex"],
        "hospital_id": row["hospital_id"],
        "admission_date": row["admission_date"] or "",
        "discharge_date": row["discharge_date"] or "",
        "diagnosis": row["diagnosis"] or "",
        "chief_complaints": row["chief_complaints"] or "",
        "history_of_illness": row["history_of_illness"] or "",
        "investigations": row["investigations"] or "",
        "treatment_given": row["treatment_given"] or "",
        "medications": row["medications"] or "",
        "condition_at_discharge": row["condition_at_discharge"] or "",
        "follow_up_instructions": row["follow_up_instructions"] or ""
    }
    
    return render_template("edit_summary.html", summary=summary, error=error)

@app.route("/summary/<int:summary_id>")
def view_summary(summary_id):
    """View a single discharge summary."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get summary with patient info
    cur.execute("""
        SELECT 
            ds.id,
            ds.diagnosis,
            ds.created_at,
            ds.pdf_path,
            ds.created_by,
            p.name as patient_name,
            p.age as patient_age,
            p.sex as patient_sex,
            p.hospital_id
        FROM discharge_summaries ds
        JOIN patients p ON ds.patient_id = p.id
        WHERE ds.id = ?
    """, (summary_id,))
    
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return "Summary not found", 404
    
    # Check access - non-admin can only view their own
    if session.get("role") != "admin" and row["created_by"] != session.get("user_id"):
        return "Access denied", 403
    
    pdf_path = row["pdf_path"]
    if os.path.exists(pdf_path):
        relpath = os.path.relpath(pdf_path, GENERATED_FOLDER).replace("\\", "/")
    else:
        relpath = pdf_path.replace("\\", "/")
    
    try:
        created_at = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
        formatted_date = created_at.strftime("%d-%m-%Y %H:%M")
    except:
        formatted_date = row["created_at"]
    
    summary = {
        "id": row["id"],
        "patient_name": row["patient_name"],
        "patient_age": row["patient_age"],
        "patient_sex": row["patient_sex"],
        "hospital_id": row["hospital_id"],
        "diagnosis": row["diagnosis"] or "",
        "created_at": formatted_date,
        "relpath": relpath
    }
    
    return render_template("view_summary.html", summary=summary)

@app.route("/bulk-delete", methods=["POST"])
def bulk_delete():
    """Delete multiple summaries at once."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    ids_to_delete = request.form.getlist("selected_ids")
    
    if not ids_to_delete:
        return redirect(url_for("summaries"))
    
    conn = get_db()
    cur = conn.cursor()
    deleted_count = 0
    
    for summary_id in ids_to_delete:
        try:
            cur.execute("""
                SELECT ds.id, ds.pdf_path, ds.created_by, p.name as patient_name
                FROM discharge_summaries ds
                JOIN patients p ON ds.patient_id = p.id
                WHERE ds.id = ?
            """, (summary_id,))
            
            row = cur.fetchone()
            if not row:
                continue
            
            if session.get("role") != "admin" and row["created_by"] != session.get("user_id"):
                continue
            
            cur.execute("DELETE FROM discharge_summaries WHERE id = ?", (summary_id,))
            
            pdf_path = row["pdf_path"]
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except:
                    pass
            
            deleted_count += 1
        except:
            continue
    
    conn.commit()
    conn.close()
    
    try:
        log_action(
            user_id=session.get("user_id"),
            action="BULK_DELETE",
            details=f"Deleted {deleted_count} summaries"
        )
    except:
        pass
    
    return redirect(url_for("summaries"))

@app.route("/summary/<int:summary_id>/delete", methods=["POST"])
def delete_summary(summary_id):
    """Delete a discharge summary."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get summary to verify ownership and get PDF path
    cur.execute("""
        SELECT ds.id, ds.pdf_path, ds.created_by, p.name as patient_name
        FROM discharge_summaries ds
        JOIN patients p ON ds.patient_id = p.id
        WHERE ds.id = ?
    """, (summary_id,))
    
    row = cur.fetchone()
    
    if not row:
        conn.close()
        return "Summary not found", 404
    
    # Check access - non-admin can only delete their own
    if session.get("role") != "admin" and row["created_by"] != session.get("user_id"):
        conn.close()
        return "Access denied", 403
    
    # Delete the record
    cur.execute("DELETE FROM discharge_summaries WHERE id = ?", (summary_id,))
    conn.commit()
    
    # Log the deletion
    try:
        log_action(
            user_id=session.get("user_id"),
            action="DELETE_SUMMARY",
            details=f"Deleted summary #{summary_id} for patient: {row['patient_name']}"
        )
    except:
        pass
    
    # Optionally delete the PDF file
    pdf_path = row["pdf_path"]
    if os.path.exists(pdf_path):
        try:
            os.remove(pdf_path)
        except:
            pass  # File deletion is optional
    
    conn.close()
    
    return redirect(url_for("summaries"))

@app.route("/summary/<int:summary_id>/regenerate", methods=["POST"])
def regenerate_pdf(summary_id):
    """Regenerate PDF for an existing summary with current data."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get summary with all fields
    cur.execute("""
        SELECT 
            ds.*,
            p.name as patient_name,
            p.age as patient_age,
            p.sex as patient_sex,
            p.hospital_id
        FROM discharge_summaries ds
        JOIN patients p ON ds.patient_id = p.id
        WHERE ds.id = ?
    """, (summary_id,))
    
    row = cur.fetchone()
    
    if not row:
        conn.close()
        return "Summary not found", 404
    
    # Check access
    if session.get("role") != "admin" and row["created_by"] != session.get("user_id"):
        conn.close()
        return "Access denied", 403
    
    # Build discharge_data dict for PDF generation
    discharge_data = {
        "patient_info": {
            "name": row["patient_name"] or "",
            "age": row["patient_age"] or "",
            "sex": row["patient_sex"] or "",
            "hospital_id": row["hospital_id"] or "",
            "date_of_admission": row["admission_date"] or "",
            "date_of_discharge": row["discharge_date"] or ""
        },
        "diagnosis": {
            "final_diagnosis": row["diagnosis"] or "",
            "chief_complaints": row["chief_complaints"] or "",
            "history_of_present_illness": row["history_of_illness"] or "",
            "investigations": row["investigations"] or "",
            "treatment_given": row["treatment_given"] or "",
            "condition_at_discharge": row["condition_at_discharge"] or "",
            "advice_on_discharge": row["follow_up_instructions"] or ""
        },
        "medications": []  # Parse from medications field if needed
    }
    
    # Parse medications if stored as text
    meds_text = row["medications"] or ""
    if meds_text:
        # Simple parsing - each line is a medication
        for line in meds_text.strip().split("\n"):
            if line.strip():
                discharge_data["medications"].append({
                    "name": line.strip(),
                    "dosage": "",
                    "frequency": "",
                    "duration": ""
                })
    
    # Store old PDF path
    old_pdf_path = row["pdf_path"]
    
    # Generate new PDF
    try:
        new_pdf_path = create_pdf(discharge_data)
        
        # Update database with new PDF path
        cur.execute("""
            UPDATE discharge_summaries 
            SET pdf_path = ?
            WHERE id = ?
        """, (new_pdf_path, summary_id))
        
        conn.commit()
        
        # Log the regeneration
        log_action(
            user_id=session.get("user_id"),
            action="REGENERATE_PDF",
            details=f"Regenerated PDF for summary #{summary_id}, patient: {row['patient_name']}"
        )
        
        # Delete old PDF file if it exists and is different
        if old_pdf_path and old_pdf_path != new_pdf_path and os.path.exists(old_pdf_path):
            try:
                os.remove(old_pdf_path)
            except:
                pass
        
        conn.close()
        
        # Download the new PDF
        return send_file(new_pdf_path, as_attachment=True, download_name=os.path.basename(new_pdf_path))
        
    except Exception as e:
        conn.close()
        return f"Error generating PDF: {str(e)}", 500

@app.route("/templates")
def templates():
    """List summary templates."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get user's templates (and admin templates for all users)
    if session.get("role") == "admin":
        cur.execute("""
            SELECT * FROM summary_templates ORDER BY created_at DESC
        """)
    else:
        cur.execute("""
            SELECT * FROM summary_templates WHERE created_by = ? ORDER BY created_at DESC
        """, (session.get("user_id"),))
    
    templates_list = [dict(row) for row in cur.fetchall()]
    conn.close()
    
    return render_template("templates.html", templates=templates_list)

@app.route("/templates/create", methods=["POST"])
def create_template():
    """Create a new summary template."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("templates"))
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO summary_templates (name, diagnosis, chief_complaints, treatment_given, medications, follow_up_instructions, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        request.form.get("diagnosis", "").strip(),
        request.form.get("chief_complaints", "").strip(),
        request.form.get("treatment_given", "").strip(),
        request.form.get("medications", "").strip(),
        request.form.get("follow_up_instructions", "").strip(),
        session.get("user_id")
    ))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for("templates"))

@app.route("/templates/<int:template_id>/delete", methods=["POST"])
def delete_template(template_id):
    """Delete a summary template."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Check ownership
    cur.execute("SELECT created_by FROM summary_templates WHERE id = ?", (template_id,))
    row = cur.fetchone()
    
    if row and (session.get("role") == "admin" or row["created_by"] == session.get("user_id")):
        cur.execute("DELETE FROM summary_templates WHERE id = ?", (template_id,))
        conn.commit()
    
    conn.close()
    return redirect(url_for("templates"))

@app.route("/templates/<int:template_id>/use")
def use_template(template_id):
    """Create a new summary using a template - stores in session and redirects to create page."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM summary_templates WHERE id = ?", (template_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return redirect(url_for("templates"))
    
    # Store template data in session for the create page
    session["template_data"] = {
        "diagnosis": row["diagnosis"] or "",
        "chief_complaints": row["chief_complaints"] or "",
        "treatment_given": row["treatment_given"] or "",
        "medications": row["medications"] or "",
        "follow_up_instructions": row["follow_up_instructions"] or ""
    }
    
    return redirect(url_for("index"))

@app.route("/summary/<int:summary_id>/print")
def print_summary(summary_id):
    """Open the PDF file for viewing/printing."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get summary with pdf_path
    cur.execute("""
        SELECT ds.pdf_path, ds.created_by
        FROM discharge_summaries ds
        WHERE ds.id = ?
    """, (summary_id,))
    
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return "Summary not found", 404
    
    # Check access
    if session.get("role") != "admin" and row["created_by"] != session.get("user_id"):
        return "Access denied", 403
    
    pdf_path = row["pdf_path"]
    
    # Check if file exists
    if not pdf_path or not os.path.exists(pdf_path):
        return "PDF file not found", 404
    
    # Serve the PDF inline (opens in browser for viewing/printing)
    return send_file(pdf_path, mimetype='application/pdf')

@app.route("/profile", methods=["GET", "POST"])
def profile():
    """User profile page with password change."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    user_id = session.get("user_id")
    conn = get_db()
    cur = conn.cursor()
    
    error = None
    success = None
    
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        # Get current password hash
        cur.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        
        if not row:
            error = "User not found"
        elif not verify_password(current_password, row["password_hash"]):
            error = "Current password is incorrect"
        elif new_password != confirm_password:
            error = "New passwords do not match"
        else:
            # Validate new password
            is_valid, msg = validate_password(new_password)
            if not is_valid:
                error = msg
            else:
                # Update password
                new_hash = hash_password(new_password)
                cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
                conn.commit()
                
                # Log the change
                try:
                    log_action(user_id=user_id, action="PASSWORD_CHANGE", details="User changed their own password")
                except:
                    pass
                
                success = "Password updated successfully!"
    
    # Get user info
    cur.execute("SELECT username, role, created_at FROM users WHERE id = ?", (user_id,))
    user_row = cur.fetchone()
    
    # Get stats
    cur.execute("SELECT COUNT(*) as count FROM discharge_summaries WHERE created_by = ?", (user_id,))
    stats_row = cur.fetchone()
    
    conn.close()
    
    user = {
        "username": user_row["username"],
        "role": user_row["role"],
        "created_at": user_row["created_at"]
    }
    
    stats = {
        "total_summaries": stats_row["count"] if stats_row else 0
    }
    
    return render_template("profile.html", user=user, stats=stats, error=error, success=success)

@app.route("/admin/add_user", methods=["GET", "POST"])
def add_user():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        return "Access denied", 403

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "doctor")

        if not username or not password:
            return render_template("add_user.html", error="Username and password are required")

        password_hash = hash_password(password)

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO users (username, password_hash, role, must_change_password)
                VALUES (?, ?, ?, 1)
            """, (username, password_hash, role))

            conn.commit()

            log_action(
                user_id=session["user_id"],
                action="CREATE_USER",
                details=f"Created user '{username}' with role '{role}'"
            )

            conn.close()
            return redirect(url_for("manage_users"))

        except Exception as e:
            conn.close()
            return render_template("add_user.html", error=f"Error creating user: {str(e)}")

    return render_template("add_user.html")

@app.route("/", methods=["GET", "POST"])
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        audio_file = request.files.get("audio")
        if not audio_file or audio_file.filename == '':
            stats = get_dashboard_stats(session.get("user_id"))
            return render_template("index.html", stats=stats, error="No file uploaded.")

        if model is None:
            stats = get_dashboard_stats(session.get("user_id"))
            return render_template("index.html", stats=stats, error="Whisper model not loaded. Please check server configuration.")

        # Save uploaded file with timestamp to avoid conflicts
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_filename = audio_file.filename
        # Sanitize filename - remove special characters
        safe_base = re.sub(r'[^A-Za-z0-9_.-]', '_', original_filename)
        safe_filename = f"{timestamp}_{safe_base}"
        file_path = os.path.join(UPLOAD_FOLDER, safe_filename)
        
        # Ensure uploads folder exists
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        try:
            audio_file.save(file_path)
            print(f"✓ Audio file saved to: {file_path}")
            print(f"✓ File exists: {os.path.exists(file_path)}")
            print(f"✓ File size: {os.path.getsize(file_path)} bytes")
        except Exception as e:
            stats = get_dashboard_stats(session.get("user_id"))
            print(f"✗ Error saving file: {e}")
            return render_template("index.html", stats=stats, error=f"Error saving file: {str(e)}")

        # Transcribe
        transcript = None
        try:
            print(f"🎤 Starting transcription of: {file_path}")
            
            # Verify file exists before transcription
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Audio file not found at: {file_path}")
            
            # Use absolute path for Whisper
            absolute_path = os.path.abspath(file_path)
            print(f"🎤 Absolute path: {absolute_path}")
            
            transcript = model.transcribe(absolute_path, language="en", fp16=False)["text"]
            print(f"✓ Transcription completed: {len(transcript)} characters")
        except Exception as e:
            stats = get_dashboard_stats(session.get("user_id"))
            print(f"✗ Transcription error: {e}")
            print(f"✗ File path attempted: {file_path}")
            print(f"✗ File exists check: {os.path.exists(file_path)}")
            # Try to clean up file even if transcription failed
            try:
                if os.path.exists(file_path):
                    time.sleep(0.5)
                    os.remove(file_path)
            except:
                pass
            return render_template("index.html", stats=stats, error=f"Error transcribing audio: {str(e)}")

        # Clean up audio file after successful transcription
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                if os.path.exists(file_path):
                    time.sleep(0.5)
                    os.remove(file_path)
                    log_action(
                        user_id=session.get("user_id"),
                        action="AUDIO_DELETED",
                        details=f"Audio file {original_filename} deleted after transcription"
                    )
                    break
            except Exception as e:
                if attempt == max_attempts - 1:
                    print(f"Audio delete failed after {max_attempts} attempts: {e}")
                else:
                    time.sleep(1)

        # Generate discharge data
        try:
            discharge_data = generate_discharge_json_from_transcript(transcript)
        except Exception as e:
            stats = get_dashboard_stats(session.get("user_id"))
            return render_template("index.html", stats=stats, error=f"Error generating summary: {str(e)}")

        return render_template(
            "summary.html",
            transcript=transcript,
            discharge_data=discharge_data,
        )

    # GET request - show dashboard with user-specific stats
    user_id = session.get("user_id")
    stats = get_dashboard_stats(user_id if session.get("role") != "admin" else None)
    return render_template("index.html", stats=stats)

def create_pdf(discharge_data: dict) -> str:
    """Create a discharge summary PDF and return its file path."""
    import textwrap
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    
    now = datetime.now()
    year_folder = f"Year {now.year}"
    month_folder = now.strftime("%B")
    day_folder = now.strftime("%d-%m-%Y")

    patient_info = discharge_data.get("patient_info") or {}
    raw_name = patient_info.get("name") or "patient"

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw_name)).strip("_")
    if not safe_name:
        safe_name = "patient"

    filename = f"{safe_name}_{now.strftime('%Y%m%d_%H%M%S')}.pdf"

    folder = os.path.join(GENERATED_FOLDER, year_folder, month_folder, day_folder)
    os.makedirs(folder, exist_ok=True)

    pdf_path = os.path.join(folder, filename)

    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4
    left_margin = 50
    right_margin = width - 50
    text_width = right_margin - left_margin
    y = height - 50

    def wrap_text(text, max_chars=85):
        """Wrap text to prevent cutoff."""
        if not text:
            return []
        return textwrap.wrap(str(text), width=max_chars)

    def line(text: str, bold: bool = False, indent: int = 0):
        nonlocal y
        if y < 80:
            c.showPage()
            _reset_page_style()
            y = height - 60
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString(left_margin + indent, y, str(text))
        y -= 14

    def wrapped_line(label: str, text: str, indent: int = 0):
        """Draw wrapped text with label."""
        nonlocal y
        if not text:
            line(f"{label}: -", indent=indent)
            return
        
        lines = wrap_text(f"{label}: {text}")
        for i, ln in enumerate(lines):
            if y < 80:
                c.showPage()
                _reset_page_style()
                y = height - 60
            c.setFont("Helvetica", 10)
            c.drawString(left_margin + indent + (10 if i > 0 else 0), y, ln)
            y -= 14

    def section_header(title: str):
        """Draw a section header with underline."""
        nonlocal y
        if y < 100:
            c.showPage()
            _reset_page_style()
            y = height - 60
        y -= 6
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left_margin, y, title)
        y -= 4
        c.setStrokeColorRGB(0.3, 0.3, 0.3)
        c.line(left_margin, y, left_margin + 150, y)
        c.setStrokeColorRGB(0, 0, 0)
        y -= 14

    def s(d: dict, key: str) -> str:
        if d is None:
            return ""
        v = d.get(key, "")
        return "" if v is None else str(v)

    def _reset_page_style():
        nonlocal y
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.5)
        y = height - 60

    _reset_page_style()

    # Header
    cx = width / 2.0
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(cx, y, "DISCHARGE SUMMARY")
    y -= 8
    c.setLineWidth(1)
    c.line(left_margin, y, right_margin, y)
    y -= 20

    # Get data
    p = discharge_data.get("patient_info") or {}
    d = discharge_data.get("diagnosis") or {}
    h = discharge_data.get("history") or {}
    fup = discharge_data.get("follow_up") or {}
    doc = discharge_data.get("doctor") or {}
    meds = discharge_data.get("medications") or []

    # Patient Information Section
    section_header("Patient Information")
    line(f"Name: {s(p, 'name')}")
    line(f"Age / Sex: {s(p, 'age')} / {s(p, 'sex')}")
    line(f"Hospital ID: {s(p, 'hospital_id')}")
    ward = s(p, 'ward')
    bed = s(p, 'bed_number')
    if ward or bed:
        line(f"Ward / Bed: {ward} / {bed}")
    line(f"Date of Admission: {s(p, 'date_of_admission')}")
    line(f"Date of Discharge: {s(p, 'date_of_discharge')}")
    y -= 8

    # Diagnosis Section
    section_header("Diagnosis")
    prov_diag = s(d, 'provisional_diagnosis')
    final_diag = s(d, 'final_diagnosis')
    if prov_diag:
        wrapped_line("Provisional", prov_diag)
    if final_diag:
        wrapped_line("Final", final_diag)
    y -= 8

    # History & Hospital Course Section
    section_header("History & Hospital Course")
    presenting = s(h, 'presenting_complaints') or s(d, 'chief_complaints')
    if presenting:
        wrapped_line("Presenting complaints", presenting)
    
    hopi = s(h, 'history_of_presenting_illness') or s(d, 'history_of_present_illness')
    if hopi:
        wrapped_line("HOPI", hopi)
    
    past_history = s(h, 'past_medical_history')
    if past_history:
        wrapped_line("Past history", past_history)
    
    hospital_course = discharge_data.get('hospital_course', '')
    if hospital_course:
        wrapped_line("Hospital course", hospital_course)
    y -= 8

    # Medications Section - Using Table with text wrapping
    section_header("Medications")
    if not meds:
        line("None prescribed")
    else:
        from reportlab.platypus import Paragraph
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        
        # Create styles for table cells
        styles = getSampleStyleSheet()
        cell_style = ParagraphStyle(
            'CellStyle',
            parent=styles['Normal'],
            fontSize=9,
            leading=11,
            wordWrap='CJK'  # Allow word wrap
        )
        header_style = ParagraphStyle(
            'HeaderStyle',
            parent=styles['Normal'],
            fontSize=9,
            leading=11,
            fontName='Helvetica-Bold'
        )
        
        # Build table data with Paragraphs for wrapping
        table_data = [[
            Paragraph("<b>Drug Name</b>", header_style),
            Paragraph("<b>Dose</b>", header_style),
            Paragraph("<b>Frequency</b>", header_style),
            Paragraph("<b>Duration</b>", header_style)
        ]]
        
        for m in meds:
            drug = m.get("drug_name") or m.get("name") or ""
            dose = m.get("dose") or m.get("dosage") or ""
            freq = m.get("frequency") or ""
            duration = m.get("duration") or ""
            remarks = m.get("remarks") or ""
            
            # Combine drug and remarks if exists
            drug_display = drug
            if remarks:
                drug_display += f" ({remarks})"
            
            table_data.append([
                Paragraph(drug_display, cell_style),
                Paragraph(str(dose), cell_style),
                Paragraph(str(freq), cell_style),
                Paragraph(str(duration), cell_style)
            ])
        
        # Create table with wider columns
        col_widths = [160, 70, 150, 80]  # Total ~460, fits A4 with margins
        table = Table(table_data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        
        # Calculate table size and check for page break
        table_w, table_h = table.wrap(text_width, height)
        if y - table_h < 80:
            c.showPage()
            _reset_page_style()
            y = height - 60
        
        # Draw table
        table.drawOn(c, left_margin, y - table_h)
        y -= table_h + 15
    
    y -= 8

    # Follow-up Section
    section_header("Follow-up")
    fup_date = s(fup, 'date')
    fup_dept = s(fup, 'department')
    fup_instr = s(fup, 'special_instructions') or s(d, 'advice_on_discharge')
    
    if fup_date:
        line(f"Date: {fup_date}")
    if fup_dept:
        line(f"Department: {fup_dept}")
    if fup_instr:
        wrapped_line("Special instructions", fup_instr)
    y -= 8

    # Doctor Section
    section_header("Doctor")
    doc_name = s(doc, 'name')
    doc_desig = s(doc, 'designation')
    doc_reg = s(doc, 'registration_number')
    
    if doc_name:
        line(f"Name: {doc_name}")
    if doc_desig:
        line(f"Designation: {doc_desig}")
    if doc_reg:
        line(f"Registration No.: {doc_reg}")

    # Signature area at bottom
    if y < 120:
        c.showPage()
        _reset_page_style()

    sig_y = 70
    c.setLineWidth(0.5)
    c.line(left_margin, sig_y, left_margin + 150, sig_y)
    c.setFont("Helvetica", 9)
    c.drawString(left_margin, sig_y - 12, "Treating Consultant")

    c.line(right_margin - 150, sig_y, right_margin, sig_y)
    c.drawString(right_margin - 150, sig_y - 12, "Medical Superintendent")

    c.save()
    print("PDF written to:", pdf_path)
    return pdf_path

@app.route("/generate_pdf", methods=["POST"])
def generate_pdf():
    if "user_id" not in session:
        return redirect(url_for("login"))

    form = request.form

    def fv(*names):
        for n in names:
            v = form.get(n)
            if v is not None and v != "":
                return v
        return ""

    discharge_data = {
        "patient_info": {
            "name": fv("patient_name", "patient_info__name"),
            "age": fv("patient_age", "patient_info__age"),
            "sex": fv("patient_sex", "patient_info__sex"),
            "hospital_id": fv("hospital_id", "patient_info__hospital_id"),
            "ward": fv("ward", "patient_info__ward"),
            "bed_number": fv("bed_number", "patient_info__bed_number"),
            "date_of_admission": fv("date_of_admission", "patient_info__date_of_admission"),
            "date_of_discharge": fv("date_of_discharge", "patient_info__date_of_discharge"),
        },
        "diagnosis": {
            "provisional_diagnosis": fv("provisional_diagnosis", "diagnosis__provisional_diagnosis"),
            "final_diagnosis": fv("final_diagnosis", "diagnosis__final_diagnosis"),
        },
        "history": {
            "presenting_complaints": fv("presenting_complaints", "history__presenting_complaints"),
            "history_of_presenting_illness": fv("history_of_presenting_illness", "history__history_of_presenting_illness"),
            "past_medical_history": fv("past_medical_history", "history__past_medical_history"),
        },
        "hospital_course": fv("hospital_course", "hospital_course"),
        "follow_up": {
            "date": fv("follow_up_date", "follow_up__date"),
            "department": fv("follow_up_department", "follow_up__department"),
            "doctor": fv("follow_up_doctor", "follow_up__doctor"),
            "special_instructions": fv("follow_up_instructions", "follow_up__special_instructions"),
        },
        "doctor": {
            "name": fv("doctor_name", "doctor__name"),
            "designation": fv("doctor_designation", "doctor__designation"),
            "registration_number": fv("doctor_reg_no", "doctor__registration_number"),
        },
        "medications": [
            {
                "drug_name": dn,
                "dose": dd,
                "frequency": fq,
                "duration": du,
                "remarks": rm,
                "route": ""
            }
            for dn, dd, fq, du, rm in zip(
                request.form.getlist("drug_name"),
                request.form.getlist("dose"),
                request.form.getlist("frequency"),
                request.form.getlist("duration"),
                request.form.getlist("remarks")
            )
            if dn.strip() != ""
        ],
    }

    try:
        pdf_path = create_pdf(discharge_data)
    except Exception as e:
        print(f"Error creating PDF: {e}")
        return f"Error creating PDF: {str(e)}", 500

    # Save to database with user tracking
    try:

        conn = get_db()
        cur = conn.cursor()

        p = discharge_data["patient_info"]
        user_id = session.get("user_id")

        # Check if patient exists for THIS user
        cur.execute("""
            SELECT id FROM patients 
            WHERE hospital_id = ? AND created_by = ?
        """, (p["hospital_id"], user_id))
        row = cur.fetchone()

        if row:
            patient_id = row["id"]
        else:
            # Create new patient linked to current user
            cur.execute("""
                INSERT INTO patients (name, age, sex, hospital_id, created_by)
                VALUES (?, ?, ?, ?, ?)
            """, (p["name"], p["age"], p["sex"], p["hospital_id"], user_id))
            patient_id = cur.lastrowid

        # Create discharge summary linked to user
        cur.execute("""
            INSERT INTO discharge_summaries (
                patient_id,
                diagnosis,
                admission_date,
                discharge_date,
                pdf_path,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            patient_id,
            discharge_data["diagnosis"]["final_diagnosis"],
            p["date_of_admission"],
            p["date_of_discharge"],
            pdf_path,
            user_id,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))

        conn.commit()
        conn.close()

        log_action(
            user_id=user_id,
            action="GENERATE_DISCHARGE",
            details=f"PDF generated for patient {p['name']} (Hospital ID: {p['hospital_id']})"
        )
    except Exception as e:
        import traceback
        print(f"DATABASE ERROR: {e}")
        traceback.print_exc()
        flash(f"PDF generated but database save failed: {str(e)}", "error")

    return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path))


if __name__ == "__main__":
    init_db()
    create_default_admin()
    app.run(debug=True)