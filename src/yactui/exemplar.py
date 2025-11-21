from importlib.metadata import metadata
import time
import asyncio
import logging
import collections
import pycyphal  # Importing PyCyphal will automatically install the import hook for DSDL compilation.

import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.
import pycyphal.transport.udp
import pycyphal.transport.can
import pycyphal.transport.serial
from urllib3 import request

# Import DSDLs after pycyphal import hook is installed.
import uavcan.node  # noqa
import uavcan.diagnostic  # noqa
import uavcan.time  # noqa

GetInfo = uavcan.node.GetInfo_1_0  # type: ignore
Version = uavcan.node.Version_1_0  # type: ignore
TimeSynchronization = uavcan.time.Synchronization_1_0  # type: ignore
SynchronizationMaster = uavcan.time.GetSynchronizationMasterInfo_0_1  # type: ignore
TimeStamp = uavcan.time.SynchronizedTimestamp_1_0  # type: ignore
# Severity = uavcan.diagnostic.Severity_1_0  # type: ignore
Record = uavcan.diagnostic.Record_1_1  # type: ignore
ExecuteCommand = uavcan.node.ExecuteCommand_1_3  # type: ignore
# Mode = uavcan.node.Mode_1  # type: ignore
# Health = uavcan.node.Health_1  # type: ignore

from typing import Any, Dict, List, Optional, Union
from pycyphal.application import make_node, NodeInfo, register

# Dataclasses for Cyphal Node
from .data import (
    Node,
    Mode,
    Health,
    Severity,
    Command,
    Status,
    Result,
    make_cyphal_node,
    Log,
    make_log,
    make_result,
)

UPDATE_PERIOD = 1.0  # seconds
MTU_GUESS = 1500 - 20 - 8 - 24  # Default MTU for Cyphal/UDP

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class ExemplarNode:
    """A testing node for Cyphal TUI"""

    node_info: GetInfo.Response
    subscribers: Dict[str, Any]
    publishers: Dict[str, Any]
    clients: Dict[str, Any]
    servers: Dict[str, Any]
    results: collections.deque[Result]
    transport_type: str
    transport: pycyphal.transport.Transport
    current_time: float = 0.0
    previous_time: float = 0.0
    captured_time: float = 0.0

    def __init__(
        self,
        node_id: int,
        ip: Optional[str] = "127.0.0.1",
        mtu: int = MTU_GUESS,
        inf: Optional[str] = "can0",
    ) -> None:
        """Constructor"""
        if ip is not None:
            self.transport_type = "UDP"
            self.transport = pycyphal.transport.udp.UDPTransport(
                local_ip_address=ip,
                local_node_id=node_id,
                mtu=mtu,
            )
        elif "can" in inf:
            # UNTESTED!
            self.transport_type = "CAN"
            self.transport = pycyphal.transport.can.CANTransport(
                interface_name=inf,
                local_node_id=node_id,
                mtu=mtu,
            )
        else:
            # UNTESTED!
            self.transport_type = "Serial"
            self.transport = pycyphal.transport.serial.SerialTransport(
                port_name=inf,
                local_node_id=node_id,
                mtu=mtu,
            )
        self.node_info = NodeInfo(
            name="org.opencyphal.exemplar",
            software_version=Version(0, 2),
            software_vcs_revision_id=1234,
            hardware_version=Version(15, 7),
        )
        self.node = make_node(info=self.node_info, transport=self.transport)
        self.node.heartbeat_publisher.mode = uavcan.node.Mode_1_0.OPERATIONAL
        self.node.heartbeat_publisher.health = uavcan.node.Health_1_0.NOMINAL
        self.subscribers = {
            "time": self.node.make_subscriber(TimeSynchronization),
        }
        self.publishers = {
            "time": self.node.make_publisher(TimeSynchronization),
            "diagnostic": self.node.make_publisher(Record),
        }
        self.clients = {}
        self.servers = {
            "time": self.node.get_server(SynchronizationMaster),
            "cmd": self.node.get_server(ExecuteCommand),
        }
        self.servers["cmd"].serve_in_background(self.on_command_request)
        self.running = True
        self.node.start()

    def get_time_message(self) -> TimeSynchronization:
        """Create a time message with the current time"""
        # we pretend here that we don't have access to time.time()
        previous_transmission_timestamp_microseconds = int(self.previous_time * 1_000_000)
        msg = TimeSynchronization(
            previous_transmission_timestamp_microsecond=previous_transmission_timestamp_microseconds
        )
        self.previous_time = self.current_time
        return msg

    def diagnostic(self, level: Severity, message: str = "") -> Record:  # type: ignore
        """Log a message to the Cyphal transport which we use"""
        return Record(
            timestamp=TimeStamp(microsecond=int(self.current_time * 1_000_000)),
            severity=uavcan.diagnostic.Severity_1_0(int(level)),
            text=message,
        )

    async def info(self, message: str):
        """Log an info message to the Cyphal network"""
        msg = self.diagnostic(Severity.INFO, message)
        await self.publishers["diagnostic"].publish(msg)

    async def debug(self, message: str):
        """Log a debug message to the Cyphal network"""
        msg = self.diagnostic(Severity.DEBUG, message)
        await self.publishers["diagnostic"].publish(msg)

    async def warning(self, message: str):
        """Log a warning message to the Cyphal network"""
        msg = self.diagnostic(Severity.WARNING, message)
        await self.publishers["diagnostic"].publish(msg)

    async def error(self, message: str):
        """Log an error message to the Cyphal network"""
        msg = self.diagnostic(Severity.ERROR, message)
        await self.publishers["diagnostic"].publish(msg)

    async def on_command_request(
        self, request: ExecuteCommand.Request, metadata: pycyphal.presentation.ServiceRequestMetadata
    ) -> ExecuteCommand.Response:
        await self.info(f"Received command {request.command} from node {metadata.client_node_id}")
        return ExecuteCommand.Response(ExecuteCommand.Response.STATUS_BAD_COMMAND)

    async def start(self):
        await asyncio.create_task(self.run())

    async def run(self):

        ###############################
        # Setup Subscription Callbacks
        ###############################

        def on_time(
            msg: TimeSynchronization,
            txfr: pycyphal.transport.TransferFrom,
        ) -> None:
            # Just update the captured time
            self.captured_time = float(msg.previous_transmission_timestamp_microsecond) / 1_000_000
            logger.info("Received time sync from Node ID %d: %f", txfr.source_node_id, self.captured_time)

        self.subscribers["time"].receive_in_background(on_time)

        ###############################
        # Run the main node loop!
        ###############################

        next_update_at = asyncio.get_running_loop().time() + UPDATE_PERIOD

        while self.running:
            logger.info(".")

            await self.info("Exemplar Node running...")
            await self.debug("Debug message from Exemplar Node.")
            await self.warning("Warning message from Exemplar Node.")
            await self.error("Error message from Exemplar Node.")
            await self.publishers["time"].publish(self.get_time_message())

            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
            self.current_time += UPDATE_PERIOD
            next_update_at += UPDATE_PERIOD

    def close(self) -> None:
        self.running = False
        self.node.close()
