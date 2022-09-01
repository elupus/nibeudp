import logging

import asyncclick as click
from anyio import create_task_group, fail_after, run, sleep

from . import Connection, Controller

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(__name__)


@click.command("monitor")
@click.argument("host", type=str)
@click.argument("registers", type=int, nargs=-1)
async def monitor(host: str, registers: list[int]):
    async with Connection(host) as connection, Controller(connection) as controller:

        async def reader():
            async for message in controller:
                click.echo(f"RX: {message}")

        async def update():
            while True:
                for register in registers:
                    try:
                        async with fail_after(2):
                            value = await controller.read(register)
                        click.echo(f"READ {register}: {value}")
                    except TimeoutError:
                        click.echo(f"READ {register}: TIMEOUT")
                await sleep(1.0)

        async with create_task_group() as tg:
            tg.start_soon(reader)
            tg.start_soon(update)


@click.group()
def cli():
    pass


cli.add_command(monitor)

try:
    cli()
except KeyboardInterrupt:
    pass
