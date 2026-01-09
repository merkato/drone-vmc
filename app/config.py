# config.py
import os
from pathlib import Path

# Środowisko i domena
DOMAIN = os.getenv('DOMAIN', 'localhost')
STORAGE_SECRET = os.getenv('STORAGE_SECRET', 'PesaToNajgorszyProducentTaboruNaSwiecie')

# Ścieżki i serwery
RECORDINGS_DIR = Path("/recordings")
MEDIAMTX_API = "http://mediamtx:9997/v3"
MEDIAMTX_WEBRTC = f"https://stream.{DOMAIN}"