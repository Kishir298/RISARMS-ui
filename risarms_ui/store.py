"""Device-local profiles, cached records and a durable explicit-sync queue."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def retry_delay(attempts):
    return min(300.0, 5.0 * (2 ** max(0, min(int(attempts), 6))))


class LocalStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.record_sync_lock = threading.Lock()
        self.file_sync_lock = threading.Lock()
        self.message_sync_lock = threading.Lock()
        self.db = sqlite3.connect(self.directory / "desktop.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT UNIQUE,salt BLOB,digest BLOB,server TEXT,remote_id TEXT);
        CREATE TABLE IF NOT EXISTS records(profile TEXT,service TEXT,key TEXT,value TEXT,base TEXT,pending INTEGER,
          PRIMARY KEY(profile,service,key));
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,profile TEXT,name TEXT,path TEXT,remote_id TEXT,pending INTEGER);
        CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY,profile TEXT,at REAL,source TEXT,prompt TEXT,output TEXT);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,profile TEXT NOT NULL,peer TEXT NOT NULL,
          direction TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,
          remote_id TEXT,error TEXT);
        CREATE INDEX IF NOT EXISTS messages_thread ON messages(profile,peer,created);
        ''')
        if "remote_role" not in {r[1] for r in self.db.execute("PRAGMA table_info(profiles)")}:
            self.db.execute("ALTER TABLE profiles ADD COLUMN remote_role TEXT DEFAULT 'local'")
        for table, columns in {
            "records": (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_attempt", "REAL NOT NULL DEFAULT 0"), ("error", "TEXT NOT NULL DEFAULT ''")),
            "files": (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_attempt", "REAL NOT NULL DEFAULT 0"), ("error", "TEXT NOT NULL DEFAULT ''")),
            "messages": (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_attempt", "REAL NOT NULL DEFAULT 0")),
        }.items():
            existing = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns:
                if name not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        self.db.commit()
        if os.name == "posix":
            self.directory.chmod(0o700)
            (self.directory / "desktop.sqlite3").chmod(0o600)

    @staticmethod
    def digest(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)

    def create(self, name, password):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", name):
            raise ValueError("Local name: 3–40 letters, numbers, dots, underscores or dashes.")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("Use a local passphrase of 12–256 characters.")
        salt, identity = secrets.token_bytes(16), uuid.uuid4().hex
        try:
            with self.lock, self.db:
                self.db.execute("INSERT INTO profiles(id,name,salt,digest,server,remote_id) VALUES(?,?,?,?,?,?)", (identity, name.lower(), salt, self.digest(password, salt), "", ""))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Local profile already exists.") from exc
        return identity

    def unlock(self, name, password):
        if not isinstance(name, str) or not isinstance(password, str) or len(password) > 256:
            raise PermissionError("Incorrect local name or passphrase.")
        with self.lock:
            row = self.db.execute("SELECT * FROM profiles WHERE name=?", (name.lower(),)).fetchone()
            actual = self.digest(password, row["salt"] if row else b"0" * 16)
            if row is None or not hmac.compare_digest(actual, row["digest"]):
                raise PermissionError("Incorrect local name or passphrase.")
            return row["id"]

    def bind(self, profile, server, remote_id, role="member"):
        with self.lock, self.db:
            row = self.db.execute("SELECT server,remote_id,remote_role FROM profiles WHERE id=?", (profile,)).fetchone()
            if row is None:
                raise PermissionError("Unlock a local profile first.")
            if row["remote_id"] and (row["server"] != server or row["remote_id"] != remote_id):
                raise PermissionError("This local profile belongs to another server account. Create a separate local profile.")
            self.db.execute("UPDATE profiles SET server=?,remote_id=?,remote_role=? WHERE id=?", (server, remote_id, role, profile))

    def binding(self, profile):
        with self.lock:
            row = self.db.execute("SELECT server,remote_id,remote_role FROM profiles WHERE id=?", (profile,)).fetchone()
            return dict(row) if row else None

    def profiles(self):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT id,name,server,remote_id,remote_role FROM profiles ORDER BY name")]

    def profile_row(self, profile):
        with self.lock:
            row = self.db.execute(
                "SELECT id,name,server,remote_id,remote_role FROM profiles WHERE id=?", (profile,)).fetchone()
            return dict(row) if row else None

    def save(self, profile, service, key, value):
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", service) or not isinstance(key, str) or not 1 <= len(key) <= 200 or not isinstance(value, dict):
            raise ValueError("Choose a service, a key of 1–200 characters and a JSON object.")
        raw = json.dumps(value)
        if len(raw.encode()) > 200000:
            raise ValueError("Record exceeds the local size limit.")
        with self.lock, self.db:
            self.db.execute('''INSERT INTO records(profile,service,key,value,base,pending)
              VALUES(?,?,?,?,?,1)
              ON CONFLICT(profile,service,key) DO UPDATE SET value=excluded.value,pending=1,
              attempts=0,next_attempt=0,error='' ''', (profile, service, key, raw, ""))

    def records(self, profile, service=None, pending=False):
        query, params = "SELECT * FROM records WHERE profile=?", [profile]
        if service:
            query += " AND service=?"; params.append(service)
        if pending:
            query += " AND pending=1"
        with self.lock:
            return [{**dict(row), "value": json.loads(row["value"])} for row in self.db.execute(query + " ORDER BY service,key", params)]

    def pending_records(self, profile, now=None):
        now = time.time() if now is None else now
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM records WHERE profile=? AND pending=1 AND next_attempt<=? ORDER BY service,key",
                (profile, now),
            )
            return [{**dict(row), "value": json.loads(row["value"])} for row in rows]

    def record_failed(self, profile, service, key, error):
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT attempts FROM records WHERE profile=? AND service=? AND key=?",
                (profile, service, key),
            ).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            self.db.execute(
                "UPDATE records SET attempts=?,next_attempt=?,error=? WHERE profile=? AND service=? AND key=?",
                (attempts, time.time() + retry_delay(attempts), str(error)[:500], profile, service, key),
            )

    def cache(self, profile, service, rows):
        with self.lock, self.db:
            for row in rows:
                self.db.execute('''INSERT INTO records(profile,service,key,value,base,pending)
                  VALUES(?,?,?,?,?,0)
                  ON CONFLICT(profile,service,key) DO UPDATE SET value=excluded.value,base=excluded.base
                  WHERE records.pending=0''', (profile, service, row["key"], json.dumps(row["value"]), revision(row["value"])))

    def acknowledged(self, profile, item, saved):
        # A local edit made during the network request must remain queued.
        with self.lock, self.db:
            current = self.db.execute("SELECT value FROM records WHERE profile=? AND service=? AND key=?", (profile, item["service"], item["key"])).fetchone()
            if current:
                clean = json.loads(current["value"]) == item["value"]
                self.db.execute("UPDATE records SET base=?,pending=?,attempts=0,next_attempt=0,error='' WHERE profile=? AND service=? AND key=?",
                    (revision(saved["value"]), 0 if clean else 1, profile, item["service"], item["key"]))

    def history(self, profile):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM history WHERE profile=? ORDER BY at DESC LIMIT 50", (profile,))]

    def log(self, profile, source, prompt, output):
        with self.lock, self.db:
            self.db.execute("INSERT INTO history VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, profile, time.time(), source, prompt, output[-200000:]))

    def close(self):
        with self.lock: self.db.close()

    def add_file(self, profile, name, data, remote_id=""):
        if len(data)>8*1024*1024: raise ValueError("Files must be at most 8 MiB.")
        identity=uuid.uuid4().hex
        home=self.directory/"profiles"/profile/"files";home.mkdir(parents=True,exist_ok=True,mode=0o700)
        if sum(Path(r["path"]).stat().st_size for r in self.files(profile) if Path(r["path"]).is_file())+len(data)>250*1024*1024:
            raise ValueError("Local file cache quota is 250 MiB for this profile.")
        path=home/identity;path.write_bytes(data)
        if os.name=="posix":path.chmod(0o600)
        with self.lock,self.db:
            self.db.execute("INSERT INTO files(id,profile,name,path,remote_id,pending) VALUES(?,?,?,?,?,?)",(identity,profile,Path(name).name,str(path),remote_id,0 if remote_id else 1))
        return identity

    def files(self, profile):
        with self.lock:return [dict(r) for r in self.db.execute("SELECT * FROM files WHERE profile=? ORDER BY name",(profile,))]

    def pending_files(self, profile, now=None):
        now = time.time() if now is None else now
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM files WHERE profile=? AND pending=1 AND next_attempt<=? ORDER BY name",
                (profile, now),
            )]

    def file_failed(self, profile, identity, error):
        with self.lock, self.db:
            row = self.db.execute("SELECT attempts FROM files WHERE profile=? AND id=?", (profile, identity)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            self.db.execute(
                "UPDATE files SET attempts=?,next_attempt=?,error=? WHERE profile=? AND id=?",
                (attempts, time.time() + retry_delay(attempts), str(error)[:500], profile, identity),
            )

    def file_ack(self, profile, identity, remote_id):
        with self.lock,self.db:
            self.db.execute("UPDATE files SET pending=0,remote_id=?,attempts=0,next_attempt=0,error='' WHERE profile=? AND id=?",(remote_id,profile,identity))

    # ------------------------------------------------------------- messaging
    def queue_message(self, profile, peer, body):
        """Queue an outgoing message durably. Queued is not delivered."""
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Write a message before sending.")
        if len(body.encode()) > 16000:
            raise ValueError("Messages are limited to 16000 bytes.")
        if not isinstance(peer, str) or not peer.strip():
            raise ValueError("Choose a recipient.")
        identity = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO messages(id,profile,peer,direction,body,created,status,remote_id,error) VALUES(?,?,?,?,?,?,?,?,?)",
                (identity, profile, peer.strip(), "out", body, time.time(), "queued", "", ""),
            )
        return identity

    def messages(self, profile, peer=None):
        query, params = "SELECT * FROM messages WHERE profile=?", [profile]
        if peer:
            query += " AND peer=?"
            params.append(peer)
        with self.lock:
            return [dict(row) for row in self.db.execute(query + " ORDER BY created,id", params)]

    def threads(self, profile):
        with self.lock:
            rows = self.db.execute(
                """SELECT peer, MAX(created) AS last_at, COUNT(*) AS total
                   FROM messages WHERE profile=? GROUP BY peer ORDER BY last_at DESC""",
                (profile,),
            ).fetchall()
            results = []
            for row in rows:
                last = self.db.execute(
                    "SELECT body FROM messages WHERE profile=? AND peer=? ORDER BY created DESC LIMIT 1",
                    (profile, row["peer"]),
                ).fetchone()
                unread = self.db.execute(
                    "SELECT COUNT(*) FROM messages WHERE profile=? AND peer=? AND direction='in' AND status!='read'",
                    (profile, row["peer"]),
                ).fetchone()[0]
                results.append({"peer": row["peer"], "last_at": row["last_at"],
                                "last_body": last["body"] if last else "", "unread": unread})
            return results

    def pending_message_count(self, profile):
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM messages WHERE profile=? AND direction='out' AND status='queued'",
                (profile,),
            ).fetchone()[0]

    def pending_messages(self, profile, now=None):
        now = time.time() if now is None else now
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT * FROM messages WHERE profile=? AND direction='out' AND status='queued' AND next_attempt<=? ORDER BY created,id",
                (profile, now),
            )]

    def message_failed(self, profile, identity, error):
        with self.lock, self.db:
            row = self.db.execute("SELECT attempts FROM messages WHERE profile=? AND id=?", (profile, identity)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            self.db.execute(
                "UPDATE messages SET attempts=?,next_attempt=?,error=? WHERE profile=? AND id=?",
                (attempts, time.time() + retry_delay(attempts), str(error)[:500], profile, identity),
            )

    def flush_messages(self, profile, remote):
        """Send queued messages once; failures stay queued for the next cycle.

        The local id doubles as the idempotency key so a retry after a lost
        response cannot duplicate a message on the server. A queued message is
        never reported as delivered.
        """
        sent = 0
        with self.message_sync_lock:
            for row in self.pending_messages(profile):
                try:
                    result = remote.request(
                        "POST", "/api/messages",
                        {"recipient": row["peer"], "body": row["body"], "client_id": row["id"]},
                        idempotency_key=row["id"],
                    )
                except Exception as exc:
                    if getattr(exc, "status", 0) in (400, 403, 404):
                        with self.lock, self.db:
                            self.db.execute(
                                "UPDATE messages SET status='failed',error=? WHERE profile=? AND id=?",
                                (str(exc)[:500], profile, row["id"]),
                            )
                    else:
                        self.message_failed(profile, row["id"], exc)
                    continue
                with self.lock, self.db:
                    self.db.execute(
                        "UPDATE messages SET status=?,remote_id=?,error='',attempts=0,next_attempt=0 WHERE profile=? AND id=?",
                        (result.get("status", "sent"), result.get("id", ""), profile, row["id"]),
                    )
                sent += 1
        return sent

    def record_incoming(self, profile, peer, rows):
        """Cache server messages for one peer without duplicating them."""
        added = 0
        with self.lock, self.db:
            for row in rows:
                remote_id = str(row.get("id", ""))
                if not remote_id:
                    continue
                exists = self.db.execute(
                    "SELECT 1 FROM messages WHERE profile=? AND remote_id=?", (profile, remote_id)
                ).fetchone()
                if exists:
                    continue
                self.db.execute(
                    "INSERT INTO messages(id,profile,peer,direction,body,created,status,remote_id,error) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("srv-" + remote_id, profile, peer, "in", row.get("body", ""),
                     float(row.get("created", time.time())), "read" if row.get("read") else "delivered",
                     remote_id, ""),
                )
                added += 1
        return added

    def mark_thread_read(self, profile, peer):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE messages SET status='read' WHERE profile=? AND peer=? AND direction='in' AND status!='read'",
                (profile, peer),
            )
