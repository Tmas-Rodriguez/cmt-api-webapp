import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename

import subprocess
import shutil
import zipfile
from pathlib import Path
from gera_runner import run_gera
from refresh_runners.refresh_runner import run_refresh
from upload_runner import run_upload
from file_handler import save_file_to_organized_folder

app = Flask(__name__)

# Configuration for file uploads
UPLOAD_FOLDER = r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API\CurrentData\providers"
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
gera_state = {"output_folder": None, "start_time": None}

companies = [
    {"id": "box1", "name": "Santander"},
    {"id": "box2", "name": "Grupo Petersen"},
    {"id": "box3", "name": "Alsea"},
    {"id": "box4", "name": "La Anónima"},
    {"id": "box5", "name": "Mostaza"},
]

app.secret_key = "supersecret"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Dummy user (replace with DB lookup)
class User(UserMixin):
    def __init__(self, id, name, password):
        self.id = id
        self.name = name
        self.password = password

users = {
    "tomi": User(id="tomi", name="Tomi", password="tomipass"),
    "marce": User(id="marce", name="Marce", password="marcepass"),
    "santi": User(id="santi", name="Santi", password="santipass"),
}

@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)

@app.route("/")
@login_required
def index():
    return render_template("index.html", companies=companies)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = users.get(username)
        if user and password == user.password:
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()
    selected_id = data.get("selected_id")
    selected_name = next((c["name"] for c in companies if c["id"] == selected_id), None)

    if not selected_name:
        return jsonify({"status": "error", "message": "Invalid selection"})

    try:
        # Run task-manager.py and pass selected_name as argument
        result = subprocess.run(
            ["python", "task-manager.py", selected_name],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        output = f"Error running task-manager.py: {e.stderr.strip()}"

    return jsonify({"status": "success", "selected_id": selected_id, "output": output})

@app.route("/stream-gera")
def stream_gera():
    def event_stream():
        try:
            for line in run_gera():
                if line.startswith("__GERA_COMPLETED__:"):
                    # Parse the completion signal: folder_path|start_time
                    parts = line.split(":", 1)[1].strip().split("|")
                    folder_path = parts[0]
                    start_time = float(parts[1]) if len(parts) > 1 else None
                    gera_state["output_folder"] = folder_path
                    gera_state["start_time"] = start_time
                    print(f"DEBUG: Gera completed. Output folder: {folder_path}, Start time: {start_time}", flush=True)
                    yield f"data: {{'status': 'completed', 'message': 'Script completed successfully'}}\n\n"
                elif line.startswith("__GERA_ERROR__:"):
                    error_code = line.split(":", 1)[1].strip()
                    print(f"DEBUG: Gera error: {error_code}", flush=True)
                    yield f"data: {{'status': 'error', 'message': 'Script failed with code: {error_code}'}}\n\n"
                else:
                    yield f"data: {line}\n\n"
        except Exception as e:
            print(f"DEBUG: Stream error: {str(e)}", flush=True)
            yield f"data: {{'status': 'error', 'message': 'Stream error: {str(e)}'}}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/stream-refresh")
def stream_refresh():
    client = request.args.get("selectedId")
    company_name = next((c["name"] for c in companies if c["id"] == client), None)
    def event_stream(client, user):
        for line in run_refresh(client, user):
            yield f"data: {line}\n\n"
    return Response(event_stream(client, current_user.name), mimetype="text/event-stream")

@app.route("/stream-upload")
def stream_upload():
    def event_stream():
        for line in run_upload():
            yield f"data: {line}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/download-gera-files")
@login_required
def download_gera_files():
    """Download only the new files generated in the last gera run as a ZIP"""
    
    output_folder = gera_state.get("output_folder")
    start_time = gera_state.get("start_time")
    
    print(f"DEBUG: Download requested. output_folder = {output_folder}, start_time = {start_time}", flush=True)
    
    if not output_folder:
        print("DEBUG: output_folder is None or empty", flush=True)
        return jsonify({"status": "error", "message": "No output folder set. Please run validation first."}), 400
    
    if not os.path.exists(output_folder):
        print(f"DEBUG: Output folder does not exist: {output_folder}", flush=True)
        return jsonify({"status": "error", "message": f"Output folder does not exist: {output_folder}"}), 400
    
    try:
        # Create a temporary ZIP file
        zip_path = os.path.join(os.path.dirname(__file__), "validation_errors.zip")
        
        # Remove old ZIP if it exists
        if os.path.exists(zip_path):
            os.remove(zip_path)
        
        print(f"DEBUG: Creating ZIP file at {zip_path}", flush=True)
        
        # Create ZIP file with only files modified after start_time
        file_count = 0
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Get file modification time
                    file_mtime = os.path.getmtime(file_path)
                    
                    # Only include files modified after the script start time
                    # Add a small buffer (2 seconds before start_time) to account for timing variations
                    if start_time is None or file_mtime >= (start_time - 2):
                        arcname = os.path.relpath(file_path, output_folder)
                        zipf.write(file_path, arcname)
                        file_count += 1
                        print(f"DEBUG: Including file: {arcname} (mtime: {file_mtime}, start_time: {start_time})", flush=True)
                    else:
                        print(f"DEBUG: Skipping old file: {file} (mtime: {file_mtime}, start_time: {start_time})", flush=True)
        
        print(f"DEBUG: ZIP created successfully with {file_count} files", flush=True)
        
        # Send the file
        return send_file(
            zip_path,
            as_attachment=True,
            download_name="validation_errors.zip",
            mimetype="application/zip"
        )
    
    except Exception as e:
        print(f"DEBUG: Exception in download: {str(e)}", flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/upload-files", methods=["POST"])
@login_required
def upload_files():
    """Upload Excel files to the configured upload folder with organized subfolder structure"""
    
    if 'files' not in request.files:
        return jsonify({"status": "error", "message": "No files provided"}), 400
    
    files = request.files.getlist('files')
    
    if not files or len(files) == 0:
        return jsonify({"status": "error", "message": "No files provided"}), 400
    
    uploaded_files = []
    errors = []
    
    for file in files:
        if file.filename == '':
            errors.append("Empty filename")
            continue
        
        if not allowed_file(file.filename):
            errors.append(f"{file.filename} - Invalid file type")
            continue
        
        try:
            # Use file_handler to save file to organized folder
            success, message, file_path = save_file_to_organized_folder(file, app.config['UPLOAD_FOLDER'])
            
            if success:
                uploaded_files.append(f"{file.filename} ({message})")
                print(f"DEBUG: File uploaded: {file.filename} to {file_path}", flush=True)
            else:
                errors.append(f"{file.filename} - {message}")
                print(f"DEBUG: Error uploading {file.filename}: {message}", flush=True)
        except Exception as e:
            errors.append(f"{file.filename} - {str(e)}")
            print(f"DEBUG: Error uploading {file.filename}: {str(e)}", flush=True)
    
    if uploaded_files:
        return jsonify({
            "status": "success",
            "message": f"Successfully uploaded {len(uploaded_files)} file(s)",
            "uploaded_files": uploaded_files,
            "errors": errors if errors else None
        }), 200
    else:
        return jsonify({
            "status": "error",
            "message": "No files were uploaded",
            "errors": errors
        }), 400

if __name__ == "__main__":
    app.run(debug=True)
