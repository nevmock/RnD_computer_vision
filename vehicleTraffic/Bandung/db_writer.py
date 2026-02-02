# db_writer.py
import time
import threading
from queue import Queue, Full, Empty
import psycopg2

# Batasi queue supaya tidak makan RAM tak terbatas
DB_QUEUE_MAX = 5000
db_queue = Queue(maxsize=DB_QUEUE_MAX)

DB_CFG = dict(
    host="localhost",
    database="vehicle_traffic",
    user="postgres",
    password="123",
    port="5432"
)

INSERT_SQL = """
    INSERT INTO vehicle_count (loc, direction, date, vehicle_type, count)
    VALUES (%s, %s, %s, %s, %s)
"""

def _connect():
    # connect + autocommit false (default)
    return psycopg2.connect(**DB_CFG)

def db_worker():
    conn = None
    cur = None
    backoff = 0.5
    MAX_BACKOFF = 10.0

    batch = []
    BATCH_SIZE = 50          # commit per 50 row
    BATCH_FLUSH_SEC = 1.0    # atau flush tiap 1 detik
    last_flush = time.monotonic()

    while True:
        try:
            item = db_queue.get(timeout=0.5)
        except Empty:
            item = None

        # flush batch by time even if no new item
        now = time.monotonic()
        time_to_flush = (batch and (now - last_flush >= BATCH_FLUSH_SEC))

        if item is None and not time_to_flush:
            continue

        if item is None and time_to_flush:
            # proceed flush
            pass
        else:
            # sentinel stop (optional kalau kamu mau stop)
            if item == "__STOP__":
                break
            batch.append(item)
            db_queue.task_done()

        # flush kondisi size/time
        if not batch:
            continue
        if len(batch) < BATCH_SIZE and (time.monotonic() - last_flush) < BATCH_FLUSH_SEC:
            continue

        # pastikan koneksi siap
        try:
            if conn is None or conn.closed:
                conn = _connect()
                cur = conn.cursor()
                backoff = 0.5

            cur.executemany(INSERT_SQL, batch)
            conn.commit()
            batch.clear()
            last_flush = time.monotonic()

        except Exception as e:
            print("DB Error:", e, flush=True)

            # rollback jika perlu
            try:
                if conn and not conn.closed:
                    conn.rollback()
            except Exception:
                pass

            # close & reset connection
            try:
                if cur:
                    cur.close()
            except Exception:
                pass
            try:
                if conn and not conn.closed:
                    conn.close()
            except Exception:
                pass

            conn, cur = None, None

            # backoff supaya tidak loop ngebut saat error
            time.sleep(backoff)
            backoff = min(MAX_BACKOFF, backoff * 2)

def start_db_thread():
    threading.Thread(target=db_worker, daemon=True).start()

def send_to_db(loc, direction, date, vehicle_type, count):
    data = (loc, direction, date, vehicle_type, count)
    try:
        db_queue.put_nowait(data)
    except Full:
        # Queue penuh -> drop event (atau bisa log 1x per sekian detik)
        # Aman untuk counter karena lebih penting sistem tetap hidup
        pass
