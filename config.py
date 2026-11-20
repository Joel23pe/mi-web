import os

# --- Call Servers (red de administración) ---
PRIMARY_CS_IP = "10.16.0.100"
SECONDARY_CS_IP = "10.16.0.11"
SSH_PORT = 22

# --- Credenciales: se leen de variables de entorno (no las dejes en el código) ---
#   Windows (PowerShell):  $env:CS_SSH_USER="usuario"; $env:CS_SSH_PASS="clave"
#   Linux/Mac:             export CS_SSH_USER=usuario CS_SSH_PASS=clave
SSH_USER = os.environ.get("CS_SSH_USER", "kk")
SSH_PASS = os.environ.get("CS_SSH_PASS", "102030")

# Usuario / password para LOGI en el overlay del CS1000
TTY_USER = os.environ.get("CS_TTY_USER", "kk")
TTY_PASS = os.environ.get("CS_TTY_PASS", "102030")

# --- Canales D a monitorear ---
D_CHANNELS = ["010", "017", "204"]

# --- Comandos LD 117 (ajústalos si en tu release son distintos) ---
# Pruébalos a mano por TTY y usa el que realmente devuelva datos.
IPMG_CMD = "STAT IPMG"
SS_CMD = "STAT SS"

# --- Otros ---
POLL_INTERVAL = 30      # segundos entre recolecciones
DEBUG = True            # imprime la salida cruda de cada comando en consola