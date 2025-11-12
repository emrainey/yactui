import time
import asyncio
import pycyphal  # Importing PyCyphal will automatically install the import hook for DSDL compilation.

import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.


# Import DSDLs after pycyphal import hook is installed.
import uavcan.node  # noqa
import uavcan.diagnostic  # noqa
import uavcan.protocol  # qnoqa
import uavcan.time  # noqa

from pycyphal.application.heartbeat_publisher import Health
from pycyphal.application import make_node, NodeInfo, register

UPDATE_PERIOD = 0.5  # seconds


class CyphalNode:
    """Holds the Cyphal Node and pub/subs"""

    def __init__(self, node_id: int, interface: str = "lo", ip: str = "127.0.0.1"):
        """Constructor"""
        self.node_info = uavcan.node.GetInfo_1.Response(
            name="org.cyphal.tui", software_version=pycyphal.Version(0, 1)
        )
        self.node = make_node(
            info=self.node_info,
            node_id=node_id,
            transport="udp",
            interface=interface,
            ip=ip,
            register_default_services=True,
        )
        self.node.heartbeat_publisher.mode = uavcan.node.Mode_1.INITIALIZING
        self.node.heartbeat_publisher.health = Health.ADVISORY
        self.subscribers = {
            "heartbeat": self.node.make_subscriber(uavcan.node.Heartbeat_1_0),
            "diagnostic": self.node.make_subscriber(uavcan.diagnostic.Record),
            "get_node_info": self.node.make_client(uavcan.protocol.GetNodeInfo),
            "transport_statistics": self.node.make_client(
                uavcan.protocol.TransportStatistics
            ),
            "time": self.node.make_subscriber(uavcan.time.SynchronizedTimestamp),
        }
        self.publishers = {
            "time": self.node.make_publisher(uavcan.time.SynchronizedTimestamp),
        }
        self.node.start()

    async def run(self):
        next_update_at = asyncio.get_running_loop().time() + UPDATE_PERIOD
        while True:
            # do stuff
            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
            next_update_at += UPDATE_PERIOD

    def close(self) -> None:
        self.node.close()
