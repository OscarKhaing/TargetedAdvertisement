# PA1 write-up: what we built and why

Study notes covering Parts 1–3 of PA1. Each section explains the concepts first, then walks
through the code. Code walkthroughs quote the real files: [app.py](app.py),
[deploy.sh](deploy.sh), [deploy-remote.sh](deploy-remote.sh),
[starter_3_4/part3/train_model.py](starter_3_4/part3/train_model.py).

Contents

1. [The big picture](#1-the-big-picture)
2. [Part 1: a simple web server](#2-part-1-a-simple-web-server)
3. [Deployment: getting it on the internet](#3-deployment-getting-it-on-the-internet)
4. [Part 2: tracking users with a cookie and a database](#4-part-2-tracking-users-with-a-cookie-and-a-database)
5. [Part 3: inferring demographics from searches](#5-part-3-inferring-demographics-from-searches)
6. [Questions you should be able to answer](#6-questions-you-should-be-able-to-answer)

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
  age, income and so on. Part 4 will plug this model into the server to target ads.

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
  byte for byte. `app.root_path` is the folder containing `app.py`, so the path works no matter
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
- Lessons from debugging: a key pasted by hand into `authorized_keys` was later removed by
  Google's agent, so the durable way is **Compute Engine → Metadata → SSH keys**. That console form
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
sudo systemctl restart boogle
...                                    # if the service isn't active, print its logs and fail
curl -fsS http://localhost/jsontest
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
  `"   padded with spaces   "`. That fix took Part 2 from 94/100 to all checks passing.
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
`model.joblib` is also produced, for Part 4. It isn't part of the submission.

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
- `joblib.dump` saves the fitted model so Part 4 can load it with `joblib.load` instead of
  retraining. Part 4 must use "exactly your model from part 3".

I checked the output: 3,000 rows matching the test users in order, the exact header, and every
value a valid label. Every class of every attribute gets predicted at least sometimes.

---

## 6. Questions you should be able to answer

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
