import time
import asyncio
import logging
import collections
import pycyphal  # Importing PyCyphal will automatically install the import hook for DSDL compilation.

import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.
import pycyphal.transport.udp
import pycyphal.transport.can
import pycyphal.transport.serial

# Import DSDLs after pycyphal import hook is installed.
import uavcan.node  # noqa
import uavcan.node.port  # noqa
import uavcan.diagnostic  # noqa
import uavcan.time  # noqa

from typing import Any, Dict, List, Optional, Union
from pycyphal.application import make_node, NodeInfo, register
from pycyphal.application.file import FileServer

GetInfo = uavcan.node.GetInfo_1_0  # type: ignore
Heartbeat = uavcan.node.Heartbeat_1_0  # type: ignore
Record = uavcan.diagnostic.Record_1_1  # type: ignore
TimeSynchronization = uavcan.time.Synchronization_1_0  # type: ignore
TimeStamp = uavcan.time.SynchronizedTimestamp_1_0  # type: ignore
# Severity = uavcan.diagnostic.Severity_1_0  # type: ignore
SynchronizationMaster = uavcan.time.GetSynchronizationMasterInfo_0_1  # type: ignore
ExecuteCommand = uavcan.node.ExecuteCommand_1_3  # type: ignore
PortList = uavcan.node.port.List_1_0  # type: ignore
Version = uavcan.node.Version_1_0  # type: ignore
# Mode = uavcan.node.Mode_1  # type: ignore
# Health = uavcan.node.Health_1  # type: ignore
TransportStatistics = uavcan.node.GetTransportStatistics_0_1  # type: ignore

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


class CyphalNode:
    """Holds the Cyphal Node and pub/subs"""

    diagnostics: collections.deque[Log]
    current_time: float = 0.0
    previous_time: float = 0.0
    captured_time: float = 0.0
    receive_event: asyncio.Event
    running: bool
    known_nodes: Dict[int, Node]
    query_nodes: collections.deque[int]
    node_info: GetInfo.Response
    subscribers: Dict[str, Any]
    publishers: Dict[str, Any]
    clients: Dict[str, Any]
    servers: Dict[str, Any]
    results: collections.deque[Result]
    time_sync_enabled: bool

    def __init__(
        self,
        node_id: int,
        ip: Optional[str] = "127.0.0.1",
        mtu: int = MTU_GUESS,
        inf: Optional[str] = "can0",
        file_server_folders: List[str] = ["."],
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
        self.diagnostics = collections.deque(maxlen=100)
        self.results = collections.deque(maxlen=100)
        self.query_nodes = []
        self.known_nodes = {}
        self.node_info = NodeInfo(name="org.opencyphal.yactui", software_version=Version(0, 1))
        self.node = make_node(info=self.node_info, transport=self.transport)
        self.node.heartbeat_publisher.mode = Mode.INITIALIZATION
        self.node.heartbeat_publisher.health = Health.ADVISORY
        self.subscribers = {
            "heartbeat": self.node.make_subscriber(Heartbeat),
            "diagnostic": self.node.make_subscriber(Record),
            "time": self.node.make_subscriber(TimeSynchronization),
            "portlist": self.node.make_subscriber(PortList),
        }
        self.publishers = {
            "time": self.node.make_publisher(TimeSynchronization),
            "diagnostic": self.node.make_publisher(Record),
        }
        self.clients = {
            "get_node_info": self.node.make_client(GetInfo, server_node_id=0),
            "transport_statistics": self.node.make_client(TransportStatistics, server_node_id=0),
        }
        self.servers = {
            "time": self.node.get_server(SynchronizationMaster),
        }
        self.file_server = FileServer(self.node, roots=file_server_folders)
        self.receive_event = asyncio.Event()
        self.running = True
        self.time_sync_enabled = True
        self.node.start()

    def get_time_message(self) -> TimeSynchronization:
        """Create a time message with the current time"""
        self.current_time = time.time()
        previous_transmission_timestamp_microseconds = int(self.previous_time * 1_000_000)
        msg = TimeSynchronization(
            previous_transmission_timestamp_microsecond=previous_transmission_timestamp_microseconds
        )
        self.previous_time = self.current_time
        return msg

    def diagnostic(self, level: Severity, message: str = "") -> Record:
        """Log a message to the Cyphal transport which we use"""
        return Record(
            timestamp=TimeStamp(microsecond=int(time.time() * 1_000_000)),
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

    async def start(self):
        await asyncio.create_task(self.run())

    def mask_to_list(self, mask: List[bool]) -> List[int]:
        """Convert a boolean mask to a list of indices where the mask is True"""
        return [i for i, bit in enumerate(mask) if bit]

    async def send_command(self, server_node_id: int, command: Union[int, str], args: str) -> bool:
        """Send a command to a node"""
        client = self.node.make_client(ExecuteCommand, server_node_id=server_node_id)
        status: Status = Status.DID_NOT_SEND
        response_message: str = ""
        respondent: int = server_node_id
        try:
            # map the strings to command numbers from Cyphal
            if isinstance(command, int):
                # Convert string command to integer command
                cmd_value = int(command)
            else:  # if isinstance(command, str):
                try:
                    cmd_value = int(Command[command.upper()].value)
                except KeyError:
                    response_message = f"Unknown command: {command}"
                    return False  # the finally will still log the DID_NOT_SEND status

            request = ExecuteCommand.Request(
                command=cmd_value,
                parameter=args.encode("utf-8"),
            )
            response = await client.call(request)
            if response is None:
                # timeout
                status = Status.TIMEOUT
                response_message = f"Command {command} to Node ID {server_node_id} timed out."
                return False  # the finally will still log the status
            msg, metadata = response
            assert isinstance(msg, ExecuteCommand.Response)
            respondent = metadata.source_node_id
            status = Status(msg.status)
            response_message = msg.output.tobytes().decode("utf-8", errors="ignore")
        finally:
            client.close()
            self.results.append(
                make_result(
                    status=status,
                    output=response_message,
                    server_node_id=respondent,
                )
            )
        return True

    async def run(self):

        ###############################
        # Setup Subscription Callbacks
        ###############################

        def on_heartbeat(msg: Heartbeat, txfr: pycyphal.transport.TransferFrom) -> None:
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
                self.known_nodes[txfr.source_node_id].vendor_specific_status_code = msg.vendor_specific_status_code
            # once we find it, add it to the query list
            self.query_nodes.append(txfr.source_node_id)
            # new data!
            self.receive_event.set()

        self.subscribers["heartbeat"].receive_in_background(on_heartbeat)

        def on_portlist(msg: PortList, txfr: pycyphal.transport.TransferFrom) -> None:
            if txfr.source_node_id in self.known_nodes.keys():
                # self.known_nodes[txfr.source_node_id].publishers = self.mask_to_list(
                #     msg.publishers.mask
                # )
                # self.known_nodes[txfr.source_node_id].subscribers = self.mask_to_list(
                #     msg.subscribers.mask
                # )
                self.known_nodes[txfr.source_node_id].clients = self.mask_to_list(msg.clients.mask)
                self.known_nodes[txfr.source_node_id].servers = self.mask_to_list(msg.servers.mask)
            # new data!
            self.receive_event.set()

        self.subscribers["portlist"].receive_in_background(on_portlist)

        def on_diagnostic(msg: Record, txfr: pycyphal.transport.TransferFrom) -> None:
            self.diagnostics.append(
                make_log(
                    timestamp_microseconds=msg.timestamp.microsecond,
                    level=Severity(msg.severity.value),
                    message=msg.text.tobytes().decode("utf-8", errors="ignore"),
                    node_id=txfr.source_node_id,
                )
            )
            self.receive_event.set()

        self.subscribers["diagnostic"].receive_in_background(on_diagnostic)

        def on_time(
            msg: TimeSynchronization,
            txfr: pycyphal.transport.TransferFrom,
        ) -> None:
            # Just update the captured time
            self.captured_time = float(msg.previous_transmission_timestamp_microsecond) / 1_000_000
            self.diagnostics.append(
                make_log(
                    timestamp_microseconds=self.captured_time,
                    level=Severity.INFO,
                    message=f"Received time sync from Node ID {txfr.source_node_id}: {self.captured_time}",
                    node_id=self.node.id,
                )
            )
            self.receive_event.set()

        self.subscribers["time"].receive_in_background(on_time)

        ###############################
        # Run the main node loop!
        ###############################

        next_update_at = asyncio.get_running_loop().time() + UPDATE_PERIOD

        while self.running:

            if self.time_sync_enabled:
                await self.publishers["time"].publish(self.get_time_message())

            for server_node_id in self.query_nodes:
                if self.known_nodes[server_node_id].name is not None:
                    continue  # Already have info
                client = self.node.make_client(GetInfo, server_node_id=server_node_id)
                try:
                    response = await client.call(GetInfo.Request())
                    if response is None:
                        # timeout, try again later
                        self.query_nodes.append(server_node_id)
                        continue
                    # split the tuple up
                    msg, metadata = response
                    assert isinstance(msg, GetInfo.Response)
                    print(
                        "Got info for Node ID",
                        metadata.source_node_id,
                        " Response:",
                        response,
                    )
                    self.known_nodes[server_node_id].name = msg.name.tobytes().decode("utf-8", errors="ignore")
                    self.known_nodes[server_node_id].software_version = (
                        msg.software_version.major,
                        msg.software_version.minor,
                    )
                    self.known_nodes[server_node_id].hardware_version = (
                        msg.hardware_version.major,
                        msg.hardware_version.minor,
                    )
                    self.known_nodes[server_node_id].revision = msg.software_vcs_revision_id
                    self.known_nodes[server_node_id].crc64we = msg.software_image_crc.flatten()
                    self.known_nodes[server_node_id].unique_id = msg.unique_id.tobytes()
                    self.known_nodes[server_node_id].certificate = msg.certificate_of_authenticity.tobytes()
                finally:
                    client.close()
            # For each known Node ID, get transport statistics every period
            for node_id in self.known_nodes.keys():
                client = self.node.make_client(TransportStatistics, server_node_id=node_id)
                try:
                    response = await client.call(TransportStatistics.Request())
                    if response is None:
                        # timeout
                        continue
                    self.known_nodes[node_id].number_emitted = response.transfer_statistics.number_of_emitted_frames
                    self.known_nodes[node_id].number_received = response.transfer_statistics.number_of_received_frames
                    self.known_nodes[node_id].number_error = response.transfer_statistics.number_of_error_frames
                finally:
                    client.close()
            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
            next_update_at += UPDATE_PERIOD

    def close(self) -> None:
        self.running = False
        self.node.close()
