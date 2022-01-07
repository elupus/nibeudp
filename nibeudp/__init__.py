from __future__ import annotations

import logging
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from anyio import create_udp_socket

LOG = logging.getLogger(__name__)

DEFAULT_PORT_RX = 9999
DEFAULT_HOST_RX = "0.0.0.0"


START_MASTER = 0x5C
START_SLAVE = 0xC0


@dataclass
class Command:
    command: int


@dataclass
class CommandUnknown(Command):
    data: bytes


@dataclass
class ResponseWrite(Command):
    command: ClassVar[int] = 0x6C
    register: int

    def to_bytes(self) -> bytes:
        return self.register.to_bytes(2, "little")

    @classmethod
    def from_bytes(cls, payload: bytes):
        if len(payload) == 0:
            return None
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


@dataclass
class MasterMessage(Message):
    start: ClassVar[int] = 0x5C
    address: int


@dataclass
class SlaveMessage(Message):
    start: ClassVar[int] = 0xC0


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

def calculate_checksum(data: bytes):
    result = 0
    for value in data:
        result ^= value
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
        data_checksum = data[5+data_len]
        checksum = calculate_checksum(data[2:5+data_len])

    elif data[0] == SlaveMessage.start:
        data = bytes(unescape(data, SlaveMessage.start))

        data_len = data[2]
        if len(data) < data_len + 4:
            raise ParseError(f"Invalid packet length: {data}")
        data_payload = data[1 : 1 + data_len]
        data_command = data[1]
        data_message = SlaveMessage()
        data_checksum = data[1+data_len]
        checksum = calculate_checksum(data[0:1+data_len])
    else:
        raise ParseError(f"Invalid startcode {data[0].hex(' ')}")

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


async def server(listen_port=DEFAULT_PORT_RX):
    async with await create_udp_socket(
        family=socket.AF_INET, local_port=listen_port, local_host=DEFAULT_HOST_RX
    ) as udp:
        async for packet, (host, port) in udp:
            try:
                message = parse(packet)
                LOG.debug(
                    "RX: %s from %s:%s -> %s", packet.hex(" "), host, port, message
                )
            except ParseError as exc:
                LOG.error(
                    "RX: %s from %s:%s -> %s", packet.hex(" "), host, port, str(exc)
                )
