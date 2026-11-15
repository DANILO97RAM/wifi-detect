"""Storage layer: SQLite persistence for devices, custom names and alerts."""
import sqlite3
import threading
import time

from wifi_monitor.core.models import Device


class Storage:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                network_id TEXT, mac TEXT, ip TEXT, hostname TEXT, vendor TEXT,
                type TEXT, name TEXT DEFAULT '', online INTEGER,
                first_seen REAL, last_seen REAL, PRIMARY KEY (network_id, mac));
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, network_id TEXT, mac TEXT,
                message TEXT, created REAL, acknowledged INTEGER DEFAULT 0);
            """)

    def known_macs(self, network_id):
        with self.lock:
            rows = self.conn.execute("SELECT mac FROM devices WHERE network_id=?", (network_id,))
            return {r["mac"] for r in rows}

    def save_scan(self, network_id, devices):
        """Upsert seen devices, mark the others offline. Returns the new devices."""
        now = time.time()
        known = self.known_macs(network_id)
        new = [d for d in devices if d.mac not in known]
        with self.lock, self.conn:
            self.conn.execute("UPDATE devices SET online=0 WHERE network_id=?", (network_id,))
            for d in devices:
                self.conn.execute("""
                INSERT INTO devices (network_id, mac, ip, hostname, vendor, type, online, first_seen, last_seen)
                VALUES (?,?,?,?,?,?,1,?,?)
                ON CONFLICT(network_id, mac) DO UPDATE SET ip=excluded.ip,
                    hostname=excluded.hostname, vendor=excluded.vendor, type=excluded.type,
                    online=1, last_seen=excluded.last_seen""",
                    (network_id, d.mac, d.ip, d.hostname, d.vendor, d.type, now, now))
        return new

    def list_devices(self, network_id):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM devices WHERE network_id=? ORDER BY online DESC, ip", (network_id,)
            ).fetchall()
        return [Device(**{k: (bool(r[k]) if k == "online" else r[k])
                          for k in r.keys() if k != "network_id"}) for r in rows]

    def rename(self, network_id, mac, name):
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE devices SET name=? WHERE network_id=? AND mac=?", (name, network_id, mac))
        return cur.rowcount > 0

    def add_alert(self, network_id, mac, message):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO alerts (network_id, mac, message, created) VALUES (?,?,?,?)",
                (network_id, mac, message, time.time()))

    def list_alerts(self, only_new=True):
        sql = "SELECT * FROM alerts" + (" WHERE acknowledged=0" if only_new else "") + " ORDER BY id DESC LIMIT 100"
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql)]

    def ack_alerts(self):
        with self.lock, self.conn:
            self.conn.execute("UPDATE alerts SET acknowledged=1")
