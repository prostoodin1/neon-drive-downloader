from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


SERVER_NAME = "NeonDrive.v13"
RequestHandler = Callable[[dict], dict]


def send_request(payload: dict, timeout_ms: int = 2500) -> dict:
    """Send a JSON command to the single running Neon Drive instance."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(timeout_ms):
        return {"ok": False, "error": "Neon Drive не запущен"}
    socket.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    if not socket.waitForBytesWritten(timeout_ms):
        return {"ok": False, "error": "Не удалось отправить команду Neon Drive"}
    if not socket.waitForReadyRead(timeout_ms):
        return {"ok": False, "error": "Neon Drive не ответил вовремя"}
    raw = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Neon Drive вернул некорректный ответ"}
    return response if isinstance(response, dict) else {"ok": False, "error": "Некорректный ответ"}


class InstanceServer(QObject):
    """Own the local IPC endpoint used for single-instance activation and agent CLI."""

    def __init__(self, handler: RequestHandler, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.handler = handler
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self.server.newConnection.connect(self._accept_connections)

    def listen(self) -> bool:
        if self.server.listen(SERVER_NAME):
            return True
        # Windows named pipes disappear with their owner. Failing closed here avoids
        # a race where a slow first instance could otherwise be replaced by a second.
        return False

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda active=socket: self._read_request(active))
            socket.disconnected.connect(lambda active=socket: self._buffers.pop(active, None))
            if socket.bytesAvailable():
                self._read_request(socket)

    def _read_request(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.setdefault(socket, bytearray())
        buffer.extend(bytes(socket.readAll()))
        if b"\n" not in buffer:
            return
        raw_bytes, _separator, _remainder = bytes(buffer).partition(b"\n")
        self._buffers.pop(socket, None)
        raw = raw_bytes.decode("utf-8", errors="replace").strip()
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request is not an object")
            response = self.handler(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        socket.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
