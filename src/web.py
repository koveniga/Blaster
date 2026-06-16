from flask import Flask, send_file
import os

app = Flask(__name__)

@app.route("/")
def return_metrics():
    path_to_file = os.getenv('METRICS_RESULT', './metrics')
    return send_file(path_to_file)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)