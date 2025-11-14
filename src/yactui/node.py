import time
import asyncio
import logging
import collections
import pycyphal  # Importing PyCyphal will automatically install the import hook for DSDL compilation.

import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.
import pycyphal.transport.udp

# Import DSDLs after pycyphal import hook is installed.
import uavcan.node  # noqa
import uavcan.diagnostic  # noqa
import uavcan.time  # noqa

from typing import Any, Dict, List
from pycyphal.application.heartbeat_publisher import Health
from pycyphal.application import make_node, NodeInfo, register

# Dataclasses for Cyphal Node
from .data import Node, Mode, Health, Severity, make_cyphal_node, Log, make_log

UPDATE_PERIOD = 1.0  # seconds


class CyphalNode:
    """Holds the Cyphal Node and pub/subs"""

    diagnostics: Dict[int, collections.deque[Log]]
    current_time: float = 0.0
    previous_time: float = 0.0
    receive_event: asyncio.Event
    running: bool
    known_nodes: Dict[int, Node]
    query_nodes: List[int]

    def __init__(self, node_id: int, ip: str = "127.0.0.1"):
        """Constructor"""
        self.transport = "UDP"
        self.diagnostics = {}
        self.query_nodes = []
        self.known_nodes = {}
        self.node_info = NodeInfo(
            name="org.opencyphal.yactui", software_version=uavcan.node.Version_1_0(0, 1)
        )
        self.node = make_node(info=self.node_info)  # , transport=self.transport)
        self.node.heartbeat_publisher.mode = uavcan.node.Mode_1.INITIALIZATION
        self.node.heartbeat_publisher.health = Health.ADVISORY
        self.subscribers = {
            "heartbeat": self.node.make_subscriber(uavcan.node.Heartbeat_1_0),
            "diagnostic": self.node.make_subscriber(uavcan.diagnostic.Record_1_1),
            "time": self.node.make_subscriber(uavcan.time.Synchronization_1_0),
            "portlist": self.node.make_subscriber(uavcan.node.port.List_1_0),
        }
        self.publishers = {
            "time": self.node.make_publisher(uavcan.time.Synchronization_1_0),
            "diagnostic": self.node.make_publisher(uavcan.diagnostic.Record_1_1),
        }
        self.clients = {
            "get_node_info": self.node.make_client(
                uavcan.node.GetInfo_1_0, server_node_id=0
            ),
            "transport_statistics": self.node.make_client(
                uavcan.node.GetTransportStatistics_0_1, server_node_id=0
            ),
        }
        self.servers = {
            "time": self.node.get_server(uavcan.time.GetSynchronizationMasterInfo_0_1),
        }
        self.receive_event = asyncio.Event()
        self.running = True
        self.node.start()

    def get_time_message(self) -> uavcan.time.Synchronization_1_0:
        """Create a time message with the current time"""
        self.current_time = time.time()
        previous_transmission_timestamp_microseconds = int(
            self.previous_time * 1_000_000
        )
        msg = uavcan.time.Synchronization_1_0(
            previous_transmission_timestamp_microsecond=previous_transmission_timestamp_microseconds
        )
        self.previous_time = self.current_time
        return msg

    def log(self, level: Severity = Severity.INFO, message: str = "") -> None:
        """Log a message to the Cyphal network"""
        log_msg = uavcan.diagnostic.Record_1_1(
            timestamp=uavcan.time.SynchronizedTimestamp_1_0(
                int(time.time() * 1_000_000)
            ),
            severity=uavcan.diagnostic.Severity_1_0(int(level)),
            text=message,
        )
        self.publishers["diagnostic"].publish(log_msg)

    async def start(self):
        await asyncio.create_task(self.run())

    def mask_to_list(self, mask: List[bool]) -> List[int]:
        """Convert a boolean mask to a list of indices where the mask is True"""
        return [i for i, bit in enumerate(mask) if bit]

    async def run(self):
        def on_heartbeat(
            msg: uavcan.node.Heartbeat_1_0, txfr: pycyphal.transport.TransferFrom
        ) -> None:
            if txfr.source_node_id not in self.known_nodes.keys():
                self.known_nodes[txfr.source_node_id] = make_cyphal_node(
                    txfr.source_node_id,
                    msg.health.value,
                    msg.mode.value,
                    msg.uptime,
                    msg.vendor_specific_status_code,
                )
            else:
                self.known_nodes[txfr.source_node_id].health = Health(msg.health.value)
                self.known_nodes[txfr.source_node_id].mode = Mode(msg.mode.value)
                self.known_nodes[txfr.source_node_id].uptime = msg.uptime
                self.known_nodes[txfr.source_node_id].vendor_specific_status_code = (
                    msg.vendor_specific_status_code
                )
            self.receive_event.set()

        self.subscribers["heartbeat"].receive_in_background(on_heartbeat)

        def on_portlist(
            msg: uavcan.node.port.List_1_0, txfr: pycyphal.transport.TransferFrom
        ) -> None:
            if txfr.source_node_id in self.known_nodes.keys():
                self.known_nodes[txfr.source_node_id].publishers = (
                    []
                )  # msg.publishers.mask
                self.known_nodes[txfr.source_node_id].subscribers = (
                    []
                )  # msg.subscribers.ids
                self.known_nodes[txfr.source_node_id].clients = []  # msg.clients.ids
                self.known_nodes[txfr.source_node_id].servers = []  # msg.servers.ids
            # self.known_nodes[txfr.source_node_id].publishers = self.mask_to_list(
            #     msg.publishers.mask
            # )
            # self.known_nodes[txfr.source_node_id].subscribers = self.mask_to_list(
            #     msg.subscribers.mask
            # )
            self.known_nodes[txfr.source_node_id].clients = self.mask_to_list(
                msg.clients.mask
            )
            self.known_nodes[txfr.source_node_id].servers = self.mask_to_list(
                msg.servers.mask
            )
            self.receive_event.set()

        self.subscribers["portlist"].receive_in_background(on_portlist)

        def on_diagnostic(
            msg: uavcan.diagnostic.Record_1_1, txfr: pycyphal.transport.TransferFrom
        ) -> None:
            if txfr.source_node_id not in self.diagnostics:
                self.diagnostics[txfr.source_node_id] = collections.deque(maxlen=100)
            self.diagnostics[txfr.source_node_id].append(
                make_log(
                    timestamp_microseconds=msg.timestamp.value,
                    level=Severity(msg.severity.value),
                    message=msg.message,
                    node_id=txfr.source_node_id,
                )
            )
            self.receive_event.set()

        self.subscribers["diagnostic"].receive_in_background(on_diagnostic)

        next_update_at = asyncio.get_running_loop().time() + UPDATE_PERIOD
        while self.running:
            # do stuff
            self.publishers["time"].publish(self.get_time_message())
            for server_node_id in self.query_nodes:
                if server_node_id in self.known_nodes:
                    continue  # Already have info
                try:
                    response = await self.clients["get_node_info"].request(
                        uavcan.node.GetInfo_1_0.Request(),
                        server_node_id=server_node_id,
                        timeout=1.0,
                    )
                    self.known_nodes[server_node_id]["getinfo"] = response
                except pycyphal.transport.OperationTimedOutError:
                    logging.debug(f"Node {server_node_id} did not respond to GetInfo")
                try:
                    response = await self.clients["transport_statistics"].request(
                        uavcan.node.GetTransportStatistics_0_1.Request(),
                        server_node_id=server_node_id,
                        timeout=1.0,
                    )
                    self.known_nodes[server_node_id]["transport_statistics"] = response
                except pycyphal.transport.OperationTimedOutError:
                    logging.debug(
                        f"Node {server_node_id} did not respond to GetTransportStatistics"
                    )
            self.log(Severity.INFO, "YACTUI is running")
            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
            next_update_at += UPDATE_PERIOD

    def close(self) -> None:
        self.running = False
        self.node.close()
