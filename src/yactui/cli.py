import os
import pathlib
import sys
import logging
import asyncio
import argparse

from typing import List, Optional
from rich_argparse import RichHelpFormatter

from .ui import CyphalTUI
from .node import CyphalNode, MTU_GUESS

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        formatter_class=RichHelpFormatter, description="Cyphal TUI Application"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=int(0),
        help="Increase verbosity level",
    )
    # Set the Node for the TUI to be in the Cyphal Network
    parser.add_argument(
        "--node-id",
        type=int,
        default=int(0),
        help="The Node ID for this TUI instance in the Cyphal network default=%(default)s",
    )
    parser.add_argument(
        "--interface",
        type=str,
        default="lo",
        help="The network interface to bind to for Cyphal communication default=%(default)s",
    )
    # The IP address to bind to
    parser.add_argument(
        "--ip",
        type=str,
        default="127.0.0.1",
        help="The IP address to bind to for Cyphal communication default=%(default)s",
    )
    parser.add_argument(
        "--mtu",
        type=int,
        default=MTU_GUESS,
        help="The MTU to use for Cyphal communication default=%(default)s",
    )
    # the path to the cyphal generated types
    parser.add_argument(
        "--cyphal-path",
        action="store",
        type=pathlib.Path,
        default=f"{os.getenv('HOME')}/cyphal/dsdl",
        help="The path to the Cyphal generated types default=%(default)s",
    )
    parser.add_argument(
        "--gen-path",
        action="store",
        type=pathlib.Path,
        default=f"{os.getenv('HOME')}/cyphal/generated",
        help="Disable colored output in the TUI",
    )

    args = parser.parse_args(argv)

    if args.verbose > 0:
        logger.setLevel(logging.WARNING)
    if args.verbose > 1:
        logger.setLevel(logging.INFO)
    if args.verbose > 2:
        logger.setLevel(logging.DEBUG)

    cyphal_path = pathlib.Path(args.cyphal_path).resolve()
    generated_path = pathlib.Path(args.gen_path).resolve()

    assert os.path.exists(
        cyphal_path
    ), f"The specified Cyphal path does not exist: {cyphal_path}"
    assert os.path.exists(
        generated_path
    ), f"The specified generated path does not exist: {generated_path}"

    os.environ["UAVCAN__LOGGING__LEVEL"] = str(logger.level)
    # os.environ["UAVCAN__NODE__ID"] = str(args.node_id)
    # os.environ["UAVCAN__UDP__IFACE"] = str(args.ip)
    # os.environ["UAVCAN__UDP__MTU"] = str(args.mtu)
    os.environ["CYPHAL_PATH"] = str(cyphal_path)
    os.environ["CYPHAL_ALLOW_UNREGULATED_FIXED_PORT_ID"] = "true"
    sys.path.append(str(generated_path))

    async def run_apps():
        node = CyphalNode(
            node_id=args.node_id,
            ip=args.ip,
        )
        app = CyphalTUI(nodes=[node])
        try:
            # asyncio.run(app.run_async())
            await asyncio.gather(node.start(), app.run_async())
        except KeyboardInterrupt:
            pass
        finally:
            node.close()

    asyncio.run(run_apps())
    return 0
