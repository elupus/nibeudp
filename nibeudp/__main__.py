import logging

from anyio import run

from . import server

logging.basicConfig(level=logging.DEBUG)

run(server)
