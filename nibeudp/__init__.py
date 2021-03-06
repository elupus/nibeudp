from __future__ import annotations
from abc import abstractmethod

import logging
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar
from contextlib import AsyncExitStack

from anyio import create_udp_socket, create_connected_udp_socket
from anyio.abc import UDPSocket

LOG = logging.getLogger(__name__)

DEFAULT_PORT_RX = 9999
DEFAULT_PORT_READ = 10000
DEFAULT_PORT_WRITE = 10001
DEFAULT_HOST_RX = "0.0.0.0"


@dataclass
class Command:
    command: int

    def to_bytes(self) -> bytes:
        return bytes()


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
class Message:
    start: int

    def to_bytes(self, command: Command):
        return bytes()


@dataclass
class MasterMessage(Message):
    start: ClassVar[int] = 0x5C
    address: int

    def to_bytes(self, command: Command):
        data = bytearray()
        data.append(self.start)
        data.append(0x00)
        data.append(self.address)
        data.append(command.command)
        payload = command.to_bytes()
        data.append(len(payload))
        data.extend(payload)
        data.append(calculate_checksum(data[2:]), self.start)
        return bytes(data)


@dataclass
class SlaveMessage(Message):
    start: ClassVar[int] = 0xC0

    def to_bytes(self, command: Command):
        data = bytearray()
        data.append(self.start)
        data.append(command.command)
        payload = command.to_bytes()
        data.append(len(payload))
        data.extend(payload)
        data.append(calculate_checksum(data, self.start))
        return bytes(data)


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

    if data[0] == MasterMessage.start:
        data = bytes(unescape(data, MasterMessage.start))

        data_len = data[4]
        if len(data) < data_len + 6:
            raise ParseError(f"Invalid packet length: {data}")
        data_payload = data[5 : 5 + data_len]
        data_command = data[3]
        data_message = MasterMessage(data[2])
        data_checksum = data[5 + data_len]
        checksum = calculate_checksum(data[2 : 5 + data_len], data_message.start)

    elif data[0] == SlaveMessage.start:
        data = bytes(unescape(data, SlaveMessage.start))

        data_len = data[2]
        if len(data) < data_len + 4:
            raise ParseError(f"Invalid packet length: {data}")
        data_payload = data[1 : 1 + data_len]
        data_command = data[1]
        data_message = SlaveMessage()
        data_checksum = data[1 + data_len]
        checksum = calculate_checksum(data[0 : 1 + data_len], data_message.start)
    else:
        raise ParseError(f"Invalid startcode {hex(data[0])}")

    if checksum != data_checksum:
        raise ParseError(f"Invalid checksum {checksum} expected {data_checksum}")

    return parse_payload(data_command, data_payload), data_message


def parse_payload(command: int, payload: bytes):
    if command == ResponseRead.command:
        return ResponseRead.from_bytes(payload)

    if command == ResponseWrite.command:
        return ResponseWrite.from_bytes(payload)

    if command == ResponseData.command:
        return ResponseData.from_bytes(payload)

    if command == ResponseRmu.command:
        return ResponseRmu(payload)

    if command == RequestRead.command and payload:
        return RequestRead.from_bytes(payload)

    if command == RequestReadNull.command:
        return RequestReadNull()

    if command == RequestWrite.command and payload:
        return RequestWrite.from_bytes(payload)

    if command == RequestWriteNull.command:
        return RequestWriteNull()

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

        data = SlaveMessage().to_bytes(command)
        if isinstance(command, RequestRead):
            port = self._port_read
        elif isinstance(command, RequestWrite):
            port = self._port_write

        await self._udp.sendto(data, self._server_host, port)

    async def __aiter__(self):
        async for packet, (host, port) in self._udp:
            try:
                if port not in (self._port_read, self._port_write):
                    continue

                self._server_host = host
                message = parse(packet)
                LOG.debug(
                    "RX: %s from %s:%s -> %s", packet.hex(" "), host, port, message
                )
                yield message
            except ParseError as exc:
                LOG.error(
                    "RX: %s from %s:%s -> %s", packet.hex(" "), host, port, str(exc)
                )
