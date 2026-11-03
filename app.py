import os
import uuid
from collections import defaultdict
from datetime import timezone

import psycopg
from flask import Flask, jsonify, redirect, request, send_file

from inference import Model
from ssp import advertiser_domains, run_auction

app = Flask(__name__)
app.json.sort_keys = False

# Default: the local "boogle" database over the unix socket, logging in as the OS user.
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql:///boogle')

COOKIE_NAME = 'boogle_id'
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

PAGE = os.path.join(app.root_path, 'starter_3_4', 'part4', 'new_boogle.html')

# The Part 3 model, shared by every request.
model = Model()

# Search history read per auction; users in the Part 3 data have at most 1200 searches.
MAX_SEARCHES = 1200

# Stop selling to an advertiser once it has this many complaints...
MIN_COMPLAINTS = 2
# ...and at least this share of its impressions drew one. One unlucky complaint isn't enough,
# and neither are a few on an advertiser that has shown thousands of ads.
MAX_COMPLAINT_RATE = 0.05

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

def set_tracking_cookie(response, client_id):
    response.set_cookie(COOKIE_NAME, str(client_id), max_age=COOKIE_MAX_AGE, path='/', samesite='Lax')

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
    response = send_file(PAGE, mimetype='text/html', conditional=False)
    if is_new:
        set_tracking_cookie(response, client_id)
    return response

@app.get('/jsontest')
def jsontest():
    return jsonify({"data": "I am in CSE190/CSE291!"})

@app.get('/search')
def search():
    # Postgres text can't hold NUL bytes.
    query = request.args.get('q', '').replace('\x00', '')
    client_id = get_client_id()
    with db() as conn:
        request_id = log_request(conn, client_id)
        # Stored exactly as typed; whitespace-only counts as empty.
        if query.strip():
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

def blocked_advertisers(conn):
    rows = conn.execute(
        "SELECT domain FROM auctions, unnest(adomain) AS domain GROUP BY domain "
        "HAVING count(*) FILTER (WHERE complained) >= %s "
        "AND count(*) FILTER (WHERE complained) >= %s * count(*)",
        (MIN_COMPLAINTS, MAX_COMPLAINT_RATE)).fetchall()
    return {domain for domain, in rows}

@app.get('/ad')
def ad():
    # Flask routes HEAD here too, and a HEAD shouldn't run an auction and charge a DSP.
    if request.method == 'HEAD':
        return '', 200

    client_id = get_client_id()
    is_new = client_id is None
    if is_new:
        client_id = uuid.uuid4()
    with db() as conn:
        log_request(conn, client_id)
        searches = conn.execute(
            "SELECT query FROM searches WHERE client_id = %s ORDER BY ts DESC, search_id DESC LIMIT %s",
            (client_id, MAX_SEARCHES)).fetchall()
        blocked = blocked_advertisers(conn)

    # Newest first and one per line, which is how the Part 3 model saw each user's searches.
    segments = model.segments("\n".join(query for query, in searches))
    # No database connection is held here: this waits on the DSPs for up to 250ms.
    auction = run_auction(client_id, segments, blocked, request.headers.get('User-Agent', ''), request.remote_addr)

    winner = auction['winner'] or {}
    with db() as conn:
        conn.execute(
            "INSERT INTO auctions (auction_id, client_id, segments, seat, bid_id, crid, adomain, "
            "bid_price, price, billed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (auction['auction_id'], client_id, segments, winner.get('seat'), winner.get('id'),
             winner.get('crid'), advertiser_domains(winner) if winner else None, winner.get('price'),
             auction['price'], auction['billed']))

    # With no winner the iframe is simply empty. margin:0 fits the ad exactly into the 300x250 slot.
    html = f'<!doctype html><html><body style="margin:0">{winner["adm"]}</body></html>' if winner else ''
    response = app.response_class(html, mimetype='text/html')
    # Every load is a new auction, so never let a browser reuse this response.
    response.headers['Cache-Control'] = 'no-store'
    # This is someone else's HTML on our origin. The iframe is sandboxed, and this applies the
    # same sandbox if /ad is opened directly.
    response.headers['Content-Security-Policy'] = 'sandbox allow-popups allow-popups-to-escape-sandbox'
    if is_new:
        set_tracking_cookie(response, client_id)
    return response

@app.post('/ad/feedback')
def ad_feedback():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('auction_id'), str):
        return jsonify({"error": "expected {\"auction_id\": ..., \"event\": ...}"}), 400
    if body.get('event') != 'complaint':
        return '', 204
    with db() as conn:
        # complained is a flag, so a repeated complaint about one auction counts once.
        found = conn.execute(
            "UPDATE auctions SET complained = true WHERE auction_id = %s AND bid_id IS NOT NULL "
            "RETURNING auction_id",
            (body['auction_id'],)).fetchone()
    return ('', 204) if found else (jsonify({"error": "unknown auction"}), 404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port="8000")
