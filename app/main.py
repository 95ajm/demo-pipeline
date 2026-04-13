from flask import Flask, jsonify
from datetime import datetime, timezone

app = Flask(__name__)


@app.route("/")
def hello():
    return "Hello World"


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
