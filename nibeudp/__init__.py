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
    data: bytes


@dataclass
class ResponseRead(Command):
    command: ClassVar[int] = 0x6A
    data: bytes


@dataclass
class ResponseData(Command):
    command: ClassVar[int] = 0x68
    data: bytes


@dataclass
class ResponseRmu(Command):
    command: ClassVar[int] = 0x62
    data: bytes


@dataclass
class RequestRead(Command):
    command: ClassVar[int] = 0x69
    data: bytes


@dataclass
class RequestWrite(Command):
    command: ClassVar[int] = 0x6B
    data: bytes


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

    elif data[0] == SlaveMessage.start:
        data = bytes(unescape(data, SlaveMessage.start))

        data_len = data[2]
        if len(data) < data_len + 4:
            raise ParseError(f"Invalid packet length: {data}")
        data_payload = data[1 : 1 + data_len]
        data_command = data[1]
        data_message = SlaveMessage()
    else:
        raise ParseError(f"Invalid startcode {data[0].hex(' ')}")

    if data_command == ResponseRead.command:
        return ResponseRead(data_payload), data_message

    if data_command == ResponseWrite.command:
        return ResponseWrite(data_payload), data_message

    if data_command == ResponseData.command:
        return ResponseData(data_payload), data_message

    if data_command == ResponseRmu.command:
        return ResponseRmu(data_payload), data_message

    if data_command == RequestRead.command:
        return RequestRead(data_payload), data_message

    if data_command == RequestWrite.command:
        return RequestWrite(data_payload), data_message

    return CommandUnknown(data_command, data_payload), data_message


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
