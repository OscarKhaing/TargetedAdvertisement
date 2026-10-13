import os
import uuid
from collections import defaultdict
from datetime import timezone

import psycopg
from flask import Flask, jsonify, redirect, request, send_file
app = Flask(__name__)
app.json.sort_keys = False

# Default: the local "boogle" database over the unix socket, logging in as the OS user.
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql:///boogle')

COOKIE_NAME = 'boogle_id'
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

def db():
    # "with db() as conn:" is one transaction. now() is fixed for the whole transaction,
    # so every row a single request writes carries the same timestamp.
    return psycopg.connect(DATABASE_URL)

def get_client_id():
    value = request.cookies.get(COOKIE_NAME, '')
    try:
        client_id = uuid.UUID(value)
    except ValueError:
        return None
    return client_id if str(client_id) == value else None

def log_request(conn, client_id):
    if client_id is not None:
        conn.execute(
            "INSERT INTO clients (client_id) VALUES (%s) "
            "ON CONFLICT (client_id) DO UPDATE SET last_seen = now()",
            (client_id,))
    return conn.execute(
        "INSERT INTO requests (client_id, ip, referer, path) VALUES (%s, %s, %s, %s) "
        "RETURNING request_id",
        (client_id, request.remote_addr, request.referrer, request.path)).fetchone()[0]

def iso(ts):
    # e.g. 2026-09-16T18:04:11.512Z
    return ts.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

@app.get('/')
def index():
    #return "<p> Hello, world!</p>"
    client_id = get_client_id()
    is_new = client_id is None
    if is_new:
        client_id = uuid.uuid4()
    with db() as conn:
        log_request(conn, client_id)

    # conditional=False: always a full 200, never a 304 Not Modified on repeat visits.
    response = send_file(os.path.join(app.root_path, 'boogle.html'), mimetype='text/html', conditional=False)
    if is_new:
        response.set_cookie(COOKIE_NAME, str(client_id), max_age=COOKIE_MAX_AGE, path='/', samesite='Lax')
    return response

@app.get('/jsontest')
def jsontest():
    return jsonify({"data": "I am in CSE190/CSE291!"})

@app.get('/search')
def search():
    # Postgres text can't hold NUL bytes.
    query = request.args.get('q', '').replace('\x00', '').strip()
    client_id = get_client_id()
    with db() as conn:
        request_id = log_request(conn, client_id)
        if query:
            conn.execute(
                "INSERT INTO searches (client_id, request_id, query) VALUES (%s, %s, %s)",
                (client_id, request_id, query))
    return redirect('/', code=303)

@app.get('/api/history')
def history():
    client_id = get_client_id()
    with db() as conn:
        log_request(conn, client_id)
        # With no cookie client_id is NULL, which matches no rows.
        rows = conn.execute(
            "SELECT query, ts FROM searches WHERE client_id = %s ORDER BY ts DESC, search_id DESC",
            (client_id,)).fetchall()
    response = jsonify({
        "client_id": str(client_id) if client_id else None,
        "searches": [{"query": query, "ts": iso(ts)} for query, ts in rows],
    })
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/dump')
def dump():
    with db() as conn:
        # One snapshot for all three queries, so they agree with each other.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        clients = conn.execute(
            "SELECT client_id, first_seen, last_seen FROM clients ORDER BY first_seen, client_id").fetchall()
        # One row per (client, ip), most recently used first.
        ip_rows = conn.execute(
            "SELECT client_id, host(ip) FROM requests "
            "WHERE client_id IS NOT NULL AND ip IS NOT NULL "
            "GROUP BY client_id, ip ORDER BY max(ts) DESC, max(request_id) DESC").fetchall()
        search_rows = conn.execute(
            "SELECT client_id, query, ts FROM searches WHERE client_id IS NOT NULL "
            "ORDER BY ts DESC, search_id DESC").fetchall()

    ips = defaultdict(list)
    for client_id, ip in ip_rows:
        ips[client_id].append(ip)
    searches = defaultdict(list)
    for client_id, query, ts in search_rows:
        searches[client_id].append({"query": query, "ts": iso(ts)})

    return jsonify({"clients": [{
        "client_id": str(client_id),
        "first_seen": iso(first_seen),
        "last_seen": iso(last_seen),
        "latest_ip": ips[client_id][0] if ips[client_id] else None,
        "ips": ips[client_id],
        "searches": searches[client_id],
    } for client_id, first_seen, last_seen in clients]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port="8000")
