from flask import Flask, render_template, request, jsonify, Response
import subprocess
from gera_runner import run_gera
from refresh_runners.refresh_alsea_runner import run_refresh_alsea
from upload_runner import run_upload

app = Flask(__name__)

companies = [
    {"id": "box1", "name": "Santander"},
    {"id": "box2", "name": "Grupo Petersen"},
    {"id": "box3", "name": "Alsea"},
    {"id": "box4", "name": "La Anónima"},
    {"id": "box5", "name": "ICBC"},
]

@app.route("/")
def index():
    return render_template("index.html", companies=companies)

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
        for line in run_gera():
            yield f"data: {line}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/stream-refresh")
def stream_refresh():
    client = request.args.get("selectedId")
    company_name = next((c["name"] for c in companies if c["id"] == client), None)
    def event_stream(client):
        for line in run_refresh_alsea(client):
            yield f"data: {line}\n\n"
    return Response(event_stream(client), mimetype="text/event-stream")

@app.route("/stream-upload")
def stream_upload():
    def event_stream():
        for line in run_upload():
            yield f"data: {line}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(debug=True)
