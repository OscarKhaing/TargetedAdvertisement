import os

from flask import Flask, jsonify, send_file
app = Flask(__name__)

@app.get('/')
def index():
    #return "<p> Hello, world!</p>"
    return send_file(os.path.join(app.root_path, 'boogle.html'), mimetype='text/html')

@app.get('/jsontest')
def jsontest():
    return jsonify({"data": "I am in CSE190/CSE291!"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port="8000")

