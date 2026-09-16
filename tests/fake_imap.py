"""A minimal IMAP4rev1 server: exactly the commands herald-daemon issues.

Not a mail server. It speaks LOGIN, SELECT, UID SEARCH, UID FETCH, IDLE and
NOOP well enough to drive the watcher through a full cycle — connect, take a
baseline, block in IDLE, wake on an EXISTS, fetch the new UID — which is the
part of the daemon that no unit test can reach.
"""

import select
import socket
import threading

CRLF = b"\r\n"


def unquote(token):
    """imaplib quotes some arguments (the password) and not others."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


class FakeIMAP:
    def __init__(self, messages=(), user="watcher@example.com", password="hunter2"):
        self.messages = list(messages)          # raw RFC822 bytes, index+1 == UID
        self.user = user
        self.password = password
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._pending = threading.Event()       # a message arrived during IDLE
        # Set while a client is parked in IDLE. Tests wait on this before
        # delivering, so "arrives during IDLE" is a fact rather than a race
        # against the watcher's startup search.
        self.idling = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def deliver(self, raw):
        self.messages.append(raw)
        self._pending.set()

    def wait_until_idle(self, timeout=10):
        return self.idling.wait(timeout)

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    # -- connection

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn):
        f = conn.makefile("rb")
        selected = False

        def send(line):
            conn.sendall(line.encode() if isinstance(line, str) else line)
            conn.sendall(CRLF)

        send("* OK [CAPABILITY IMAP4rev1 IDLE] fake imap ready")
        try:
            while not self._stop.is_set():
                raw = f.readline()
                if not raw:
                    return
                parts = [unquote(p) for p in raw.decode("utf-8", "replace").strip().split(" ")]
                tag, cmd, args = parts[0], parts[1].upper() if len(parts) > 1 else "", parts[2:]

                if cmd == "CAPABILITY":
                    send("* CAPABILITY IMAP4rev1 IDLE")
                    send(f"{tag} OK CAPABILITY completed")

                elif cmd == "LOGIN":
                    ok = args[:2] == [self.user, self.password]
                    send(f"{tag} OK LOGIN completed" if ok else f"{tag} NO bad credentials")

                elif cmd == "SELECT":
                    selected = True
                    send(f"* {len(self.messages)} EXISTS")
                    send("* 0 RECENT")
                    send("* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
                    send("* OK [UIDVALIDITY 1] UIDs valid")
                    send(f"* OK [UIDNEXT {len(self.messages) + 1}] predicted next UID")
                    send(f"{tag} OK [READ-WRITE] SELECT completed")

                elif cmd == "UID" and args and args[0].upper() == "SEARCH":
                    send("* SEARCH " + " ".join(str(u) for u in self._search(args[1:])))
                    send(f"{tag} OK UID SEARCH completed")

                elif cmd == "UID" and args and args[0].upper() == "FETCH":
                    uid = int(args[1])
                    if 1 <= uid <= len(self.messages):
                        body = self.messages[uid - 1]
                        conn.sendall(f"* {uid} FETCH (UID {uid} BODY[] {{{len(body)}}}".encode() + CRLF)
                        conn.sendall(body)
                        conn.sendall(b")" + CRLF)
                    send(f"{tag} OK UID FETCH completed")

                elif cmd == "IDLE":
                    send("+ idling")
                    self.idling.set()
                    try:
                        self._idle(conn, f, tag, send)
                    finally:
                        self.idling.clear()

                elif cmd == "NOOP":
                    send(f"{tag} OK NOOP completed")

                elif cmd == "CLOSE":
                    selected = False
                    send(f"{tag} OK CLOSE completed")

                elif cmd == "LOGOUT":
                    send("* BYE logging out")
                    send(f"{tag} OK LOGOUT completed")
                    return

                else:
                    send(f"{tag} BAD unsupported: {cmd}")
        except (OSError, ValueError, IndexError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _search(self, args):
        """ALL, or the `UID n:*` range the watcher uses to ask what is new."""
        uids = list(range(1, len(self.messages) + 1))
        if args and args[0].upper() == "UID" and len(args) > 1 and ":" in args[1]:
            low = int(args[1].split(":")[0])
            found = [u for u in uids if u >= low]
            # RFC 3501: `n:*` always matches the highest UID, even below n.
            return found or uids[-1:]
        return uids

    def _idle(self, conn, f, tag, send):
        # select() on the raw socket rather than a socket timeout: a timeout
        # raised out of a buffered makefile() reader leaves that reader in an
        # undefined state, and the next readline() returns EOF on a socket
        # that is still perfectly open.
        while not self._stop.is_set():
            if self._pending.is_set():
                self._pending.clear()
                send(f"* {len(self.messages)} EXISTS")
            ready, _, _ = select.select([conn], [], [], 0.05)
            if not ready:
                continue
            line = f.readline()
            if not line:
                return
            if line.strip().upper() == b"DONE":
                send(f"{tag} OK IDLE terminated")
                return
