import asyncio
import threading
import subprocess
import os
import websockets
import time

# ==========================================================
# KONFIGURASI WEBSOCKET
# ==========================================================
WS_HOST = '192.168.1.103'
WS_PORT = 8007

loop_ws = None

# mapping websocket -> asyncio.Queue
client_queues = {}

async def sender_loop(ws, q: asyncio.Queue):
    """Task pengirim untuk 1 client. Ambil data dari queue dan kirim."""
    try:
        while True:
            data = await q.get()
            await ws.send(data)
    except Exception:
        # client putus / error kirim
        pass

async def ws_handler(websocket):
    # Queue maxsize=1 = latest only (drop backlog)
    q = asyncio.Queue(maxsize=1)
    client_queues[websocket] = q
    send_task = asyncio.create_task(sender_loop(websocket, q))

    try:
        await websocket.wait_closed()
    finally:
        client_queues.pop(websocket, None)
        send_task.cancel()
        try:
            await send_task
        except Exception:
            pass

async def websocket_server():
    async with websockets.serve(
        ws_handler,
        WS_HOST,
        WS_PORT,
        ping_interval=20,
        ping_timeout=20,
        max_size=None,   # opsional: boleh besar karena kita kirim chunk
    ):
        print(f"[WebSocket] Running on ws://{WS_HOST}:{WS_PORT}")
        await asyncio.Future()  # run forever

def start_websocket_thread():
    global loop_ws
    loop_ws = asyncio.new_event_loop()

    def run():
        asyncio.set_event_loop(loop_ws)
        loop_ws.run_until_complete(websocket_server())

    threading.Thread(target=run, daemon=True).start()

def _fanout_mpeg(data: bytes):
    """Jalankan di thread event loop: push ke queue tiap client, drop kalau penuh."""
    if not client_queues:
        return

    dead = []
    for ws, q in list(client_queues.items()):
        try:
            if q.full():
                # buang data lama, keep latest
                _ = q.get_nowait()
            q.put_nowait(data)
        except Exception:
            dead.append(ws)

    for ws in dead:
        client_queues.pop(ws, None)

def broadcast_mpeg(data: bytes):
    """Dipanggil dari thread ffmpeg_reader: aman lewat call_soon_threadsafe."""
    if loop_ws is None:
        return
    loop_ws.call_soon_threadsafe(_fanout_mpeg, data)

# ==========================================================
# KONFIGURASI FFMPEG
# ==========================================================
FFMPEG_CMD = [
    'ffmpeg',
    '-f', 'rawvideo',
    '-pix_fmt', 'bgr24',
    '-s', '1280x720',
    '-r', '25',
    '-i', '-',
    '-fflags', 'nobuffer',
    '-flush_packets', '1',
    '-analyzeduration', '0',
    '-probesize', '32',
    '-f', 'mpegts',
    '-codec:v', 'mpeg1video',
    '-bf', '0',
    '-b:v', '800k',
    '-r', '25',
    '-'
]

ffmpeg_process = subprocess.Popen(
    FFMPEG_CMD,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    bufsize=0
)

# stdout non-blocking (Linux)
if os.name != 'nt':
    import fcntl
    fd = ffmpeg_process.stdout.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def ffmpeg_reader():
    while True:
        try:
            mpeg_data = ffmpeg_process.stdout.read(65536)
            if mpeg_data:
                broadcast_mpeg(mpeg_data)
            else:
                time.sleep(0.002)  # cegah busy loop
        except BlockingIOError:
            time.sleep(0.002)
        except Exception:
            time.sleep(0.05)

threading.Thread(target=ffmpeg_reader, daemon=True).start()

# ==========================================================
# FUNGSI UNTUK DIPANGGIL DARI FILE UTAMA
# ==========================================================
def send_frame_to_clients(frame):
    """Kirim frame OpenCV (BGR) ke FFmpeg. Skip kalau tidak ada client."""
    if not client_queues:
        return

    try:
        ffmpeg_process.stdin.write(frame.tobytes())
        # flush tidak perlu karena bufsize=0
    except BrokenPipeError:
        # ffmpeg mati -> sebaiknya restart ffmpeg (opsional)
        pass
    except Exception:
        pass
