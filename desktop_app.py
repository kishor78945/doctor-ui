import os
import sys
import webview
from app import app, init_db

# Configuration
# (WebView will pick a random port automatically)

def on_closed():
    """Shutdown logic."""
    print("[INFO] Application window closed")
    os._exit(0)

if __name__ == '__main__':
    # Fix paths when running as PyInstaller executable
    if getattr(sys, 'frozen', False):
        import db
        import app as flask_app
        
        # Use executable directory as base
        base_dir = os.path.dirname(sys.executable)
        
        # Update DB path
        db.BASE_DIR = base_dir
        db.DB_PATH = os.path.join(base_dir, "hospital.db")
        
        # Update App paths
        flask_app.BASE_DIR = base_dir
        flask_app.UPLOAD_FOLDER = os.path.join(base_dir, "uploads")
        flask_app.GENERATED_FOLDER = os.path.join(base_dir, "generated")
        
        # Ensure directories exist
        os.makedirs(flask_app.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(flask_app.GENERATED_FOLDER, exist_ok=True)

    # Initialize database
    print("[INFO] Initializing database...")
    init_db()
    
    # Create the window with Flask App directly
    print("[INFO] Creating application window with embedded Flask server...")
    webview.create_window(
        'Discharge Summary Generator', 
        app,
        width=1200,
        height=800
    )
    
    
    # Start the native GUI loop
    print("[INFO] Starting GUI loop...")
    try:
        # debug=True allows F12 dev tools and detailed logs
        webview.start(debug=True)
    except Exception as e:
        print(f"[ERROR] GUI Loop crashed: {e}")
        import traceback
        traceback.print_exc()
