# PA1 write-up: what we built and why

Study notes covering all four parts of PA1. Each section explains the concepts first, then walks
through the code. Code walkthroughs quote the real files: [app.py](app.py),
[ssp.py](ssp.py), [inference.py](inference.py), [deploy.sh](deploy.sh),
[deploy-remote.sh](deploy-remote.sh),
[starter_3_4/part3/train_model.py](starter_3_4/part3/train_model.py). Code in sections 2–4 shows how
those parts were built; section 6 notes what Part 4 changed.

Contents

1. [The big picture](#1-the-big-picture)
2. [Part 1: a simple web server](#2-part-1-a-simple-web-server)
3. [Deployment: getting it on the internet](#3-deployment-getting-it-on-the-internet)
4. [Part 2: tracking users with a cookie and a database](#4-part-2-tracking-users-with-a-cookie-and-a-database)
5. [Part 3: inferring demographics from searches](#5-part-3-inferring-demographics-from-searches)
6. [Part 4: selling ads on Boogle](#6-part-4-selling-ads-on-boogle)
7. [Questions you should be able to answer](#7-questions-you-should-be-able-to-answer)

---

## 1. The big picture

```
 browser / autograder
        │  HTTP on port 80 (or 8000)
        ▼
 ┌──────────────── Google Cloud VM (35.252.229.92) ────────────────┐
 │  firewall: allow TCP 80, 8000                                    │
 │  systemd ── keeps running ──► gunicorn (2 worker processes)      │
 │                                   │ imports app:app              │
 │                                   ▼                              │
 │                               Flask app (app.py)                 │
 │                                   │ SQL over a unix socket       │
 │                                   ▼                              │
 │                               PostgreSQL ("boogle" database)     │
 └──────────────────────────────────────────────────────────────────┘

 your Mac ── git push ──► GitHub (private repo) ◄── git pull ── VM
          └─ ./deploy-remote.sh ── ssh ──► VM runs ./deploy.sh
```

- **Part 1:** a Flask server with two routes, deployed so the graders can reach it at a public IP.
- **Part 2:** the same server now tags every visitor with a cookie and records what they search for
  in Postgres. This is "stateful tracking": the state (the cookie) lives in the browser, and the
  server uses it to link all of a person's visits together.
- **Part 3:** offline machine learning. Given people's search histories, predict their gender,
  age, income and so on.
- **Part 4:** the server now runs a real-time ad auction on every page view, using the Part 3 model
  to describe the visitor to advertisers.

---

## 2. Part 1: a simple web server

### Concepts

**HTTP request/response.** A browser sends a *request*: a method (`GET`, `POST`, ...), a path
(`/jsontest`), headers (including cookies), and sometimes a body. The server answers with a
*response*: a status code, headers (such as `Content-Type`), and a body.

Status codes used in this PA:

| Code | Meaning | Where we use it |
|---|---|---|
| 200 OK | success, body attached | every normal page or JSON response |
| 303 See Other | "go to this other URL with a GET" | `/search` sends the browser back to `/` |
| 304 Not Modified | "your cached copy is still good" | we deliberately turned this **off** (see Part 2) |
| 405 Method Not Allowed | wrong method for this route | Flask sends it automatically for e.g. `POST /` |

**Content-Type** tells the client how to interpret the body: `text/html` for the page and
`application/json` for JSON. The grader checks these exactly.

**Flask, WSGI and gunicorn.** A Flask app is just a Python object: give it a request and it returns
a response. Something else has to listen on the network port, parse raw HTTP, and call the app.
The standard interface between the two is **WSGI**. In development, `app.run()` starts Flask's
built-in server. In production we use **gunicorn**, a real WSGI server: it runs several worker
processes and restarts any that crash or hang. Flask itself warns against using the dev server in
production.

### The Part 1 code

```python
@app.get('/')
def index():
    ...
    return send_file(os.path.join(app.root_path, 'boogle.html'), mimetype='text/html', ...)
```

- `@app.get('/')` registers `index()` for **GET** requests to `/`. Any other method automatically
  gets a 405, which satisfies "all other request types should return an error". (Flask also
  answers `HEAD` and `OPTIONS` automatically; HEAD is a GET without the body.)
- `send_file` streams the file's bytes unchanged, which matters because the grader checks the page
  byte for byte. (Part 4 swapped the file for `new_boogle.html`, as the handout instructs.) `app.root_path` is the folder containing `app.py`, so the path works no matter
  which directory you start the server from.
- We chose `send_file` over `app.send_static_file`: the latter only serves from a `static/` folder.
  `send_file` doesn't validate paths, which is fine here because the path is hard-coded and nothing
  in it comes from the user.

```python
@app.get('/jsontest')
def jsontest():
    return jsonify({"data": "I am in CSE190/CSE291!"})
```

- `jsonify` turns the dict into JSON and sets `Content-Type: application/json`. The status is 200
  by default.

---

## 3. Deployment: getting it on the internet

### Why a VM

We needed a fixed `ip:port` that stays up after the deadline, a database next to the app (Part 2),
and the visitor's real IP address. Serverless platforms give you a hostname rather than an IP, and
put a proxy in front of your app, so you'd see the proxy's IP instead of the visitor's. A small VM
does all three.

### What we set up in Google Cloud

| Piece | What it is | Why |
|---|---|---|
| **e2-micro VM, us-west1-b, Debian 12** | a tiny Linux machine (2 shared vCPUs, 1 GB RAM) | in the always-free tier |
| **Standard persistent disk, 10 GB** | the VM's hard drive | "balanced"/SSD disks aren't free |
| **Static external IP 35.252.229.92** | a reserved public address | ephemeral IPs can change on restart, which would break `server.txt` |
| **Firewall rules TCP 80, 8000** | allow inbound connections on those ports | Google blocks inbound traffic by default |
| Snapshots / Ops Agent off | backups and monitoring | avoid charges; save RAM |

### SSH and keys

- **SSH** gives you an encrypted terminal on the VM. The browser "SSH" button creates temporary
  keys automatically; that's why `~/.ssh/authorized_keys` on the VM had lines marked
  `# Added by Google` that expire after a few minutes.
- **Public-key authentication:** you keep a *private* key, and the server stores the matching
  *public* key in `authorized_keys`. Only the public key (`.pub`) is ever shared.
- For SSH from your Mac we created a dedicated key, `~/.ssh/id_ed25519_gcp`, rather than reusing
  an old internship key, plus this alias in `~/.ssh/config`:

  ```
  Host boogle-vm
    HostName 35.252.229.92
    User akhaing
    IdentityFile ~/.ssh/id_ed25519_gcp
    IdentitiesOnly yes
  ```

  `ssh boogle-vm` now fills in the IP, the user and the key. `IdentitiesOnly yes` means "only try
  this key", so SSH doesn't offer every key on your machine.
- Lessons from debugging: a key pasted by hand into `authorized_keys` stopped working weeks later,
  so the more durable way is **Compute Engine → Metadata → SSH keys**. That console form
  wants the bare key; the username comes from the comment at the end.
- **Deploy key:** the VM has its own key (`~/.ssh/github_deploy`) registered on GitHub as a
  read-only deploy key for this one repo. If the VM were compromised, it couldn't touch your other
  repositories or push code.

### gunicorn under systemd

The service file lives **on the VM** at `/etc/systemd/system/boogle.service`. It isn't in the
repo; it's system configuration you created with `sudo`.

```ini
[Unit]
Description=Boogle Flask app
After=network.target                  # start once networking is up

[Service]
User=akhaing                          # run as your user, not root
WorkingDirectory=/home/akhaing/boogle # where app.py lives (the cloned repo)
ExecStart=/home/akhaing/boogle/.venv/bin/gunicorn -w 2 -b 0.0.0.0:80 -b 0.0.0.0:8000 app:app
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always                        # restart if it crashes

[Install]
WantedBy=multi-user.target            # start at boot
```

- `-w 2`: two worker processes, which suits a 1 GB machine.
- `-b 0.0.0.0:80 -b 0.0.0.0:8000`: listen on both ports on every network interface.
  `app:app` means "the object `app` inside `app.py`".
- **Why port 80 too:** the Part 1 grader's input box took just an IP, and `http://IP/` means
  port 80. Our first grader run timed out because we only listened on 8000. We fixed it by listening
  on both ports, not by redirecting.
- `AmbientCapabilities=CAP_NET_BIND_SERVICE`: ports below 1024 normally require root. This grants
  only the "bind low ports" permission, so gunicorn still runs as an ordinary user.
- `systemctl enable --now boogle` starts the service and makes it start at every boot.
  `journalctl -u boogle` shows its logs.
- The bug we hit: `No module named 'app'`. `WorkingDirectory` pointed at a folder without `app.py`
  (back when the code lived in `starter/`). We flattened the repo so `app.py` sits at the root.

### Deploying with two scripts

Code moves from your Mac to GitHub to the VM. The VM never receives files directly, so the
deployed version is always a specific commit.

**[deploy-remote.sh](deploy-remote.sh)** (runs on your Mac):

```bash
if [[ -n "$(git status --porcelain)" ]]; then ...exit 1   # refuse if anything is uncommitted
git fetch -q origin
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse @{u})" ]]; then ...exit 1  # refuse if not pushed
ssh "$HOST" 'cd ~/boogle && git pull --ff-only -q && ./deploy.sh'
curl -fsS "$PUBLIC_URL/jsontest"                          # final check from the public internet
```

- The two safety checks exist because the VM deploys **what's on GitHub**. Deploying with
  uncommitted local changes would mean testing one thing and shipping another.
- `--ff-only` makes `git pull` refuse to create a merge on the server.

**[deploy.sh](deploy.sh)** (runs on the VM, after the pull):

```bash
set -euo pipefail                      # stop at the first failing command
.venv/bin/pip install -q -r requirements.txt
PGOPTIONS='-c client_min_messages=warning' psql -q -v ON_ERROR_STOP=1 -d boogle -f schema_handout.sql
.venv/bin/python -c 'import app'       # added in Part 4: does the code and model load?
sudo systemctl restart boogle
...                                    # if the service isn't active, print its logs and fail
curl -fsS --retry 15 --retry-connrefused --retry-delay 1 http://localhost/jsontest
curl -fsS http://localhost/api/history # goes through the database
```

- `set -euo pipefail` aborts on any error. Because the schema step comes **before** the restart, a
  missing database stops the deploy while the old code keeps serving.
- The schema file uses `CREATE TABLE IF NOT EXISTS`, so running it on every deploy is harmless.
  This is a simple form of migration.
- `sudo systemctl restart boogle` works without a password because of a narrow rule in
  `/etc/sudoers.d/boogle-deploy`:
  `akhaing ALL=(root) NOPASSWD: /usr/bin/systemctl restart boogle`. That allows exactly one
  command, rather than giving your SSH key full root.

### Dependencies

`requirements.txt` is a `pip freeze` of the project venv: exact versions of Flask, gunicorn,
psycopg and so on. You never copy a venv between machines (it contains compiled,
platform-specific files); each machine rebuilds its own from `requirements.txt`.

---

## 4. Part 2: tracking users with a cookie and a database

### Concepts

**Cookies.** The server sends `Set-Cookie: name=value; attributes`. From then on, the browser sends
`Cookie: name=value` with every request to that site. This is a *first-party* cookie, because it
belongs to the site you're actually visiting. Our cookie:

```
Set-Cookie: boogle_id=cb46f9e3-...; Expires=...; Max-Age=31536000; Path=/; SameSite=Lax
```

| Attribute | Meaning | Why |
|---|---|---|
| `boogle_id=<uuid4>` | a random identifier | uuid4 is 122 random bits, so collisions are practically impossible and nothing is guessable |
| `Max-Age=31536000` | keep for one year | makes it *persistent*; without it, it's a session cookie deleted when the browser closes |
| `Path=/` | send it for every path on the site | `/search` and `/api/history` need it too |
| `SameSite=Lax` | don't send on cross-site sub-requests, but do send on top-level navigation | the spec requires Lax |
| no `Secure` | `Secure` cookies are only sent over HTTPS | our server is plain HTTP, so `Secure` would break it |

The key tracking rule: **only set the cookie if it isn't already there.** Re-issuing a new ID on
every visit would make each visit look like a new person.

**Relational database (Postgres).** The handout schema, used unchanged:

```sql
clients  (client_id UUID PRIMARY KEY, first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ)
requests (request_id BIGSERIAL PRIMARY KEY, client_id UUID REFERENCES clients,
          ts TIMESTAMPTZ, ip INET, referer TEXT, path TEXT)
searches (search_id BIGSERIAL PRIMARY KEY, client_id UUID REFERENCES clients,
          request_id BIGINT REFERENCES requests, query TEXT NOT NULL, ts TIMESTAMPTZ)
```

- **PRIMARY KEY**: unique row identifier. **BIGSERIAL**: auto-incrementing 64-bit integer.
- **REFERENCES** (foreign key): `searches.client_id` must name an existing client. The column can
  be NULL, which we use for searches made without a cookie.
- **TIMESTAMPTZ**: a timestamp that's an absolute instant (stored as UTC). **INET**: an IP
  address type.
- `latest_ip` and `ips` aren't stored as columns. They're computed from `requests`, which logs
  every visit.
- The two indexes on `(client_id, ts DESC, ...)` make "this client's rows, newest first" a fast
  lookup instead of a full table scan.

**Transactions.** Everything inside `with db() as conn:` is one transaction: either all of it is
committed or none of it is. Postgres's `now()` returns the **transaction start time**, so every
row a single request writes carries the same timestamp. That's why `last_seen` can equal a search's
`ts` exactly, matching the handout's example.

**Parameterized queries.** We never paste user input into SQL strings. We write `%s` and pass the
values separately, and the database driver sends them as data, never as SQL. That's what made the
grader's `'); DROP TABLE searches;--` query harmless. It was stored as text like any other search.

**XSS (cross-site scripting).** `boogle.html` displays history with `item.textContent = query`,
never `innerHTML`, so `<script>alert('xss')</script>` shows up as literal text. The grader's
"unusual queries render as literal text" check tests this.

### [app.py](app.py), line by line

**Setup**

```python
import psycopg
from flask import Flask, jsonify, redirect, request, send_file
app = Flask(__name__)
app.json.sort_keys = False
```
- `psycopg` (version 3) is the Postgres driver.
- `sort_keys = False` keeps JSON keys in the order we write them (`client_id`, `first_seen`, ...),
  matching the handout examples. Flask sorts them alphabetically by default.

```python
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql:///boogle')
```
- `postgresql:///boogle` means: no host (so use the local unix socket), database `boogle`, and log
  in as the current OS user. On both your Mac and the VM, Postgres trusts the OS user over the
  socket ("peer" authentication), so there's **no password anywhere**. The environment variable
  lets tests point at a different database.

```python
COOKIE_NAME = 'boogle_id'
COOKIE_MAX_AGE = 365 * 24 * 60 * 60
```

```python
def db():
    return psycopg.connect(DATABASE_URL)
```
- One connection per request. Used as `with db() as conn:`, the connection commits when the block
  finishes normally, rolls back if an exception escapes, and closes either way. Connecting to a
  local socket costs a few milliseconds, so a connection pool isn't worth the complexity here.

```python
def get_client_id():
    value = request.cookies.get(COOKIE_NAME, '')
    try:
        client_id = uuid.UUID(value)
    except ValueError:
        return None
    return client_id if str(client_id) == value else None
```
- Returns the visitor's ID, or `None` if there's no cookie or it isn't a valid canonical UUID.
- The final comparison rejects odd spellings that `uuid.UUID` accepts (uppercase, braces, no
  dashes), so only IDs in exactly the format we issue are trusted. Garbage can't reach the UUID
  column and cause an error.

```python
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
```
- This is the heart of the tracking. The first statement is an **upsert**: insert the client if
  new (so `first_seen` and `last_seen` default to `now()`), otherwise just bump `last_seen`.
  `ON CONFLICT` makes this safe even if two requests with the same new cookie arrive at once.
- The second statement logs the visit: who, from which IP, the referring page, and which path.
  `RETURNING request_id` hands back the new row's ID so a search can link to its request.
- `request.remote_addr` is the real visitor IP, because nothing sits between the internet and
  gunicorn. Behind a proxy or load balancer it would be the proxy's IP.

```python
def iso(ts):
    return ts.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
```
- Converts to UTC, then formats like `2026-09-16T18:04:11.512Z`: ISO 8601 with milliseconds, where
  `Z` means UTC. This matches the handout examples.

**`GET /`**

```python
@app.get('/')
def index():
    client_id = get_client_id()
    is_new = client_id is None
    if is_new:
        client_id = uuid.uuid4()
    with db() as conn:
        log_request(conn, client_id)

    response = send_file(os.path.join(app.root_path, 'boogle.html'), mimetype='text/html', conditional=False)
    if is_new:
        response.set_cookie(COOKIE_NAME, str(client_id), max_age=COOKIE_MAX_AGE, path='/', samesite='Lax')
    return response
```
- No valid cookie means a new visitor: mint a uuid4, record them, and attach `Set-Cookie`.
  A returning visitor gets no `Set-Cookie` at all, so the ID is never overwritten.
- If someone arrives with a valid UUID we've never seen, the upsert simply adopts it as a client.
- `conditional=False`: by default `send_file` honours browser cache validation and can answer
  `304 Not Modified`. A grader that loads `/` twice in the same browser might then see 304 instead
  of 200, so we always send the full page.

**`GET /search`**

```python
@app.get('/search')
def search():
    query = request.args.get('q', '').replace('\x00', '')
    client_id = get_client_id()
    with db() as conn:
        request_id = log_request(conn, client_id)
        if query.strip():
            conn.execute(
                "INSERT INTO searches (client_id, request_id, query) VALUES (%s, %s, %s)",
                (client_id, request_id, query))
    return redirect('/', code=303)
```
- `request.args.get('q', '')` reads `?q=...` from the URL (the form in `boogle.html` submits there
  with GET).
- NUL bytes are removed because Postgres text columns can't store them; the insert would error.
- `query.strip()` is used **only to decide** whether the search is empty; the query is stored
  exactly as typed. Our first version stored the stripped text, and the grader failed us on
  `"   padded with spaces   "`, which cost 2 checks (94/100) until we fixed it.
- `redirect('/', code=303)` sends `303 See Other` with `Location: /`, so the browser reloads the
  home page, which then fetches the updated history. This is the Post/Redirect/Get pattern; it
  also means refreshing the page doesn't resubmit the search.
- With no cookie, `client_id` is `None` and the search is stored unattributed (NULL client). Only
  `/` ever sets the cookie.

**`GET /api/history`**

```python
@app.get('/api/history')
def history():
    client_id = get_client_id()
    with db() as conn:
        log_request(conn, client_id)
        rows = conn.execute(
            "SELECT query, ts FROM searches WHERE client_id = %s ORDER BY ts DESC, search_id DESC",
            (client_id,)).fetchall()
    response = jsonify({
        "client_id": str(client_id) if client_id else None,
        "searches": [{"query": query, "ts": iso(ts)} for query, ts in rows],
    })
    response.headers['Cache-Control'] = 'no-store'
    return response
```
- `WHERE client_id = %s` restricts results to the caller's own searches. That's what makes each
  person see only their own history.
- With no cookie, `client_id` is NULL, and in SQL `x = NULL` is never true, so the result is
  correctly `[]`.
- `ORDER BY ts DESC, search_id DESC` gives newest first; the ID breaks ties when two timestamps are
  equal.
- `Cache-Control: no-store` stops browsers caching per-person data.

**`GET /dump`**

```python
conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
clients = ... "SELECT client_id, first_seen, last_seen FROM clients ORDER BY first_seen, client_id"
ip_rows = ... "SELECT client_id, host(ip) FROM requests WHERE ... GROUP BY client_id, ip
               ORDER BY max(ts) DESC, max(request_id) DESC"
search_rows = ... "SELECT client_id, query, ts FROM searches WHERE client_id IS NOT NULL
                   ORDER BY ts DESC, search_id DESC"
```
- Three queries in total, no matter how many clients there are. Querying once per client (the
  "N+1 queries" problem) would get slow as bots and graders add thousands of clients.
- `REPEATABLE READ` makes all three queries see the same snapshot of the database, so a request
  arriving mid-dump can't make them disagree.
- `GROUP BY client_id, ip` gives one row per distinct IP per client, ordered by when it was last
  used. So `ips[0]` is always `latest_ip`. `host(ip)` turns the INET into a plain string.
- Python then groups the rows by client with `defaultdict(list)` and builds the JSON.
- `/dump` and `/jsontest` deliberately don't call `log_request`, so reading the dump never changes
  it.

### Design choices the spec left open

| Situation | What we do |
|---|---|
| `/api/history` with no cookie | `{"client_id": null, "searches": []}` |
| `/search` with no cookie | store the query with a NULL client; redirect |
| malformed cookie on `/` | treat as a new visitor and issue a fresh ID |
| valid but unknown UUID cookie | adopt it as a client (don't overwrite) |
| whitespace around a query | stored exactly as typed (the grader requires this) |

---

## 5. Part 3: inferring demographics from searches

**Files to submit:** [starter_3_4/part3/predictions.csv](starter_3_4/part3/predictions.csv) and
[starter_3_4/part3/train_model.py](starter_3_4/part3/train_model.py). The script reads the
starter data files next to it (`train.jsonl.gz`, `train_labels.csv`, `dev.jsonl.gz`,
`dev_labels.csv`, `test.jsonl.gz`); those are the course's files, not ours.
`model.joblib` is also produced, and Part 4's model comes from it. It isn't part of the submission.

### The task

Each user is a JSON record with their searches (median 43 per user). For each user we predict
nine *categorical* attributes, each with 2–13 possible labels. That's nine separate
classification problems sharing the same input.

| Split | Users | Labels? | Used for |
|---|---|---|---|
| train | 10,000 | yes | fitting the model |
| dev | 2,000 | yes | measuring how well it generalizes; choosing settings |
| test | 3,000 | **no** | the predictions we submit |

The dev set matters because a model scored on the data it was trained on looks far better than it
really is (it can memorize). Dev users are unseen during training, so dev scores estimate test
scores.

### Concepts

**Text to numbers: TF-IDF.** A model needs numeric vectors. We concatenate all of a user's queries
into one document and count words and word pairs. Two adjustments make counts more useful:

- **TF (term frequency)**, made *sublinear*: `tf = 1 + ln(count)`. Searching "mortgage" 20 times
  is more telling than once, but not 20× more.
- **IDF (inverse document frequency)**: `idf = ln((1 + N) / (1 + df)) + 1`, where `N` is the
  number of users and `df` is how many users used the term. Words everyone uses ("weather",
  "news") get low weight; distinctive words ("retirement", "daycare") get high weight.
- Each user's vector is `tf × idf` per term, then scaled to length 1 (L2 normalization), so heavy
  searchers and light searchers are comparable.

Result: a 10,000 × ~121,000 matrix, mostly zeros (stored as a sparse matrix).

**Bigrams** (`ngram_range=(1, 2)`) add two-word phrases as features: "high school" means something
different from "high" and "school" separately. **`min_df=2`** drops terms used by only one user;
they can't generalize and mostly add noise.

**Logistic regression.** For each class `k` the model learns a weight vector `w_k` and a bias `b_k`,
computes a score `w_k · x + b_k` for every class, and turns the scores into probabilities with the
*softmax* function. It predicts the most probable class. Training finds weights that maximize the
probability of the correct labels, using an optimizer called lbfgs. With text, this amounts to
learning how strongly each word or phrase pushes toward each label; for example, "medicare" pushes
toward 65+.

**Regularization (`C`).** With 121k features and 10k users, the model could memorize the training
set. Logistic regression adds a penalty on large weights; `C` is the inverse of its strength. Small
`C` gives a simpler model that may underfit; large `C` fits the training data closely and may
overfit. We tried C = 1, 4 and 16.

**Macro-F1, the grading metric.** For each class: precision `P = TP / (TP + FP)` ("of the users I
called *retired*, how many were?"), recall `R = TP / (TP + FN)` ("of the retired users, how many did
I find?"), and `F1 = 2PR / (P + R)`. **Macro**-F1 is the plain average over classes. Every class
counts equally, so a model that never predicts a rare class like `unknown` is heavily penalized,
even if its overall accuracy looks fine.

**`class_weight="balanced"`.** Because macro-F1 treats rare classes as equally important, we make
training treat them that way too. Each class gets weight `n_samples / (n_classes × class_count)`.
For gender: `unknown` (1,004 users) gets 10000 / (3 × 1004) ≈ 3.3, while `male` (4,548) gets ≈ 0.73.
Mistakes on rare classes then cost more during training. This was the single biggest improvement.

**`unknown` is a real class.** The spec says to predict it like any other label, and balancing
helps the model actually predict it.

### What we tried (dev macro-F1)

| Setting | gender | age | edu | income | industry | employ. | emp. size | home | parent | mean |
|---|---|---|---|---|---|---|---|---|---|---|
| C=1, unweighted | .641 | .707 | .640 | .253 | .677 | .793 | .702 | .725 | .903 | .671 |
| C=16, unweighted | .679 | .703 | .642 | .302 | .680 | .825 | .704 | .782 | .915 | .692 |
| **C=1, balanced (chosen)** | **.721** | **.706** | **.655** | **.320** | **.680** | **.832** | **.699** | **.809** | **.915** | **.704** |
| C=16, balanced | .702 | .708 | .640 | .326 | .673 | .834 | .699 | .811 | .918 | .701 |
| target (full points) | .497 | .498 | .450 | .241 | .470 | .578 | .478 | .562 | .644 | |
| extra credit | .639 | .641 | .579 | .310 | .605 | .743 | .615 | .723 | .828 | |

The chosen setting beats the extra-credit bar on all nine attributes on dev. The closest call is
household income (0.320 against 0.310). Test scores will differ somewhat from dev scores.

### [train_model.py](starter_3_4/part3/train_model.py), line by line

```python
def load_users(split):
    with gzip.open(HERE / f"{split}.jsonl.gz", "rt") as f:
        return [json.loads(line) for line in f]
```
- `.jsonl.gz` is gzip-compressed "JSON Lines": one JSON object (one user) per line. `"rt"` opens
  it as decompressed text. `HERE` is the script's folder, so it runs from anywhere.

```python
def load_labels(split, users):
    with open(HERE / f"{split}_labels.csv", newline="") as f:
        by_id = {row["client_id"]: row for row in csv.DictReader(f)}
    return {a: [by_id[u["client_id"]][a] for u in users] for a in ATTRIBUTES}
```
- Labels are joined to users **by `client_id`**, not by row position, so the order of the two files
  can't silently misalign them. The result is `{attribute: [label for each user, in user order]}`.

```python
def user_text(user):
    return "\n".join(s["query"] for s in user["searches"])
```
- One document per user. One quirk worth knowing: the tokenizer ignores the newlines, so a bigram
  can span the last word of one query and the first word of the next. Those pairs are mostly noise,
  and `min_df` and the weights largely neutralize them.

```python
def fit(users, labels):
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    X = vectorizer.fit_transform(user_text(u) for u in users)
```
- `fit_transform` learns the vocabulary and IDF weights **from the training users only**, then
  converts them to the matrix `X`. Lowercasing and splitting into words happen by default.

```python
    classifiers = Parallel(n_jobs=-1)(
        delayed(LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit)(X, labels[a])
        for a in ATTRIBUTES)
    return {"vectorizer": vectorizer, "classifiers": dict(zip(ATTRIBUTES, classifiers))}
```
- One classifier per attribute, all sharing the same `X`. The nine are independent, so joblib's
  `Parallel` trains them simultaneously on your CPU cores. `delayed(f)(args)` packages a call to run
  later in a worker; `n_jobs=-1` means use all cores.
- `max_iter=2000` gives the optimizer enough steps to converge.
- The "model" is the vectorizer plus the nine classifiers. New users **must** go through the same
  fitted vectorizer, so the columns mean the same words.

```python
def predict(model, users):
    X = model["vectorizer"].transform(user_text(u) for u in users)
    return {a: model["classifiers"][a].predict(X) for a in ATTRIBUTES}
```
- `transform`, not `fit_transform`: reuse the training vocabulary and IDF. Words never seen in
  training are ignored.

```python
    dev_preds = predict(fit(train, train_labels), dev)
    for a in ATTRIBUTES:
        print(f"  {a:18} {f1_score(dev_labels[a], dev_preds[a], average='macro'):.3f}")
```
- Step 1, evaluation: train on train, score on dev. `average='macro'` is the grading metric.

```python
    model = fit(train + dev, {a: train_labels[a] + dev_labels[a] for a in ATTRIBUTES})
```
- Step 2, the final model: once the settings were chosen using dev, we retrain on **train + dev**
  (12,000 users). More data usually helps, and dev has already done its job. The trade-off is that
  we can no longer measure this exact model, which is standard practice.

```python
    with open(HERE / "predictions.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["client_id", *ATTRIBUTES])
        for i, user in enumerate(test):
            writer.writerow([user["client_id"], *(test_preds[a][i] for a in ATTRIBUTES)])
    joblib.dump(model, HERE / "model.joblib")
```
- Writes the exact header the spec requires, then one row per test user in file order.
- `joblib.dump` saves the fitted model so it doesn't need retraining. Part 4 must use "exactly your
  model from part 3", and section 6 explains how it reaches the server.

I checked the output: 3,000 rows matching the test users in order, the exact header, and every
value a valid label. Every class of every attribute gets predicted at least sometimes.

---

## 6. Part 4: selling ads on Boogle

Boogle now runs a real-time auction every time the ad iframe loads. Our server is a **Supply-Side
Platform (SSP)**: it sells the ad slot. New files: [ssp.py](ssp.py) (the auction),
[inference.py](inference.py) (the model inside the server), [export_model.py](export_model.py),
[gunicorn.conf.py](gunicorn.conf.py). New routes in [app.py](app.py): `GET /ad` and
`POST /ad/feedback`. New table: `auctions`, in [schema_handout.sql](schema_handout.sql).

**To submit:** `server.txt` with the single line `35.252.229.92:80`.

### Concepts

**SSP, DSP and real-time bidding.** A *Demand-Side Platform* (DSP) buys ad slots for advertisers.
For each page view, the SSP describes the slot and the visitor to several DSPs, they answer within
a time limit, the SSP picks a winner, and the winner's ad is shown. All of this happens while the
page loads. **OpenRTB** is the standard JSON format for those messages.

```
 browser ─ GET /ad ─► our SSP ─ read searches, predict segments, read blocklist (Postgres)
                        │  POST bid request to all 4 DSPs at once
                        ├──► steady  bids regularly, about $1.30–2.30
                        ├──► niche   bids only on certain audiences, such as parents, about $4–6
                        ├──► shady   high bids up to about $6, with bad ads
                        └──► glitch  usually no bid; sometimes broken or slow
                        │  after 400ms: drop late or invalid bids, pick the winner, set the price
                        └──► GET burl (the bill) with the final price
 browser ◄─ winning ad HTML ─┘
```

**Key bid request fields**

| Field | Meaning |
|---|---|
| `id` | the auction ID; the response must echo it, and complaints refer to it |
| `imp[0]` | the ad slot ("impression"): `id` "1", 300×250, and our `bidfloor` |
| `device` | the visitor's user agent and IP |
| `user.id` | the `boogle_id` cookie |
| `user.data[0].segment` | what we claim about the user, as segment IDs; `ext.segtax: 500` names the taxonomy |
| `tmax` | how many milliseconds DSPs have to answer (we send 250; our own cutoff is 400, see below) |
| `badv` | advertiser domains we refuse; DSPs honor this list |

**CPM** means cost per mille, the price per 1,000 impressions. A price of 2.00 earns Boogle $0.002
for one ad.

**Second-price auction.** The highest bidder wins but pays just above the runner-up:

```
price = min(winner's bid, max(second-highest bid, floor) + 0.01)
```

With bids of 5.00 and 3.00 the price is 3.01. With a lone 2.00 bid and our 0.50 floor it is 0.51. The
`min` stops anyone paying more than they bid: in a tie at 2.00, the price is 2.00. The point is that
bidders can bid what the slot is truly worth, without gaming their bid.

**`burl`, the billing URL.** The winner's bid includes a URL containing the literal text
`${AUCTION_PRICE}`. We replace that with the final price and make a GET request. Only that request
earns money, so it must happen exactly once and only for the winner.

**Segments and confidence.** Part 3 had to guess for every user. Part 4 must **leave a segment out
when unsure**, because a false claim lies to advertisers. We claim a segment only when the model's
probability is at least **0.7**. Because `model.joblib` was trained on train + dev, I measured this
with a separate model fit on train only, so dev was truly unseen, and on shortened histories too,
since new users have few searches:

| Searches per user | 3 | 5 | 10 | 20 | all |
|---|---|---|---|---|---|
| Claims correct at ≥ 0.7 | 87% | 90% | 94% | 96% | 98% |
| Claims per user | 1.5 | 1.8 | 2.2 | 2.6 | 3.5 |
| "parent" claims correct | 93% | 96% | 97% | 98% | 99% |
| Real parents found | 37% | 46% | 56% | 64% | 74% |

So a new user gets few segments, and more appear as they search. `unknown` has no segment ID, so
it's never claimed. The `niche` DSP bid in both probes that included 901 (parent) and in neither
without it, which is how the model drives revenue: a good claim lets a $5 niche bid in. Its buyers
in the grader run were kidsco and minivanly, plus medigapplus and cruiseco, which suggests older
audiences too. The grader reported about 100% of niche's ads reached someone in its audience.

**Sandboxed iframe.** The ad is someone else's HTML. `new_boogle.html` loads it in
`<iframe sandbox="allow-popups allow-popups-to-escape-sandbox">`. Without `allow-scripts` the ad can't
run code, and without `allow-same-origin` it can't read our page or cookies. Our `/ad` response repeats
this with a `Content-Security-Policy: sandbox ...` header, so opening `/ad` directly is just as safe.

**Threads vs processes.** Gunicorn has 2 worker processes with 8 threads each. An auction mostly
*waits* on the network, and Python threads work well for waiting, so one worker handles several
auctions at once. Each process has its own memory, so shared state like the blocklist must live in
Postgres.

### Why a separate model file

The VM can't load `model.joblib`. A pickle needs matching library versions, and the VM's Python 3.11
can't install the Mac's numpy 2.5 and scikit-learn 1.9. Installing scikit-learn would also use
memory on a 1 GB machine for code we barely need, because *prediction* is simple arithmetic.

[export_model.py](export_model.py) saves the same fitted numbers (vocabulary, IDF, weights) to
`model.npz`, a plain numpy file, and [inference.py](inference.py) redoes the arithmetic with numpy.
The script then checks both agree: all 5,000 dev and test users got identical predictions, with
probabilities within 0.00000002 (the tiny gap is from storing weights as 32-bit floats, which
also shrinks the file from 54 MB to 25 MB). It is still "exactly the model from Part 3".

`model.npz` is too big for git, so [deploy-remote.sh](deploy-remote.sh) copies it to the VM with `scp`
when its hash differs.

### [inference.py](inference.py), line by line

```python
TOKEN = re.compile(r"(?u)\b\w\w+\b")
```
- TfidfVectorizer's default definition of a word: two or more word characters.

```python
def tfidf(self, text):
    tokens = TOKEN.findall(text.lower())
    counts = Counter(tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])])
    known = [(self.vocabulary[t], count) for t, count in counts.items() if t in self.vocabulary]
    if not known:
        return [], np.zeros(0)
    indices = [i for i, _ in known]
    values = (1 + np.log([count for _, count in known])) * self.idf[indices]
    return indices, values / np.linalg.norm(values)
```
- This is TF-IDF from section 5 by hand. `zip(tokens, tokens[1:])` pairs each word with the next
  to make bigrams. Words outside the training vocabulary are dropped.
- `1 + log(count)` is sublinear TF, multiplied by IDF, then divided by the length (L2 norm).
- A user has only a few hundred features out of 137,946, so it returns just the non-zero ones.

```python
def probabilities(self, text):
    indices, values = self.tfidf(text)
    ...
    scores = self.coef[attribute][:, indices] @ values + self.intercept[attribute]
    if len(scores) == 1:
        p = 1 / (1 + math.exp(-scores[0]))
        result[attribute] = np.array([1 - p, p])
    else:
        exp = np.exp(scores - scores.max())
        result[attribute] = exp / exp.sum()
```
- `@` is the dot product: `w · x + b`, one score per class, using only this user's columns.
- Two classes (parental status) have one score, turned into a probability by the sigmoid.
- More classes use softmax. Subtracting the max first avoids overflow without changing the result.

```python
def segments(self, text):
    ids = []
    for attribute, probs in self.probabilities(text).items():
        best = int(probs.argmax())
        segment = self.segment_ids.get((attribute, self.classes[attribute][best]))
        if segment and probs[best] >= CONFIDENCE:
            ids.append(segment)
    return ids
```
- One best guess per attribute, so at most one segment per attribute, as `segments.json` requires.
  `.get` returns `None` for `unknown`, which has no segment.

### [ssp.py](ssp.py), line by line

```python
session = requests.Session()
for scheme in ('https://', 'http://'):
    session.mount(scheme, HTTPAdapter(pool_maxsize=32))
executor = ThreadPoolExecutor(max_workers=32)
```
- A `Session` keeps connections open for reuse. `executor` is a pool of threads for asking DSPs
  in parallel.

**The cold-start bug.** On the first live test, the first two auctions showed no ad. Measured from the
VM, a new TLS connection took **300–535 ms** per DSP, longer than `tmax`, while a reused one took
30–75 ms. There were two fixes:

```python
def warm_connections():
    global last_warmed
    if time.monotonic() - last_warmed >= WARM_EVERY:
        last_warmed = time.monotonic()
        for _ in range(SPARE_CONNECTIONS):
            executor.submit(ping)
```
- The first fix opens spare connections ahead of time, when a worker starts (a hook in `gunicorn.conf.py`) and at
  most once a minute on incoming requests (`@app.before_request`). All 8 pings go at once, so
  each needs its own connection. Being traffic-driven, it sends nothing while nobody uses Boogle.
- `time.monotonic()` is a clock that never jumps backward, which suits measuring intervals.

```python
def ask_dsp(dsp, bid_request):
    try:
        return session.post(f'{RTB_URL}/{dsp}/bid', json=bid_request, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return None
```
- The second fix: this timeout is 2 s, **not** the auction deadline. If `requests` gave up at the
  deadline, it would close the connection, and the next auction would need a new one.
  `collect_bids` enforces the deadline instead, and a late response finishes harmlessly in the
  background, leaving its connection reusable.

```python
def collect_bids(bid_request, blocked):
    futures = [executor.submit(ask_dsp, dsp, bid_request) for dsp in DSPS]
    done, _ = wait(futures, timeout=DEADLINE_MS / 1000)
    bids = []
    for future in futures:
        if future in done:
            bids += parse_bids(future.result(), bid_request['id'], blocked)
    return bids
```
- `submit` starts all four requests at once. If they ran one after another, a slow DSP would use up
  everyone's time.
- `wait(timeout=0.4)` returns when all are finished or at 400 ms, whichever is first. Anything not
  in `done` is late and never read. This is filter 1, "arrive after tmax".

**The second-price bug: why the cutoff is 400 ms and not 250.** The first grader run scored 90/100,
with 58 of 886 bills (6.5%) wrong, such as charging 0.51 on a 3.2585 bid. We had seen only one valid
bid in an auction that really had two. My first guess, running out of connections, was wrong: a
load test at the grader's rate never opened a new one. The timings showed three clear groups:

| Bids | Arrive at |
|---|---|
| Normal | 30–75 ms |
| On time but stalled | ~270–385 ms |
| Deliberately late | 528 ms or later |

The middle group is a normal response plus about 230 ms, on about 6% of requests. It didn't change
with the `tmax` we sent (100, 250 or 600), it happened on a non-bidding URL too, it happened from my
Mac as well as the VM, and the VM's TCP counters showed no retransmissions. So the DSP answered on
time, and the delay came from the exchange's end; the exact cause isn't known. The grader counts
those bids as valid, so dropping them mispriced the auction.

So `TMAX_MS` (what we *tell* DSPs) stayed 250, and `DEADLINE_MS` (when we *stop listening*) became 400,
which sits in the gap between the second and third groups. **This is a deliberate reading of
"arrive after tmax"**: a bid arriving at 300 ms on our clock is accepted, because the evidence says
it was sent on time. Bids the DSP truly held back, 528 ms or later, are still dropped. The cost is that
a slow auction now takes up to 400 ms rather than 250.

```python
def parse_bids(response, auction_id, blocked):
    if response is None or response.status_code != 200:
        return []
    try:
        body = json.loads(response.content)
    except ValueError:
        return []
    if not isinstance(body, dict) or body.get('id') != auction_id:
        return []
    if body.get('cur', 'USD') != 'USD' or not isinstance(body.get('seatbid'), list):
        return []
```
- 204 ("no bid") and errors yield nothing. A failed parse is filter 2, "not valid JSON". The type
  checks matter because valid JSON can still be the wrong shape, like `[1,2]` or `null`.
- A mismatched `id` means this answers some other auction (OpenRTB requires the echo).
- `cur` is filter 3. OpenRTB says a missing `cur` means USD, so that's accepted.
- Then it loops over every bid of every seat, because one response can contain several bids.

```python
def is_valid_bid(bid, blocked):
    ...
    return (
        isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price)
        and price >= BID_FLOOR                         # filter 6
        and bid.get('impid') == IMP_ID                 # filter 4
        and bid.get('w') == SLOT_W and bid.get('h') == SLOT_H   # filter 5
        and isinstance(adm, str) and adm.strip() != ''
        and isinstance(burl, str) and burl.startswith(RTB_URL + '/') and PRICE_MACRO in burl
        and not blocked.intersection(advertiser_domains(bid)))
```
- Python's `json` accepts `NaN` and `Infinity`, and `True` counts as an `int`, so the price check is
  stricter than it looks. An `Infinity` bid would otherwise always win.
- `adm` must exist, or there's nothing to show.
- `burl` must point at the exchange because *our server fetches that URL*. Without the check, a
  malicious bid could make it request internal addresses (a server-side request forgery, SSRF).
- The last line drops blocked advertisers even if a DSP ignored `badv`.

```python
def clearing_price(prices):
    second = prices[1] if len(prices) > 1 else 0
    return min(prices[0], round(max(second, BID_FLOOR) + 0.01, 4))
```
- The formula, given valid bids sorted high to low. `round` removes float noise like
  3.0100000000000002. Bids have 4 decimals. Invalid bids never reach this, so a bad bid can't
  raise anyone's price.

```python
def bill(bid, price):
    try:
        return session.get(bid['burl'].replace(PRICE_MACRO, str(price)), timeout=HTTP_TIMEOUT).ok
```
- Called once, for the winner. There's no retry: a timed-out bill may have arrived anyway, and
  billing twice would be worse. Whether it succeeded is recorded in `auctions.billed`.

### The new routes in [app.py](app.py)

**`GET /ad`**

```python
if request.method == 'HEAD':
    return '', 200
```
- Flask sends HEAD to GET routes. Without this, a HEAD request would run an auction and charge a DSP.

```python
client_id = get_client_id()
is_new = client_id is None
if is_new:
    client_id = uuid.uuid4()
with db() as conn:
    log_request(conn, client_id)
    searches = conn.execute("SELECT query FROM searches WHERE client_id = %s
                             ORDER BY ts DESC, search_id DESC LIMIT %s", ...).fetchall()
    blocked = blocked_advertisers(conn)
```
- Same identity logic as `/`, so DSPs always get a valid ID, even if `/ad` is the first thing a
  visitor hits (the spec doesn't cover that case).
- Newest first, the order the training data used.

```python
segments = model.segments("\n".join(query for query, in searches))
auction = run_auction(client_id, segments, blocked, request.headers.get('User-Agent', ''), request.remote_addr)
```
- The first `with db()` block has already ended, so **no database connection is held** during the
  wait for bids.
- `for query, in searches` unpacks each one-column row.

```python
winner = auction['winner'] or {}
with db() as conn:
    conn.execute("INSERT INTO auctions (...) VALUES (...)", ...)
```
- Every auction is recorded, even with no winner: the row is how `/ad/feedback` later finds out
  who won, and how complaint rates are computed.

```python
html = f'<!doctype html><html><body style="margin:0">{winner["adm"]}</body></html>' if winner else ''
response = app.response_class(html, mimetype='text/html')
response.headers['Cache-Control'] = 'no-store'
response.headers['Content-Security-Policy'] = 'sandbox allow-popups allow-popups-to-escape-sandbox'
```
- With no winner, this is a 200 with an empty body, as the spec requires.
- `margin:0` removes the browser's default 8 px margin so the ad fits the slot exactly.
- `no-store` matters: a cached response would show an ad with no auction and no payment.

**Blocking advertisers**

```python
def blocked_advertisers(conn):
    rows = conn.execute(
        "SELECT domain FROM auctions, unnest(adomain) AS domain GROUP BY domain "
        "HAVING count(*) FILTER (WHERE complained) >= %s "
        "AND count(*) FILTER (WHERE complained) >= %s * count(*)",
        (MIN_COMPLAINTS, MAX_COMPLAINT_RATE)).fetchall()
    return {domain for domain, in rows}
```
- `unnest` gives one row per domain per auction, `GROUP BY` collects them, and `count(*)` is that
  advertiser's impressions. `FILTER` counts only the complained ones. `HAVING` filters after grouping.
- An advertiser is blocked with **at least 2 complaints and at least 5% of impressions complained
  about**. Two conditions, because one complaint could be bad luck, and a few complaints on an
  advertiser with thousands of impressions are normal. Blocking steady by mistake would hurt twice:
  we'd lose its ads, and without its bid, second prices would drop to the floor.
- These two numbers are starting guesses: the handout doesn't say what a complaint costs.
- Blocked domains go in `badv`, so the DSP won't bid them, and shady's domains eventually drop out.
  Niche's ~$5 bids can then win instead of being outbid by shady's ~$6.

**`POST /ad/feedback`**

```python
body = request.get_json(force=True, silent=True)
if not isinstance(body, dict) or not isinstance(body.get('auction_id'), str):
    return jsonify({"error": ...}), 400
if body.get('event') != 'complaint':
    return '', 204
with db() as conn:
    found = conn.execute(
        "UPDATE auctions SET complained = true WHERE auction_id = %s AND bid_id IS NOT NULL "
        "RETURNING auction_id", (body['auction_id'],)).fetchone()
return ('', 204) if found else (jsonify({"error": "unknown auction"}), 404)
```
- `force=True` parses the body as JSON whatever its `Content-Type`. `silent=True` returns `None`
  on bad JSON instead of raising.
- `complained` is a boolean, so a repeated complaint about one auction counts once.
- `bid_id IS NOT NULL` ignores complaints about auctions nobody won.

### Serving it: [gunicorn.conf.py](gunicorn.conf.py)

```python
worker_class = 'gthread'
threads = 8
preload_app = True

def post_worker_init(worker):
    import ssp
    ssp.warm_connections()
```
- Gunicorn reads this file from its working directory automatically, so we didn't need to edit the
  service file.
- `preload_app` loads the model once before forking. The OS shares that memory between workers until
  one writes to it (*copy-on-write*), which matters on 1 GB. Each worker uses about 90 MB, and the VM
  had about 460 MB available after deploying.

[deploy.sh](deploy.sh) also gained `python -c 'import app'` before the restart, so broken code or a
missing model stops the deploy while the old version keeps serving.

### Design choices

| Situation | What we do | Why |
|---|---|---|
| Bid floor | 0.50 | the handout's example; below every steady bid we saw |
| Bid deadline | 400 ms, with `tmax` 250 sent to DSPs | keeps on-time bids that stalled in delivery; truly late bids start at 528 ms |
| Complaint rule | ≥ 2 complaints and ≥ 5% rate | one-off complaints shouldn't block steady |
| `/ad` with no cookie | mint an ID and set the cookie | DSPs always get a valid `user.id` |
| Response with no `cur` | treated as USD | OpenRTB's default |
| Billing fails | still show the ad, record `billed = false` | it already won; never retry |
| `HEAD /ad` | no auction | mustn't charge anyone |

### How it was tested

- **216 checks against a fake DSP** that sends each failure on demand: every filter (tested by
  offering a tempting $9 bad bid beside a legitimate one), every pricing case including stalled
  bids, exact-once billing, blocking, and 40 concurrent auctions.
- **The handout's `dsp.py`:** one bill at the right price, no warnings.
- **Real Chrome:** the ad fills the 300×250 iframe, and the iframe's request carried the cookie.
- **Live DSPs:** niche won all 6 auctions for a user with parent-style searches, every winner was
  billed, and 8 out of 8 auctions right after a restart served an ad.
- **The grader's first run (90/100):** 960 ad views at up to 20 auctions per second, net profit
  $1.28 where a naive SSP would make -$0.99, and niche won 222 ads. Ads that cost more in expected
  complaints than they bid fell from 19% in round 1 to 0% from round 3, so the complaint rule worked.
  The only failed check was the second-price one, fixed above.

---

## 7. Questions you should be able to answer

- **Why does the server return 303 after a search instead of 200?** To send the browser back to `/`
  (Post/Redirect/Get). The page reloads and fetches the new history, and refreshing doesn't
  resubmit the search.
- **Why is `/` always a 200 and never a 304?** `send_file(..., conditional=False)`. Repeat visits
  must still return 200 with the full page.
- **What happens if the cookie is already set?** No `Set-Cookie` is sent; we keep the existing ID
  and just update `last_seen`.
- **How does the server know a visitor's IP?** `request.remote_addr`, the TCP peer address. It's
  accurate because no proxy sits in front of gunicorn.
- **How is SQL injection prevented?** Parameterized queries (`%s` with a separate values tuple).
- **How is XSS prevented on the page?** `boogle.html` uses `textContent`, never `innerHTML`.
- **Why one transaction per request?** All-or-nothing writes, and a single `now()`, so the
  timestamps a request writes agree.
- **Why gunicorn and systemd?** Gunicorn is a production WSGI server with multiple workers;
  systemd starts it at boot and restarts it after crashes, because grading happens long after the
  deadline.
- **Why ports 80 and 8000?** `http://IP` defaults to port 80; we keep 8000 too. Binding port 80
  without root uses `CAP_NET_BIND_SERVICE`.
- **What is TF-IDF?** Word counts, damped (sublinear TF) and weighted by rarity across users
  (IDF), then normalized.
- **Why logistic regression?** It's fast, works well on sparse high-dimensional text, and its
  weights are interpretable. It's what the reference implementation uses.
- **Why `class_weight="balanced"`?** Macro-F1 weights every class equally; balancing makes training
  do the same, which raised mean dev macro-F1 from 0.671 to 0.704.
- **What are train, dev and test for?** Fit on train, choose settings on dev, predict test. Then
  refit on train + dev for the final predictions.
- **What does `C` control?** Inverse regularization strength: larger `C` fits the training data
  more closely, with more risk of overfitting.
- **What's the difference between an SSP and a DSP?** The SSP sells the slot for the publisher; DSPs
  buy on behalf of advertisers.
- **How is the price set?** Second price: `min(winner, max(second, floor) + 0.01)`. The bid floor is
  the minimum we accept, and it sets the price for a lone bidder.
- **Why ask all DSPs at once?** So one slow DSP can't use up the others' time. `wait` enforces the
  deadline.
- **Why is the deadline 400 ms when `tmax` is 250?** About 6% of on-time bids stall ~230 ms in
  delivery, which made us misprice auctions. Truly late bids start at 528 ms, so 400 ms separates them.
- **Which bids get filtered?** Late, invalid JSON, non-USD, wrong `impid`, not 300×250, below floor.
  We also drop a wrong response `id`, a bad price, an empty `adm`, a suspicious `burl`, and blocked
  advertisers.
- **Why does the worker keep connections warm?** A new TLS connection takes 300–535 ms from the VM,
  so auctions on cold connections would lose bids.
- **Why leave segments out?** A wrong claim misleads advertisers. We claim only at probability ≥ 0.7.
- **How does the model affect revenue?** Niche bids only on audiences it wants, such as "parent"
  (901). A confident claim brings in ~$5 bids and raises the second price.
- **How do complaints work?** Each marks an auction in the `auctions` table. An advertiser with at
  least 2 complaints and a rate of at least 5% goes into `badv`, and its bids are dropped locally too.
- **How is the ad kept from harming the page?** A sandboxed iframe with no scripts and no same-origin
  access, plus a matching CSP header on `/ad`.
- **Why isn't scikit-learn on the VM?** The pickle needs versions the VM can't install, so the same
  weights are exported to `model.npz`, and `export_model.py` proves the predictions match.
