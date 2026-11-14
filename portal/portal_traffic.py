"""Sample 3x-ui's counters into per-client, per-day usage."""
import datetime as dt
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')


@contextmanager
def database(state):
    conn = sqlite3.connect(Path(state) / 'traffic.db', timeout=12)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize(state):
    with database(state) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, first_seen INTEGER NOT NULL, sampled_at INTEGER NOT NULL,
                up INTEGER NOT NULL, down INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily (
                day TEXT NOT NULL, client_id INTEGER NOT NULL,
                up INTEGER NOT NULL, down INTEGER NOT NULL,
                PRIMARY KEY(day, client_id)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS daily_by_client ON daily(client_id, day);
            CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                first_sample INTEGER NOT NULL, last_sample INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coverage (
                day TEXT PRIMARY KEY, seconds INTEGER NOT NULL
            ) WITHOUT ROWID;
        """)


def calendar_day(ts):
    return dt.datetime.fromtimestamp(ts, TZ).date()


def intervals(start, end):
    """Split an observation interval at local midnight."""
    while start < end:
        day = calendar_day(start)
        midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), TZ)
        stop = min(end, int(midnight.timestamp()))
        yield day.isoformat(), start, stop
        start = stop


def add_daily(db, client_id, start, end, up, down):
    duration = end - start
    if duration <= 0 or not (up or down):
        return
    for day, first, last in intervals(start, end):
        # Cumulative integer allocation preserves the exact byte total.
        portion = [value * (last-start) // duration - value * (first-start) // duration
                   for value in (up, down)]
        db.execute("""INSERT INTO daily VALUES(?,?,?,?)
                      ON CONFLICT(day,client_id) DO UPDATE SET
                      up=daily.up+excluded.up, down=daily.down+excluded.down""", (day, client_id, *portion))


def sample_locked(db, fetch, now=None):
    rows = fetch('clients/list')
    if not isinstance(rows, list):
        raise RuntimeError('3x-ui returned an invalid client list')
    now = int(time.time()) if now is None else now
    state = db.execute('SELECT * FROM state WHERE id=1').fetchone()
    if state and now < state['last_sample']:
        return
    start = state['last_sample'] if state else now
    # Long outages are visible as incomplete coverage. Their measured counter
    # difference still contributes to the totals.
    if now - start <= 120:
        for day, first, last in intervals(start, now):
            db.execute("""INSERT INTO coverage VALUES(?,?) ON CONFLICT(day) DO UPDATE
                          SET seconds=coverage.seconds+excluded.seconds""", (day, last-first))
    previous = {row['id']: row for row in db.execute('SELECT * FROM clients')}
    for item in rows:
        client = item.get('client', item)
        if int(os.environ.get('CCVPN_INBOUND_ID', '1')) not in client.get('inboundIds', []):
            continue
        traffic = client.get('traffic') or {}
        up, down = traffic.get('up'), traffic.get('down')
        if not isinstance(up, int) or not isinstance(down, int) or min(up, down) < 0:
            continue
        client_id = client['id']
        old = previous.get(client_id)
        if old is not None:
            delta = [count - old[key] if count >= old[key] else count
                     for key, count in (('up', up), ('down', down))]
            # Two samples can be taken in the same second around a reset.
            if now > old['sampled_at']:
                add_daily(db, client_id, old['sampled_at'], now, *delta)
            elif any(delta):
                add_daily(db, client_id, now, now+1, *delta)
        db.execute("""INSERT INTO clients VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE
                      SET name=excluded.name,sampled_at=excluded.sampled_at,up=excluded.up,down=excluded.down""",
                   (client_id, client['email'], now, now, up, down))
    db.execute("""INSERT INTO state VALUES(1,?,?) ON CONFLICT(id) DO UPDATE
                  SET last_sample=excluded.last_sample""", (now, now))


def collect(state, fetch, now=None):
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        sample_locked(db, fetch, now)


def around_counter_change(state, fetch, action):
    # Prevent the timer sampling out of order while a portal reset is in flight.
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        sample_locked(db, fetch)
        result = action()
        sample_locked(db, fetch)
        return result


def report(state, first, last, client_id, shown, now=None):
    today = calendar_day(int(time.time()) if now is None else now)
    with database(state) as db:
        db.execute('BEGIN')
        snapshot = db.execute('SELECT * FROM state WHERE id=1').fetchone()
        account = (db.execute('SELECT first_seen FROM clients WHERE id=?', (client_id,)).fetchone()
                   if client_id is not None else None)
        start = ((account['first_seen'] if account else None) if client_id is not None else
                 (snapshot['first_sample'] if snapshot else None))
        end = snapshot['last_sample'] if snapshot else None
        where = 'day BETWEEN ? AND ?'
        identity = ()
        if client_id is not None:
            where += ' AND client_id=?'
            identity = (client_id,)
        sum_row = db.execute('SELECT SUM(up), SUM(down) FROM daily WHERE ' + where,
                             (first.isoformat(), last.isoformat(), *identity)).fetchone()
        totals = None
        if start is not None:
            first_sample, last_sample = calendar_day(start), calendar_day(end)
            observed = (sum_row[0] is not None or first <= first_sample <= last or first <= last_sample <= last or
                        (last >= first_sample and db.execute('SELECT 1 FROM coverage WHERE day BETWEEN ? AND ? LIMIT 1',
                                                            (max(first, first_sample).isoformat(), last.isoformat())).fetchone()))
            if observed:
                totals = tuple(value or 0 for value in sum_row)
        shown_first, shown_last = shown
        display_bounds = (shown_first.isoformat(), shown_last.isoformat())
        amounts = {r['day']: (r['up'], r['down']) for r in db.execute(
            'SELECT day,SUM(up) AS up,SUM(down) AS down FROM daily WHERE ' + where + ' GROUP BY day',
            (*display_bounds, *identity))}
        coverage = {r['day']: r['seconds'] for r in db.execute(
            'SELECT day,seconds FROM coverage WHERE day BETWEEN ? AND ?', display_bounds)}
    rows = []
    for offset in range((shown_last-shown_first).days + 1):
        day = shown_first + dt.timedelta(days=offset)
        key = day.isoformat()
        seconds = coverage.get(key, 0)
        up, down = amounts.get(key, (0, 0))
        if start is not None and day >= first_sample and (seconds or up or down or day in (first_sample, last_sample)):
            status = '今日' if day == today else '完整' if seconds >= 86340 and day > first_sample else '部分采集'
            rows.append((key, up, down, status))
        else:
            rows.append((key, None, None, '未采集'))
    return start, end, totals, rows
