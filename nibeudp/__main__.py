import logging

import asyncclick as click
from anyio import create_task_group, fail_after, run, sleep

from . import Connection, Controller


@click.group()
@click.option("-l", "--log-level", type=str, default="WARNING")
def cli(log_level: str):
    logging.basicConfig(
        format="[%(levelname)-8s] %(message)s",
        level=log_level,
    )
    logging.log(logging.INFO, "Log level set to %r", log_level)


@cli.command("monitor")
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


try:
    cli()
except (KeyboardInterrupt, SystemExit):
    pass
