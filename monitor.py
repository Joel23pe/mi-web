import re
import time
import paramiko
import config

# Prompt del Call Server: ">" o "=>" SOLO en una línea al final de la salida
PROMPT_RE = re.compile(r"(?:^|\n)[ \t]*(?:=>|>)[ \t]*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def clean(text):
    """Quita secuencias ANSI y normaliza saltos de línea."""
    text = ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


class CS1000Monitor:
    def __init__(self, host, port, user, password):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.client = None
        self.channel = None
        self.raw = {}  # salida cruda por comando (para depurar)

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    def connect(self):
        """Abre sesión SSH ajustando algoritmos legacy para Call Server / CPDC"""
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kex_legacy = [
            'diffie-hellman-group-exchange-sha1',
            'diffie-hellman-group14-sha1',
            'diffie-hellman-group1-sha1'
        ]
        ciphers_legacy = [
            'aes128-ctr', 'aes192-ctr', 'aes256-ctr',
            'aes128-cbc', 'aes192-cbc', 'aes256-cbc',
            '3des-cbc'
        ]
        macs_legacy = ['hmac-sha1', 'hmac-sha1-96', 'hmac-md5']
        key_types_legacy = ['ssh-rsa', 'ssh-dss']

        try:
            paramiko.Transport._preferred_kex = tuple(kex_legacy)
            paramiko.Transport._preferred_ciphers = tuple(ciphers_legacy)
            paramiko.Transport._preferred_macs = tuple(macs_legacy)
            paramiko.Transport._preferred_keys = tuple(key_types_legacy)
        except Exception as e:
            print(f"[WARN] No se pudieron ajustar las preferencias de Transport: {e}")

        self.client.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            timeout=10,
            look_for_keys=False,
            allow_agent=False
        )

        self.channel = self.client.invoke_shell(width=200, height=50)
        time.sleep(1)
        self._drain(2)

        # Autenticación TTY
        self._send_cmd("cslogin", wait=2)
        self._send_cmd(f"LOGI {config.TTY_USER}", wait=1.5)
        auth_out = self._send_cmd(config.TTY_PASS, wait=2)
        self.raw["LOGIN"] = auth_out
        if config.DEBUG:
            print("\n--- [DEBUG LOGIN RAW OUTPUT] ---")
            print(auth_out)
            print("--------------------------------\n")

        up = auth_out.upper()
        if any(bad in up for bad in ("INCORRECT", "INVALID", "FAIL", "DENIED", "BAD PASSWORD")):
            raise Exception(f"Login TTY rechazado en el Call Server: {auth_out.strip()[:120]}")
        if not up.strip():
            raise Exception("Sin respuesta tras LOGI en el Call Server (revisa usuario/clave TTY).")

    # ------------------------------------------------------------------
    # Lectura de la sesión
    # ------------------------------------------------------------------
    def _drain(self, timeout=0.5):
        """Lee y descarta/retorna todo lo pendiente en el buffer."""
        output = ""
        end = time.time() + timeout
        while time.time() < end:
            if self.channel.recv_ready():
                output += self.channel.recv(65535).decode('utf-8', errors='ignore')
                end = time.time() + 0.3
            else:
                time.sleep(0.1)
        return clean(output)

    def _read_until_prompt(self, timeout=10.0, prompt_quiet=0.8, hard_quiet=4.0):
        """
        Lee hasta que:
          - llegó el prompt (">" o "=>" al final) y hubo silencio `prompt_quiet`, o
          - hubo silencio total `hard_quiet` con datos recibidos, o
          - se acabó `timeout`.
        """
        output = ""
        start = time.time()
        last_data = time.time()
        while time.time() - start < timeout:
            if self.channel.recv_ready():
                output += self.channel.recv(65535).decode('utf-8', errors='ignore')
                last_data = time.time()
                continue
            idle = time.time() - last_data
            text = clean(output)
            if text.strip():
                if PROMPT_RE.search(text) and idle > prompt_quiet:
                    break
                if idle > hard_quiet:
                    break
            time.sleep(0.1)
        return clean(output)

    def _send_cmd(self, cmd, wait=1.5):
        self.channel.send(cmd + "\r")
        time.sleep(wait)
        return self._drain(1.0)

    def _exec_overlay(self, ld_number, cmd, key):
        """Entra al overlay `ld_number`, ejecuta `cmd` y devuelve la salida cruda."""
        self._drain(0.3)                       # descarta basura pendiente

        self.channel.send("****\r")          # salir de cualquier overlay previo
        time.sleep(0.5)
        self._drain(1.0)

        self.channel.send(f"LD {ld_number}\r")
        load = self._read_until_prompt(timeout=6, prompt_quiet=0.5)
        self.raw[key + "_LOAD"] = load
        if config.DEBUG:
            print(f"\n--- [DEBUG LOAD LD {ld_number}] ---")
            print(load)
            print("-" * 40)
        if "IDLE" in load.upper():
            print(f"[WARN] LD {ld_number} no parece haberse cargado (el sistema sigue en OVL IDLE).")

        self.channel.send(f"{cmd}\r")
        out = self._read_until_prompt(timeout=15)

        self.raw[key] = out
        if config.DEBUG:
            print(f"\n--- [DEBUG {key} (LD {ld_number}: {cmd})] ---")
            print(out)
            print("-" * 40 + "\n")
        return out

    # ------------------------------------------------------------------
    # Canales D
    # ------------------------------------------------------------------
    @staticmethod
    def _find_dch_line(raw, number):
        m = re.search(rf"^\s*DCH\s+0*{number}\b.*$", raw, re.IGNORECASE | re.MULTILINE)
        return m.group(0).strip() if m else ""

    def get_dchannel_status(self, dch_number):
        n = int(dch_number)
        raw = self._exec_overlay("96", f"STAT DCH {n}", f"DCH_{dch_number}")
        line = self._find_dch_line(raw, n)

        if not line:  # fallback a LD 60
            raw = self._exec_overlay("60", f"STAT DCH {n}", f"DCH_{dch_number}_LD60")
            line = self._find_dch_line(raw, n)

        u = line.upper()
        if not line:
            status = "UNKNOWN"
        elif re.search(r"\bDSBL\b|DISABLED", u):
            status = "DISABLED"
        elif re.search(r"\bRLS\b|\bREL\b|RELEASED", u):
            status = "RELEASED"
        elif re.search(r"\bEST\b", u):
            status = "ESTABLISHED"
        elif re.search(r"\bIDLE\b", u):
            status = "IDLE"
        else:
            status = "UNKNOWN"

        return {"dch": dch_number, "status": status, "detail": line}

    # ------------------------------------------------------------------
    # IPMG
    # ------------------------------------------------------------------
    def get_ipmg_status(self):
        raw = self._exec_overlay("117", config.IPMG_CMD, "IPMG")

        ipmg_list = []
        seen = set()
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.upper().startswith(("STAT", "OVL", "LD ", "****")):
                continue
            if any(w in s.upper() for w in ("ERROR", "INVALID", "SCH")):
                continue
            # "004 0 ..." / "IPMG 004 ..." -> id, tipo, estado
            m = re.match(r"^(?:IPMG\s+)?(\d{1,3}(?:\s+\d{1,2})?)\s+([A-Za-z0-9/_-]+)\s+([A-Za-z0-9/_-]+)", s)
            if not m:
                continue
            ipmg_id = m.group(1).strip()
            if ipmg_id in seen:
                continue
            seen.add(ipmg_id)
            ipmg_list.append({
                "ipmg": ipmg_id,
                "type": m.group(2).strip(),
                "status": m.group(3).strip(),
            })
        return ipmg_list

    # ------------------------------------------------------------------
    # Signaling Server
    # ------------------------------------------------------------------
    def get_ss_status(self):
        raw = self._exec_overlay("117", config.SS_CMD, "SS")
        up = raw.upper()

        link_state = "UNKNOWN"
        if "LINK UP" in up:
            link_state = "LINK UP"
        elif "LINK DOWN" in up:
            link_state = "LINK DOWN"
        else:
            m = re.search(r"PBX\s*LINK[^\n]*?\b(UP|DOWN)\b", up)
            if m:
                link_state = f"LINK {m.group(1)}"

        def grab(label):
            m = re.search(
                rf"{label}[^\n]*?reg\w*\s*[-:=]?\s*(\d+)[^\n]*?busy\s*[-:=]?\s*(\d+)",
                raw, re.IGNORECASE)
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

        sets_reg, sets_busy = grab(r"Sets?")
        vtrk_reg, vtrk_busy = grab(r"VTRK")

        return {
            "pbxlink_state": link_state,
            "sets": {"registered": sets_reg, "busy": sets_busy},
            "vtrk": {"registered": vtrk_reg, "busy": vtrk_busy},
        }

    # ------------------------------------------------------------------
    def close(self):
        try:
            if self.channel:
                self.channel.send("****\r")
                time.sleep(0.5)
                self._drain(1.0)              # deja que el PBX termine de escribir
                self.channel.send("LOGO\r")
                time.sleep(0.5)
                self._drain(1.5)              # lee la confirmación del logout
        except Exception:
            pass
        try:
            if self.channel:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.client:
                self.client.close()
        finally:
            self.client = None
            self.channel = None