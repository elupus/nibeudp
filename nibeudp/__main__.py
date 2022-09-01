import argparse
import logging

from anyio import create_task_group, fail_after, run, sleep

from . import Connection, Controller, RequestRead

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description="Listen to pump messages")
parser.add_argument("host", type=str)

command = parser.add_subparsers(dest="command", required=True)

command_monitor = command.add_parser("monitor")
command_monitor.add_argument("--registers", type=int, nargs="+", default=[])

args = parser.parse_args()


async def monitor():
    async with Connection(args.host) as connection, Controller(
        connection
    ) as controller:

        async def reader():
            async for message in controller:
                LOG.debug("RX: %s", message)

        async def update():
            while True:
                for register in args.registers:
                    try:
                        async with fail_after(2):
                            value = await controller.read(register)
                        print(f"{register}: {value}")
                    except TimeoutError:
                        print(f"{register}: TIMEOUT")
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
