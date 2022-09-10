from __future__ import annotations

import pytest
from anyio import create_task_group, fail_after, run, sleep

from nibeudp import (
    Command,
    CommandUnknown,
    Message,
    MessageMaster,
    MessageSlave,
    RequestRead,
    RequestReadNull,
    RequestWrite,
    RequestWriteNull,
    ResponseData,
    ResponseFuture,
    ResponseProduct,
    ResponseRead,
    ResponseRmu,
    parse,
)


@pytest.mark.parametrize(
    "data,result",
    [
        pytest.param(
            "5c 00 20 6d 0b" + b"\x01$\xe3F1155-16".hex(" ") + "ec",
            (MessageMaster(0x20, ResponseProduct(1, 9443, "F1155-16"))),
            id="Product Data",
        ),
        pytest.param(
            "5c 00 02 a0 03 00 5c 5c a1",
            (MessageMaster(0x02, CommandUnknown(0xA0, bytes.fromhex("00 5c")))),
        ),
        pytest.param(
            "5c 00 20 6b 00 4b a8",
            (MessageMaster(0x20, RequestWriteNull())),
            id="Buggy server responding with extra byte",
        ),
        pytest.param(
            "5c 00 20 6b 00 4b",
            (MessageMaster(0x20, RequestWriteNull())),
            id="Frame from MODBUS40",
        ),
        pytest.param(
            "5C 00 19 60 00 79",
            (MessageMaster(0x19, CommandUnknown(0x60, b""))),
            id="Frame from RMU40",
        ),
        pytest.param(
            "5C 00 19 62 18 00 80 00 80 00 00 00 00 00 80 00 00 00 00 00 0B 0B 00 00 00 01 00 00 05 E7",
            (
                MessageMaster(
                    0x19,
                    ResponseRmu(
                        bytes.fromhex(
                            "00 80 00 80 00 00 00 00 00 80 00 00 00 00 00 0B 0B 00 00 00 01 00 00 05"
                        )
                    ),
                )
            ),
            id="Frame from RMU40",
        ),
        pytest.param(
            "5C 00 20 68 50 01 A8 1F 01 00 A8 64 00 FD A7 D0 03 44 9C 1E 00 4F 9C A0 00 50 9C 78 00 51 9C 03 01 52 9C 1B 01 87 9C 14 01 4E 9C C6 01 47 9C 01 01 15 B9 B0 FF 3A B9 4B 00 C9 AF 00 00 48 9C 0D 01 4C 9C E7 00 4B 9C 00 00 FF FF 00 00 FF FF 00 00 FF FF 00 00 45",
            (
                MessageMaster(
                    0x20,
                    ResponseData(
                        {
                            43009: 287,
                            43008: 100,
                            43005: 976,
                            40004: 30,
                            40015: 160,
                            40016: 120,
                            40017: 259,
                            40018: 283,
                            40071: 276,
                            40014: 454,
                            40007: 257,
                            47381: 65456,
                            47418: 75,
                            45001: 0,
                            40008: 269,
                            40012: 231,
                            40011: 0,
                        }
                    ),
                )
            ),
            id="Data frame from MODBUS40",
        ),
        pytest.param(
            "5C 00 20 69 00 49",
            (MessageMaster(0x20, RequestReadNull())),
            id="Token Frame from MODBUS40",
        ),
        pytest.param(
            "C0 69 02 34 12 8d",
            (MessageSlave(RequestRead(0x1234))),
            id="Slave read request",
        ),
    ],
)
def test_parse(data: str, result):
    message = parse(bytes.fromhex(data))
    assert message == result


@pytest.mark.parametrize(
    "message,expected",
    [
        pytest.param(MessageSlave(RequestRead(0x1234)), "C0 69 02 34 12 8d"),
        pytest.param(MessageSlave(RequestRead(12345)), "C0 69 02 39 30 A2"),
        pytest.param(
            MessageSlave(RequestWrite(12345, 987654)), "C0 6B 06 39 30 06 12 0F 00 BF"
        ),
    ],
)
def test_construct(message: Message, expected: str):
    assert message.to_bytes() == bytes.fromhex(expected)


@pytest.mark.parametrize("parameters", [{1234: 5678, 4321: 8765}])
def test_response_data(parameters: dict[int, int]):
    message = ResponseData.from_bytes(ResponseData(parameters).to_bytes())
    assert message.parameters == parameters


@pytest.mark.parametrize("register,value", [(1234, 5678), (4321, 8765)])
def test_response_read(register: int, value: int):
    message = ResponseRead.from_bytes(ResponseRead(register, value).to_bytes())
    assert message.register == register
    assert message.value == value


@pytest.mark.asyncio
async def test_response_future_read():

    response = ResponseFuture[ResponseRead]()
    result: ResponseRead | None = None

    with fail_after(10):

        async def wait_set():
            response.set(ResponseRead(1234, 5678))

        async with create_task_group() as tg:
            tg.start_soon(wait_set)
            result = await response.get()

        assert result
        assert result.register == 1234
        assert result.value == 5678
