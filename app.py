import threading
import time
from datetime import datetime
from flask import Flask, render_template, jsonify
import config
from monitor import CS1000Monitor

app = Flask(__name__)

lock = threading.Lock()

# Estructura global en memoria compartida
last_data = {
    "status": "OFFLINE",
    "active_host": "N/A",
    "last_update": "N/A",
    "ss_status": {
        "pbxlink_state": "UNKNOWN",
        "sets": {"registered": 0, "busy": 0},
        "vtrk": {"registered": 0, "busy": 0}
    },
    "ipmg": [],
    "dchannels": [],
    "errors": []
}
last_raw = {}  # salida cruda de los comandos (para /api/debug)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def collect_from(host):
    """Consulta un Call Server. Cada sección falla de forma independiente."""
    mon = CS1000Monitor(host, config.SSH_PORT, config.SSH_USER, config.SSH_PASS)
    errors = []
    mon.connect()
    print(f"[OK] Sesión SSH/TTY iniciada en {host}")
    try:
        # 1. Canales D
        print("[INFO] Consultando canales D (LD 96/60)...")
        dch_results = []
        for dch in config.D_CHANNELS:
            try:
                dch_results.append(mon.get_dchannel_status(dch))
            except Exception as e:
                errors.append(f"DCH {dch}: {e}")
                dch_results.append({"dch": dch, "status": "UNKNOWN", "detail": ""})

        # 2. IPMG
        print("[INFO] Consultando IPMG (LD 117)...")
        try:
            ipmg_results = mon.get_ipmg_status()
        except Exception as e:
            errors.append(f"IPMG: {e}")
            ipmg_results = []

        # 3. Signaling Server
        print("[INFO] Consultando Signaling Server (LD 117)...")
        try:
            ss_results = mon.get_ss_status()
        except Exception as e:
            errors.append(f"SS: {e}")
            ss_results = {
                "pbxlink_state": "UNKNOWN",
                "sets": {"registered": 0, "busy": 0},
                "vtrk": {"registered": 0, "busy": 0},
            }
    finally:
        raw = dict(mon.raw)
        mon.close()

    data = {
        "status": "ONLINE",
        "active_host": host,
        "last_update": now(),
        "ss_status": ss_results,
        "ipmg": ipmg_results,
        "dchannels": dch_results,
        "errors": errors,
    }
    return data, raw


def background_collector():
    global last_data, last_raw
    hosts = [config.PRIMARY_CS_IP, config.SECONDARY_CS_IP]

    while True:
        connected = False
        for host in hosts:
            if not host:
                continue
            print(f"[INFO] Conectando a Call Server en {host}...")
            try:
                data, raw = collect_from(host)
                with lock:
                    last_data = data
                    last_raw = raw
                print("[OK] Recolección completada.")
                connected = True
                break
            except Exception as e:
                print(f"[ERROR] Error al consultar {host}: {e}")

        if not connected:
            with lock:
                last_data = {**last_data,
                             "status": "OFFLINE",
                             "active_host": "N/A",
                             "last_update": now()}

        time.sleep(config.POLL_INTERVAL)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def get_status():
    with lock:
        return jsonify(last_data)


@app.route('/api/debug')
def get_debug():
    """Salida cruda de cada comando: útil para ajustar los parsers."""
    with lock:
        return jsonify(last_raw)


if __name__ == '__main__':
    t = threading.Thread(target=background_collector, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=5000, debug=False)