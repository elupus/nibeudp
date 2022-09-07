from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import ClassVar, Generic, TypeVar

from anyio import Event, create_udp_socket
from anyio.abc import UDPSocket

LOG = logging.getLogger(__name__)

DEFAULT_PORT_RX = 9999
DEFAULT_PORT_READ = 9999
DEFAULT_PORT_WRITE = 10000
DEFAULT_HOST_RX = "0.0.0.0"


@dataclass
class Command:
    command: int

    def to_bytes(self) -> bytes:
        return b""


@dataclass
class CommandUnknown(Command):
    data: bytes

    def to_bytes(self) -> bytes:
        return self.data


@dataclass
class ResponseWrite(Command):
    command: ClassVar[int] = 0x6C
    register: int

    def to_bytes(self) -> bytes:
        return self.register.to_bytes(2, "little")

    @classmethod
    def from_bytes(cls, payload: bytes):
        if len(payload) != 2:
            raise ParseError(f"Length is not 2: {len(payload)}")
        return cls(int.from_bytes(payload, "little"))


@dataclass
class ResponseRead(Command):
    command: ClassVar[int] = 0x6A
    register: int
    value: int

    def to_bytes(self) -> bytes:
        return self.register.to_bytes(2, "little") + self.value.to_bytes(4, "little")

    @classmethod
    def from_bytes(cls, payload: bytes):
        if len(payload) != 6:
            raise ParseError(f"Length is not 6: {len(payload)}")

        return cls(
            int.from_bytes(payload[0:2], "little"),
            int.from_bytes(payload[2:6], "little"),
        )


@dataclass
class ResponseData(Command):
    command: ClassVar[int] = 0x68
    parameters: dict[int, int]

    def to_bytes(self) -> bytes:
        payload = b"".join(
            register.to_bytes(2, "little") + data.to_bytes(2, "little")
            for register, data in self.parameters.items()
        )
        return payload

    @classmethod
    def from_bytes(cls, payload: bytes):
        parameters = {}
        if len(payload) % 4:
            raise ParseError(f"Length is not a multiple of 4: {len(payload)}")
        for idx in range(0, len(payload), 4):
            register = int.from_bytes(payload[idx : idx + 2], "little")
            data = int.from_bytes(payload[idx + 2 : idx + 4], "little")
            if register == 0xFFFF:
                continue
            parameters[register] = data
        return cls(parameters)


@dataclass
class ResponseRmu(Command):
    command: ClassVar[int] = 0x62
    data: bytes

    @classmethod
    def from_bytes(cls, payload: bytes):
        return cls(payload)


@dataclass
class RequestReadNull(Command):
    command: ClassVar[int] = 0x69


@dataclass
class RequestRead(Command):
    command: ClassVar[int] = 0x69
    register: int

    def to_bytes(self) -> bytes:
        return self.register.to_bytes(2, "little")

    @classmethod
    def from_bytes(cls, payload: bytes):
        if len(payload) != 2:
            raise ParseError(f"Length is not 2: {len(payload)}")
        return cls(int.from_bytes(payload, "little"))


@dataclass
class RequestWriteNull(Command):
    command: ClassVar[int] = 0x6B


@dataclass
class RequestWrite(Command):
    command: ClassVar[int] = 0x6B
    register: int
    value: int

    def to_bytes(self) -> bytes:
        return self.register.to_bytes(2, "little") + self.value.to_bytes(4, "little")

    @classmethod
    def from_bytes(cls, payload: bytes):
        if len(payload) != 6:
            raise ParseError(f"Length is not 6: {len(payload)}")

        return cls(
            int.from_bytes(payload[0:2], "little"),
            int.from_bytes(payload[2:6], "little"),
        )


@dataclass
class ResponseProduct(Command):
    command: ClassVar[int] = 0x6D
    unknown: bytes
    product: str

    @classmethod
    def from_bytes(cls, payload: bytes):
        return cls(payload[0:3], payload[3:].decode("ascii"))


@dataclass
class Message:
    start: int

    def to_bytes(self, command: Command) -> bytes:
        return bytes([self.start])


@dataclass
class MessageMaster(Message):
    start: ClassVar[int] = 0x5C
    address: int
    command: Command

    def to_bytes(self):
        data = bytearray()
        data.append(self.start)
        data.append(0x00)
        data.append(self.address)
        data.append(self.command.command)
        payload = self.command.to_bytes()
        data.append(len(payload))
        data.extend(payload)
        data.append(calculate_checksum(data[2:]), self.start)
        return bytes(data)


@dataclass
class MessageSlave(Message):
    start: ClassVar[int] = 0xC0
    command: Command

    def to_bytes(self):
        data = bytearray()
        data.append(self.start)
        data.append(self.command.command)
        payload = self.command.to_bytes()
        data.append(len(payload))
        data.extend(payload)
        data.append(calculate_checksum(data, self.start))
        return bytes(data)


@dataclass
class MessageAck(Message):
    start: ClassVar[int] = 0x06


@dataclass
class MessageNak(Message):
    start: ClassVar[int] = 0x15


@dataclass
class MessageUnknown(Message):
    data: bytes


class ParseError(Exception):
    pass


def unescape(data: Iterable[int], key: int):
    it = iter(data)
    try:
        yield next(it)
    except StopIteration:
        return

    while True:
        try:
            val = next(it)
            if val == key:
                val = next(it)
            yield val
        except StopIteration:
            return


def escape(data: Iterable[int], key: int):
    it = iter(data)
    try:
        yield next(it)
    except StopIteration:
        return

    while True:
        try:
            val = next(it)
            if val == key:
                yield key
            yield val
        except StopIteration:
            return


def calculate_checksum(data: Iterable[int], key: int):
    result = 0
    for value in data:
        result ^= value

    if result == key:
        result = ((key << 4) | (key >> 4)) & 0xFF

    return result


def parse(data: bytes):
    if not data:
        raise ParseError("Empty packet")

    if data[0] == MessageMaster.start:
        data = bytes(unescape(data, MessageMaster.start))

        data_len = data[4]
        if len(data) < data_len + 6:
            raise ParseError(f"Invalid packet length: {data}")
        data_payload = data[5 : 5 + data_len]
        data_command = data[3]
        data_checksum = data[5 + data_len]
        checksum = calculate_checksum(data[2 : 5 + data_len], data[0])
        if checksum != data_checksum:
            raise ParseError(f"Invalid checksum {checksum} expected {data_checksum}")
        command = parse_payload(data_command, data_payload)
        return MessageMaster(data[2], command)

    elif data[0] == MessageSlave.start:
        data = bytes(unescape(data, MessageSlave.start))

        data_len = data[2]
        if len(data) < data_len + 4:
            raise ParseError(f"Invalid packet length: {data}")
        data_payload = data[3 : 3 + data_len]
        data_command = data[1]
        data_checksum = data[3 + data_len]
        checksum = calculate_checksum(data[0 : 3 + data_len], data[0])
        if checksum != data_checksum:
            raise ParseError(f"Invalid checksum {checksum} expected {data_checksum}")
        command = parse_payload(data_command, data_payload)
        return MessageSlave(command)

    elif data[0] == MessageAck.start:
        return MessageAck()

    elif data[0] == MessageNak.start:
        return MessageNak()

    else:
        return MessageUnknown(data[0], data[1:])


def parse_payload(command: int, payload: bytes):
    if command == ResponseRead.command:
        return ResponseRead.from_bytes(payload)

    if command == ResponseWrite.command:
        return ResponseWrite.from_bytes(payload)

    if command == ResponseData.command:
        return ResponseData.from_bytes(payload)

    if command == ResponseRmu.command:
        return ResponseRmu.from_bytes(payload)

    if command == RequestRead.command and payload:
        return RequestRead.from_bytes(payload)

    if command == RequestReadNull.command:
        return RequestReadNull()

    if command == RequestWrite.command and payload:
        return RequestWrite.from_bytes(payload)

    if command == RequestWriteNull.command:
        return RequestWriteNull()

    if command == ResponseProduct.command:
        return ResponseProduct.from_bytes(payload)

    return CommandUnknown(command, payload)


class Connection(AsyncExitStack):
    _udp: UDPSocket

    def __init__(
        self,
        server_host,
        port_listen: int = DEFAULT_PORT_RX,
        port_read: int = DEFAULT_PORT_READ,
        port_write: int = DEFAULT_PORT_WRITE,
    ):
        super().__init__()
        self._port_listen = port_listen
        self._port_read = port_read
        self._port_write = port_write
        self._server_host = server_host

    async def __aenter__(self):
        self._udp = await self.enter_async_context(
            await create_udp_socket(
                family=socket.AF_INET,
                local_port=self._port_listen,
                local_host=DEFAULT_HOST_RX,
            )
        )
        await super().__aenter__()
        return self

    async def send(self, command: RequestRead | RequestWrite):

        data = MessageSlave(command).to_bytes()
        if isinstance(command, RequestRead):
            port = self._port_read
        elif isinstance(command, RequestWrite):
            port = self._port_write

        await self._udp.sendto(data, self._server_host, port)

    async def __aiter__(self):
        async for packet, (host, port) in self._udp:
            try:
                if self._server_host != host:
                    LOG.warning("Data from unexpected host %s", host)

                message = parse(packet)
                LOG.debug(
                    "RX: %s from %s:%s -> %s", packet.hex(" "), host, port, message
                )
                yield message
            except ParseError as exc:
                LOG.error(
                    "RX: %s from %s:%s -> %s", packet.hex(" "), host, port, str(exc)
                )


M = TypeVar("M", bound=Command)


class ResponseFuture(Generic[M]):
    response: M

    def __init__(self) -> None:
        self.event = Event()

    def set(self, response: M):
        self.response = response
        self.event.set()

    async def get(self):
        await self.event.wait()
        return self.response


class Controller(AbstractAsyncContextManager):
    def __init__(self, connection: Connection) -> None:
        """Initialize controller."""
        self._connection = connection
        self._listeners: set[Callable[[Command], None]] = set()

    @contextmanager
    def listen(self, listener: Callable[[Command], None]):
        self._listeners.add(listener)
        try:
            yield
        finally:
            self._listeners.remove(listener)

    async def read(self, register: int) -> int:
        command = RequestRead(register)
        response = ResponseFuture[ResponseRead]()

        def listener(reply: Command):
            if not isinstance(reply, ResponseRead):
                return
            if reply.register != register:
                return
            response.set(reply)

        with self.listen(listener):
            await self._connection.send(command)
            return (await response.get()).value

    async def write(self, register: int, value: int) -> int:
        command = RequestWrite(register, value)
        response = ResponseFuture[ResponseWrite]()

        def listener(reply: Command):
            if not isinstance(reply, ResponseWrite):
                return
            if reply.register != register:
                return
            response.set(reply)

        with self.listen(listener):
            await self._connection.send(command)
            await response.get()
            return

    async def __aenter__(self) -> Controller:
        return self

    async def __aexit__(
        self,
        __exc_type: type[BaseException] | None,
        __exc_value: BaseException | None,
        __traceback: TracebackType | None,
    ) -> bool | None:
        return None

    async def __aiter__(self):
        async for command in self._connection:
            for listener in self._listeners:
                listener(command)

            yield command
