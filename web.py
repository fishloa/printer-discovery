#!/usr/bin/env python3
"""Web service for drag-and-drop label printing to a Zebra ZD621."""

from flask import Flask, request, jsonify, send_from_directory
from label_printer import process_and_preview, process_and_print, get_printer_config
import base64

app = Flask(__name__, static_folder="static")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/preview", methods=["POST"])
def preview():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    file_bytes = f.read()
    try:
        png_bytes = process_and_preview(file_bytes, f.filename)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return jsonify({"image": f"data:image/png;base64,{b64}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/print", methods=["POST"])
def print_label():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    file_bytes = f.read()
    try:
        printer = get_printer_config()
        process_and_print(file_bytes, f.filename, ip=printer["ip"])
        return jsonify({"status": "ok", "printer": printer["name"], "ip": printer["ip"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    printer = get_printer_config()
    print(f"Printer: {printer['name']} at {printer['ip']}")
    app.run(host="0.0.0.0", port=5555, debug=True)
