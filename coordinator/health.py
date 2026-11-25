"""Local health check; never contacts an actuator."""
import json,sys,time
from pathlib import Path
try:
    heartbeat=json.loads(Path('/data/heartbeat.json').read_text())
    sys.exit(0 if time.time()-heartbeat['at']<45 else 1)
except (OSError,ValueError,KeyError):sys.exit(1)
