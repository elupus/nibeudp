import argparse
import logging

from anyio import create_task_group, run, sleep

from . import Connection, RequestRead

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description="Listen to pump messages")
parser.add_argument("host", type=str)

command = parser.add_subparsers(dest="command", required=True)

command_monitor = command.add_parser("monitor")
command_monitor.add_argument("--registers", type=int, nargs="+", default=[])

args = parser.parse_args()


async def monitor():
    async with Connection(args.host) as connection:

        async def reader():
            async for message in connection:
                LOG.debug("RX: %s", message)

        async def update():
            while True:
                for register in args.registers:
                    await connection.send(RequestRead(register))
                await sleep(1.0)

        async with create_task_group() as tg:
            tg.start_soon(reader)
            tg.start_soon(update)


async def main():
    if args.command == "monitor":
        await monitor()


try:
    run(main)
except KeyboardInterrupt:
    pass
