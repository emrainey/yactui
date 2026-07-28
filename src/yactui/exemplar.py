from importlib.metadata import metadata
import random
import statistics
import time
import asyncio
import logging
import collections
import pycyphal  # Importing PyCyphal will automatically install the import hook for DSDL compilation.

import pycyphal.application  # This module requires the root namespace "uavcan" to be trans-piled.
import pycyphal.transport.udp
import pycyphal.transport.can
import pycyphal.transport.serial
from urllib3 import request

# Import DSDLs after pycyphal import hook is installed.
import uavcan.node  # noqa
import uavcan.diagnostic  # noqa
import uavcan.time  # noqa

GetInfo = uavcan.node.GetInfo_1_0
Version = uavcan.node.Version_1_0
TimeSynchronization = uavcan.time.Synchronization_1_0
SynchronizationMaster = uavcan.time.GetSynchronizationMasterInfo_0_1
TimeStamp = uavcan.time.SynchronizedTimestamp_1_0
# Severity = uavcan.diagnostic.Severity_1_0
Record = uavcan.diagnostic.Record_1_1
ExecuteCommand = uavcan.node.ExecuteCommand_1_3
# Mode = uavcan.node.Mode_1
# Health = uavcan.node.Health_1
TransportStatistics = uavcan.node.GetTransportStatistics_0_1
IOStatistics = uavcan.node.IOStatistics_0_1

from typing import Any, Dict, List, Optional, Union
from pycyphal.application import make_node, NodeInfo, register

# Dataclasses for Cyphal Node
from yactui.data import (
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
from yactui import TimeSyncer, MonotonicClock, make_transport, NodeStatistics

UPDATE_PERIOD = 1.0  # seconds
MTU_GUESS = 1500 - 20 - 8 - 24  # Default MTU for Cyphal/UDP

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class ExemplarNode(MonotonicClock):
    """A testing node for Cyphal TUI"""

    node_info: GetInfo.Response
    subscribers: Dict[str, Any]
    publishers: Dict[str, Any]
    clients: Dict[str, Any]
    servers: Dict[str, Any]
    results: collections.deque[Result]
    transport_type: str
    transport: pycyphal.transport.Transport
    time_sync: TimeSyncer
    last_time_microseconds: int
    statistics: NodeStatistics

    def __init__(
        self,
        node_id: int,
        ip: Optional[str] = "127.0.0.1",
        mtu: int = MTU_GUESS,
        inf: Optional[str] = "can0",
    ) -> None:
        """Constructor"""
        self.transport_type, self.transport = make_transport(ip, inf, mtu, node_id)
        self.node_info = NodeInfo(
            name="org.opencyphal.exemplar",
            software_version=Version(0, 2),
            software_vcs_revision_id=1234,
            hardware_version=Version(15, 7),
            software_image_crc=0xDEADBEEF,
        )
        self.statistics = NodeStatistics(0, 0, 0)
        self.time_sync = TimeSyncer(clock=self, client_not_server=False, time_basis_seconds=42.0)
        self.node = make_node(
            info=self.node_info,
            transport=self.transport,
        )
        self.node.heartbeat_publisher.mode = uavcan.node.Mode_1_0.MAINTENANCE
        self.node.heartbeat_publisher.health = uavcan.node.Health_1_0.ADVISORY
        self.node.heartbeat_publisher.vendor_specific_status_code = 42
        self.last_time_microseconds = self.time_sync.get_current_time_microseconds()
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
            "transport_stats": self.node.get_server(TransportStatistics),
        }
        self.servers["cmd"].serve_in_background(self.on_command_request)
        self.servers["transport_stats"].serve_in_background(self.on_transport_statistics)
        self.running = True
        self.node.registry.setdefault(
            "exemplar.random.vec4",
            lambda: [
                random.uniform(0.0, 1.0),
                random.uniform(0.0, 1.0),
                random.uniform(0.0, 1.0),
                random.uniform(0.0, 1.0),
            ],
        )
        self.node.registry.setdefault("exemplar.good-time.call", [8, 6, 7, 5, 3, 0, 9])  # mutable
        self.node.registry.setdefault("exemplar.status.code", lambda: int(42))  # not mutable
        self.node.registry["uavcan.node.description"] = "An exemplar node for testing YACTUI."
        self.node.registry.setdefault("exemplar.errno", -9)  # mutable?
        self.node.start()

    def get_time_microseconds(self) -> int:
        """Get the current time in microseconds"""
        return int(time.monotonic() * 1_000_000)

    def get_time_message(self) -> TimeSynchronization:
        """Create a time message with the current time"""
        msg = TimeSynchronization(
            previous_transmission_timestamp_microsecond=self.last_time_microseconds,
        )
        self.last_time_microseconds = self.time_sync.get_current_time_microseconds()
        return msg

    def diagnostic(self, level: Severity, message: str = "") -> Record:
        """Log a message to the Cyphal transport which we use"""
        return Record(
            timestamp=TimeStamp(microsecond=self.time_sync.get_current_time_microseconds()),
            severity=uavcan.diagnostic.Severity_1_0(int(level)),
            text=message,
        )

    async def info(self, message: str) -> None:
        """Log an info message to the Cyphal network"""
        msg = self.diagnostic(Severity.INFO, message)
        await self.publishers["diagnostic"].publish(msg)

    async def debug(self, message: str) -> None:
        """Log a debug message to the Cyphal network"""
        msg = self.diagnostic(Severity.DEBUG, message)
        await self.publishers["diagnostic"].publish(msg)

    async def warning(self, message: str) -> None:
        """Log a warning message to the Cyphal network"""
        msg = self.diagnostic(Severity.WARNING, message)
        await self.publishers["diagnostic"].publish(msg)

    async def error(self, message: str) -> None:
        """Log an error message to the Cyphal network"""
        msg = self.diagnostic(Severity.ERROR, message)
        await self.publishers["diagnostic"].publish(msg)

    async def on_command_request(
        self, request: ExecuteCommand.Request, metadata: pycyphal.presentation.ServiceRequestMetadata
    ) -> ExecuteCommand.Response:
        await self.info(f"Received command {request.command} from node {metadata.client_node_id}")
        return ExecuteCommand.Response(ExecuteCommand.Response.STATUS_BAD_COMMAND)

    async def on_transport_statistics(
        self, request: TransportStatistics.Request, metadata: pycyphal.presentation.ServiceRequestMetadata
    ) -> TransportStatistics.Response:
        await self.debug(f"Transport stats: ")
        stats = IOStatistics(
            num_emitted=self.statistics.number_emitted,
            num_received=self.statistics.number_received,
            num_errored=self.statistics.number_error,
        )
        return TransportStatistics.Response(
            transfer_statistics=stats,
            network_interface_statistics=[stats],
        )

    async def start(self) -> None:
        await asyncio.create_task(self.run())

    async def run(self) -> None:

        ###############################
        # Setup Subscription Callbacks
        ###############################

        def on_time(
            msg: TimeSynchronization,
            txfr: pycyphal.transport.TransferFrom,
        ) -> None:
            self.time_sync.on_receive_previous_time_microseconds(msg.previous_transmission_timestamp_microsecond)

        self.subscribers["time"].receive_in_background(on_time)

        ###############################
        # Run the main node loop!
        ###############################

        next_update_at = asyncio.get_running_loop().time() + UPDATE_PERIOD

        while self.running:
            await self.info("Exemplar Node running...")
            await self.debug("Debug message from Exemplar Node.")
            await self.warning("Warning message from Exemplar Node.")
            await self.error("Error message from Exemplar Node.")
            await self.publishers["time"].publish(self.get_time_message())

            # fetch the transport statistics and accumulate them into the local tally
            self.statistics.number_emitted = 0
            self.statistics.number_received = 0
            self.statistics.number_error = 0
            for session in self.transport.output_sessions:
                stats = session.sample_statistics()
                self.statistics.number_emitted += stats.frames
                self.statistics.number_error += stats.errors
            for session in self.transport.input_sessions:
                stats = session.sample_statistics()
                self.statistics.number_received += stats.frames
                self.statistics.number_error += stats.errors

            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
            next_update_at += UPDATE_PERIOD

    def close(self) -> None:
        self.running = False
        self.node.close()
