"""
Script to import orphaned PDF files from the generated folder into the database.
This recovers summaries that were generated before the database was reset.
"""
import os
import re
import sqlite3
from datetime import datetime

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATED_FOLDER = os.path.join(BASE_DIR, "generated")
DB_PATH = os.path.join(BASE_DIR, "hospital.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def scan_pdf_files():
    """Scan the generated folder for all PDF files."""
    pdf_files = []
    
    for root, dirs, files in os.walk(GENERATED_FOLDER):
        for file in files:
            if file.endswith('.pdf'):
                full_path = os.path.join(root, file)
                pdf_files.append(full_path)
    
    return pdf_files

def extract_info_from_pdf(pdf_path):
    """
    Extract patient name and timestamp from PDF filename.
    Expected format: PatientName_YYYYMMDD_HHMMSS.pdf
    Example: Rajesh_Kumar_20251210_182341.pdf
    """
    filename = os.path.basename(pdf_path)
    name_without_ext = filename.replace('.pdf', '')
    
    # Pattern: name parts followed by _YYYYMMDD_HHMMSS
    pattern = r'^(.+)_(\d{8})_(\d{6})$'
    match = re.match(pattern, name_without_ext)
    
    if match:
        name_parts = match.group(1).replace('_', ' ')
        date_str = match.group(2)
        time_str = match.group(3)
        
        # Parse the datetime
        try:
            dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
            created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "patient_name": name_parts,
            "created_at": created_at
        }
    else:
        # Fallback: use filename as patient name
        return {
            "patient_name": name_without_ext.replace('_', ' '),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

def get_existing_pdf_paths():
    """Get list of PDF paths already in database."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT pdf_path FROM discharge_summaries")
    rows = cur.fetchall()
    conn.close()
    
    return set(row["pdf_path"] for row in rows)

def import_orphaned_pdfs(user_id=1):
    """
    Import orphaned PDFs into the database.
    Assigns them to a specific user (default: admin with id=1).
    """
    conn = get_db()
    cur = conn.cursor()
    
    # Get all PDFs on filesystem
    all_pdfs = scan_pdf_files()
    print(f"Found {len(all_pdfs)} PDF files in generated folder")
    
    # Get PDFs already in database
    existing_pdfs = get_existing_pdf_paths()
    print(f"Found {len(existing_pdfs)} PDFs already in database")
    
    # Find orphaned PDFs
    orphaned_pdfs = [p for p in all_pdfs if p not in existing_pdfs]
    print(f"Found {len(orphaned_pdfs)} orphaned PDFs to import")
    
    imported_count = 0
    
    for pdf_path in orphaned_pdfs:
        info = extract_info_from_pdf(pdf_path)
        print(f"\nImporting: {os.path.basename(pdf_path)}")
        print(f"  Patient: {info['patient_name']}")
        print(f"  Created: {info['created_at']}")
        
        try:
            # Generate a hospital ID from the filename
            filename = os.path.basename(pdf_path).replace('.pdf', '')
            hospital_id = f"IMPORTED-{filename[-12:]}"  # Use last 12 chars as ID
            
            # Create or find patient
            cur.execute("""
                SELECT id FROM patients 
                WHERE name = ? AND created_by = ?
            """, (info["patient_name"], user_id))
            patient_row = cur.fetchone()
            
            if patient_row:
                patient_id = patient_row["id"]
            else:
                cur.execute("""
                    INSERT INTO patients (name, age, sex, hospital_id, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (info["patient_name"], "Unknown", "Unknown", hospital_id, user_id, info["created_at"]))
                patient_id = cur.lastrowid
            
            # Create discharge summary
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
                "Imported from file",
                "",
                "",
                pdf_path,
                user_id,
                info["created_at"]
            ))
            
            imported_count += 1
            print(f"  [OK] Imported successfully")
            
        except Exception as e:
            print(f"  [ERROR] {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"Import complete! {imported_count} PDFs imported.")
    print(f"{'='*50}")
    
    return imported_count

if __name__ == "__main__":
    print("="*50)
    print("PDF Recovery Script")
    print("="*50)
    print(f"\nGenerated folder: {GENERATED_FOLDER}")
    print(f"Database: {DB_PATH}")
    
    # Get user to assign the PDFs to
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM users WHERE role = 'admin' LIMIT 1")
    admin = cur.fetchone()
    conn.close()
    
    if admin:
        print(f"\nImporting PDFs assigned to user: {admin['username']} (ID: {admin['id']})")
        import_orphaned_pdfs(admin["id"])
    else:
        print("\nNo admin user found. Creating default assignment to user_id=1")
        import_orphaned_pdfs(1)
