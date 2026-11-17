"""Anonymous Twitch chat logger.

Writes one JSON line per message: {"t": seconds since recording start, "u": user, "m": text}.
Chat velocity is the single strongest "something just happened" signal on a live stream.
"""
from __future__ import annotations

import json
import random
import socket
import ssl
import threading
import time
from pathlib import Path

HOST, PORT = "irc.chat.twitch.tv", 6697


def _parse_privmsg(line: str) -> tuple[str, str] | None:
    # :user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
    if " PRIVMSG #" not in line:
        return None
    try:
        prefix, _, rest = line.partition(" PRIVMSG #")
        user = prefix.lstrip(":").split("!", 1)[0]
        msg = rest.split(" :", 1)[1]
        return user, msg
    except IndexError:
        return None


def log_chat(channel: str, out_path: Path, t0: float, stop: threading.Event) -> None:
    """Block until `stop` is set, reconnecting on errors."""
    channel = channel.lower().lstrip("#")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as out:
        while not stop.is_set():
            try:
                raw = socket.create_connection((HOST, PORT), timeout=30)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
                sock.settimeout(2)
                nick = f"justinfan{random.randint(10000, 99999)}"
                sock.sendall(f"NICK {nick}\r\nJOIN #{channel}\r\n".encode())
                buf = ""
                last_rx = time.time()
                while not stop.is_set():
                    try:
                        data = sock.recv(8192)
                    except socket.timeout:
                        if time.time() - last_rx > 360:
                            raise ConnectionError("chat idle timeout")
                        continue
                    if not data:
                        raise ConnectionError("chat socket closed")
                    last_rx = time.time()
                    buf += data.decode("utf-8", errors="ignore")
                    *lines, buf = buf.split("\r\n")
                    for line in lines:
                        if line.startswith("PING"):
                            sock.sendall(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                            continue
                        parsed = _parse_privmsg(line)
                        if parsed:
                            out.write(json.dumps({"t": round(time.time() - t0, 2), "u": parsed[0], "m": parsed[1]}) + "\n")
                    out.flush()
                sock.close()
            except Exception:
                if stop.wait(5):
                    break
