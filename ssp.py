"""The Supply-Side Platform: runs one OpenRTB auction for Boogle's ad slot."""

import json
import math
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait

import requests
from requests.adapters import HTTPAdapter

RTB_URL = os.environ.get('RTB_URL', 'https://webtracking.sysnet.ucsd.edu/pa1/rtb').rstrip('/')
DSPS = ('steady', 'niche', 'shady', 'glitch')
PRICE_MACRO = '${AUCTION_PRICE}'

IMP_ID = '1'
SLOT_W, SLOT_H = 300, 250
BID_FLOOR = 0.50    # CPM in USD
TMAX_MS = 250       # how long we tell DSPs they have to answer
# When we stop listening, which is later than tmax on purpose. About 6% of the exchange's
# responses stall for around 230ms on their way to us, on every URL and from every network we
# tried, so bids the DSP sent on time arrive at 270-385ms. Bids that really are late start at
# 528ms, so this keeps the first group and still drops the second.
DEADLINE_MS = 400
HTTP_TIMEOUT = 2    # seconds, for anything sent to the exchange

# A new TLS connection takes 300-535ms from the VM, so an auction that has to open one can lose
# that DSP's bid. One shared session reuses connections instead (30-75ms).
SPARE_CONNECTIONS = 8   # more than one auction needs: late DSPs can still be holding some
WARM_EVERY = 60         # seconds
last_warmed = 0.0

session = requests.Session()
for scheme in ('https://', 'http://'):
    session.mount(scheme, HTTPAdapter(pool_maxsize=32))
executor = ThreadPoolExecutor(max_workers=32)


def ping():
    try:
        session.get(RTB_URL + '/', timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        pass


def warm_connections():
    """Opens spare connections in the background, at most once per WARM_EVERY.

    Runs when a worker starts and on incoming requests rather than on a timer, so the
    exchange gets no traffic from us while nobody is using Boogle.
    """
    global last_warmed
    if time.monotonic() - last_warmed >= WARM_EVERY:
        last_warmed = time.monotonic()
        # Sent together, so that each needs a connection of its own.
        for _ in range(SPARE_CONNECTIONS):
            executor.submit(ping)


def build_request(auction_id, client_id, segments, blocked, user_agent, ip):
    return {
        "id": auction_id,
        "imp": [{
            "id": IMP_ID,
            "tagid": "boogle-top",
            "banner": {"format": [{"w": SLOT_W, "h": SLOT_H}]},
            "bidfloor": BID_FLOOR,
        }],
        "device": {"ua": user_agent, "ip": ip},
        "user": {
            "id": str(client_id),
            "data": [{
                "id": "boogle",
                "name": "boogle-inferred",
                "segment": [{"id": segment} for segment in segments],
                "ext": {"segtax": 500},
            }],
        },
        "tmax": TMAX_MS,
        "badv": sorted(blocked),
    }


def ask_dsp(dsp, bid_request):
    # collect_bids enforces the deadline. Timing out here too would close the connection, and
    # then some other DSP pays for opening a new one in the next auction.
    try:
        return session.post(f'{RTB_URL}/{dsp}/bid', json=bid_request, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return None


def advertiser_domains(bid):
    adomain = bid.get('adomain')
    return [d for d in adomain if isinstance(d, str)] if isinstance(adomain, list) else []


def is_valid_bid(bid, blocked):
    if not isinstance(bid, dict):
        return False
    price, adm, burl = bid.get('price'), bid.get('adm'), bid.get('burl')
    return (
        isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price)
        and price >= BID_FLOOR
        and bid.get('impid') == IMP_ID
        and bid.get('w') == SLOT_W and bid.get('h') == SLOT_H
        and isinstance(adm, str) and adm.strip() != ''
        # We make a request to this URL, so it must really be the exchange's billing endpoint.
        and isinstance(burl, str) and burl.startswith(RTB_URL + '/') and PRICE_MACRO in burl
        # DSPs honor badv, but a bid that ignored it shouldn't be able to win anyway.
        and not blocked.intersection(advertiser_domains(bid)))


def parse_bids(response, auction_id, blocked):
    """Returns every usable bid in one DSP's response, each tagged with its seat."""
    if response is None or response.status_code != 200:
        return []
    try:
        body = json.loads(response.content)
    except ValueError:
        return []
    if not isinstance(body, dict) or body.get('id') != auction_id:
        return []
    # OpenRTB says a response with no cur is in USD.
    if body.get('cur', 'USD') != 'USD' or not isinstance(body.get('seatbid'), list):
        return []
    bids = []
    for seatbid in body['seatbid']:
        if isinstance(seatbid, dict) and isinstance(seatbid.get('bid'), list):
            bids += [{**bid, 'seat': seatbid.get('seat')} for bid in seatbid['bid']
                     if is_valid_bid(bid, blocked)]
    return bids


def collect_bids(bid_request, blocked):
    """Asks all DSPs at once and keeps what arrived by the deadline. Late responses are ignored."""
    futures = [executor.submit(ask_dsp, dsp, bid_request) for dsp in DSPS]
    done, _ = wait(futures, timeout=DEADLINE_MS / 1000)
    bids = []
    for future in futures:
        if future in done:
            bids += parse_bids(future.result(), bid_request['id'], blocked)
    return bids


def clearing_price(prices):
    """Second-price rule, given all valid bids from highest to lowest."""
    second = prices[1] if len(prices) > 1 else 0
    return min(prices[0], round(max(second, BID_FLOOR) + 0.01, 4))


def bill(bid, price):
    try:
        return session.get(bid['burl'].replace(PRICE_MACRO, str(price)), timeout=HTTP_TIMEOUT).ok
    except requests.RequestException:
        return False


def run_auction(client_id, segments, blocked, user_agent, ip):
    """Runs one auction and bills the winner.

    Returns a dict with the auction_id, plus winner, price and billed when anyone won.
    """
    auction_id = uuid.uuid4().hex[:16]
    bid_request = build_request(auction_id, client_id, segments, blocked, user_agent, ip)
    bids = collect_bids(bid_request, blocked)
    if not bids:
        return {"auction_id": auction_id, "winner": None, "price": None, "billed": False}
    bids.sort(key=lambda bid: bid['price'], reverse=True)
    winner = bids[0]
    price = clearing_price([bid['price'] for bid in bids])
    return {"auction_id": auction_id, "winner": winner, "price": price, "billed": bill(winner, price)}
