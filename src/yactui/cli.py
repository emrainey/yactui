import os
import pathlib
import sys
import logging
import asyncio
import argparse

from typing import List, Optional
from rich_argparse import RichHelpFormatter

from yactui.ui import CyphalTUI
from yactui.node import CyphalNode, MTU_GUESS

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        formatter_class=RichHelpFormatter,
        description="Yet Another Cyphal Textual User Interface (YACTUI)",
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
        help="The generated path for the Cyphal generated types default=%(default)s",
    )
    parser.add_argument(
        "--exemplar",
        action="store_true",
        help="Run the exemplar node instead of the TUI",
    )
    parser.add_argument(
        "--file-server-folder",
        action="append",
        default=["."],
        help="The folder to serve files from when running the exemplar node default=%(default)s",
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

    assert os.path.exists(cyphal_path), f"The specified Cyphal path does not exist: {cyphal_path}"
    assert os.path.exists(generated_path), f"The specified generated path does not exist: {generated_path}"
    sys.path.append(str(generated_path))

    async def run_apps() -> None:
        if args.exemplar:
            from .exemplar import ExemplarNode

            print("Running Exemplar Node...")
            exemplar = ExemplarNode(
                node_id=args.node_id,
                ip=args.ip,
                inf=args.interface,
                mtu=args.mtu,
            )
            try:
                await exemplar.start()
            except KeyboardInterrupt:
                pass
            finally:
                exemplar.close()
            return
        else:
            node = CyphalNode(node_id=args.node_id, ip=args.ip, file_server_folders=args.file_server_folder)
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


# In case you want to run this module directly with `textual console`
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
