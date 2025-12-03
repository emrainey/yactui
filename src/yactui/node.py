import ast
import time
import asyncio
import collections
import traceback
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

from yactui import MonotonicClock, NodeStatistics, TimeSyncer, make_transport, EXCEPTIONS_LOGFILE

GetInfo = uavcan.node.GetInfo_1_0
Heartbeat = uavcan.node.Heartbeat_1_0
Record = uavcan.diagnostic.Record_1_1
TimeSynchronization = uavcan.time.Synchronization_1_0
TimeStamp = uavcan.time.SynchronizedTimestamp_1_0
# Severity = uavcan.diagnostic.Severity_1_0
SynchronizationMaster = uavcan.time.GetSynchronizationMasterInfo_0_1
ExecuteCommand = uavcan.node.ExecuteCommand_1_3
PortList = uavcan.node.port.List_1_0
Version = uavcan.node.Version_1_0
SubjectList = uavcan.node.port.SubjectIDList_1_0
# Mode = uavcan.node.Mode_1
# Health = uavcan.node.Health_1
TransportStatistics = uavcan.node.GetTransportStatistics_0_1
RegisterList = uavcan.register.List_1_0
RegisterAccess = uavcan.register.Access_1_0
Name = uavcan.register.Name_1_0
Value = uavcan.register.Value_1_0
Empty = uavcan.primitive.Empty_1_0

# Dataclasses for Cyphal Node
from yactui.data import (
    Node,
    Mode,
    Health,
    Register,
    Severity,
    Command,
    Status,
    Result,
    make_cyphal_node,
    Log,
    make_log,
    make_result,
    make_register,
)

UPDATE_PERIOD = 1.0  # seconds
MTU_GUESS = 1500 - 20 - 8 - 24  # Default MTU for Cyphal/UDP


class CyphalNode(MonotonicClock):
    """Holds the Cyphal Node and pub/subs"""

    receive_event: asyncio.Event
    diagnostics: collections.deque[Log]
    current_time: float = 0.0
    previous_time: float = 0.0
    captured_time: float = 0.0
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
    transport: Union[
        pycyphal.transport.udp.UDPTransport,
        pycyphal.transport.can.CANTransport,
        pycyphal.transport.serial.SerialTransport,
    ]
    transport_type: str

    def __init__(
        self,
        node_id: int,
        ip: Optional[str] = "127.0.0.1",
        mtu: int = MTU_GUESS,
        inf: Optional[str] = "can0",
        file_server_folders: List[str] = ["."],
    ) -> None:
        """Constructor"""
        self.receive_event = asyncio.Event()
        self.transport_type, self.transport = make_transport(ip, inf, mtu, node_id)
        self.diagnostics = collections.deque(maxlen=100)
        self.results = collections.deque(maxlen=100)
        self.query_nodes = collections.deque()
        self.known_nodes = dict()
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
        self.running = True
        self.time_sync_enabled = True
        self.time_sync = TimeSyncer(clock=self, client_not_server=True)
        self.node.start()

    def get_time_microseconds(self) -> int:
        """Get the current time in microseconds"""
        return int(time.monotonic() * 1_000_000)

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
            timestamp=TimeStamp(microsecond=int(time.monotonic() * 1_000_000)),
            severity=uavcan.diagnostic.Severity_1_0(level.value),
            text=message.encode("utf-8", errors="ignore"),
        )

    async def request_register_list(self, node_id: int) -> None:
        """
        First, this will query the RegisterList from 0 until it returns an empty value. This will fix the number of registers.
        Then it will "Access" each register to get its value and metadata and populate the node's registry.
        Request the register list from a node
        """
        end_of_list = False
        index: int = 0
        await self.info(f"Requesting Register List from Node ID {node_id}")
        if node_id not in self.known_nodes:
            return  # unknown node
        node: Node = self.known_nodes[node_id]
        if len(node.registry) == 0:
            while not end_of_list:
                client = self.node.make_client(RegisterList, server_node_id=node_id)
                try:
                    response = await client.call(RegisterList.Request(index=index))
                    if response is None:
                        # timeout
                        break
                    msg, metadata = response
                    assert isinstance(msg, RegisterList.Response)
                    name = msg.name.name.tobytes().decode("utf-8", errors="ignore")
                    if name != "":
                        node.registry.append(make_register(index, name))
                        index += 1
                        await self.info(f"Register List for Node ID {node_id}: {msg}")
                    else:
                        end_of_list = True
                finally:
                    client.close()
        # Now we have the list of registers, get their values
        for register in node.registry:
            client = self.node.make_client(RegisterAccess, server_node_id=node_id)
            try:
                response = await client.call(
                    RegisterAccess.Request(name=Name(register.name.encode("utf-8")), value=Value())
                )
                if response is None:
                    # timeout
                    continue
                msg, metadata = response
                assert isinstance(msg, RegisterAccess.Response)
                if msg.value.empty is not None:
                    register.value = None
                    register.type = "Empty"
                elif msg.value.string is not None:
                    register.value = msg.value.string.value.tobytes().decode("utf-8", errors="ignore")
                    register.type = "String"
                elif msg.value.unstructured is not None:
                    # convert to a hex string
                    register.value = msg.value.unstructured.value.tobytes().hex()
                    register.type = "Unstructured"
                elif msg.value.bit is not None:
                    register.value = msg.value.bit.value
                    register.type = "Bit"
                elif msg.value.integer8 is not None:
                    register.value = msg.value.integer8.value
                    register.type = "Integer8"
                elif msg.value.integer16 is not None:
                    register.value = msg.value.integer16.value
                    register.type = "Integer16"
                elif msg.value.integer32 is not None:
                    register.value = msg.value.integer32.value
                    register.type = "Integer32"
                elif msg.value.integer64 is not None:
                    register.value = msg.value.integer64.value
                    register.type = "Integer64"
                elif msg.value.natural8 is not None:
                    register.value = msg.value.natural8.value
                    register.type = "Natural8"
                elif msg.value.natural16 is not None:
                    register.value = msg.value.natural16.value
                    register.type = "Natural16"
                elif msg.value.natural32 is not None:
                    register.value = msg.value.natural32.value
                    register.type = "Natural32"
                elif msg.value.natural64 is not None:
                    register.value = msg.value.natural64.value
                    register.type = "Natural64"
                elif msg.value.real32 is not None:
                    register.value = msg.value.real32.value
                    register.type = "Real32"
                elif msg.value.real64 is not None:
                    register.value = msg.value.real64.value
                    register.type = "Real64"
                else:
                    register.value = None
                    register.type = "???"
                register.mutable = msg.mutable
                register.persistent = msg.persistent
            finally:
                client.close()

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

    async def start(self) -> None:
        await asyncio.create_task(self.run())

    def subjectlist_to_list(self, subject_list: SubjectList) -> List[int]:
        """Convert a SubjectList to a list of Subject ID integers"""
        if subject_list.mask is not None:
            return self.mask_to_list(subject_list.mask)
        elif subject_list.sparse_list is not None:  # sparse list
            return [port.value for port in subject_list.sparse_list]
        else:
            return []

    def mask_to_list(self, mask: List[bool]) -> List[int]:
        """Convert a boolean mask to a list of indices where the mask is True"""
        return [i for i, bit in enumerate(mask) if bit]

    async def send_command(self, server_node_id: int, command: Union[int, str], args: str) -> bool:
        """Send a command to a node"""
        client = self.node.make_client(ExecuteCommand, server_node_id=server_node_id)
        status: Status = Status.DID_NOT_SEND
        response_message: str = ""
        respondent: int = server_node_id
        if respondent < 0 or respondent > 127:
            response_message = f"Invalid Node ID: {respondent}"
            self.results.append(
                make_result(
                    status=Status.DID_NOT_SEND,
                    output=response_message,
                    server_node_id=respondent,
                )
            )
            return False
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
            if metadata.source_node_id is not None:
                respondent = metadata.source_node_id
            else:
                respondent = server_node_id
            status = Status(msg.status)
            response_message = msg.output.tobytes().decode("utf-8", errors="ignore")
        except Exception as e:
            response_message = f"Error sending Command to Node ID {server_node_id}: {e}\n{traceback.format_exc()}"
            with open(EXCEPTIONS_LOGFILE, "a") as f:
                f.write(f"{response_message}\n")
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

    async def send_register_access(
        self, server_node_id: int, register_name: str, register_type: str, register_value: Any
    ) -> bool:
        status: Status = Status.DID_NOT_SEND
        response_message: str = ""
        respondent: int = server_node_id
        node: Node = self.known_nodes[server_node_id]
        if node.registry is None:
            response_message = f"Node ID {server_node_id} has no registry loaded."
            self.results.append(
                make_result(
                    status=Status.DID_NOT_SEND,
                    output=response_message,
                    server_node_id=respondent,
                )
            )
            return False

        found = False
        for reg in node.registry:
            if reg.name == register_name:
                found = True
                break
        if not found:
            response_message = f"Register {register_name} not found on Node ID {server_node_id}."
            self.results.append(
                make_result(
                    status=Status.DID_NOT_SEND,
                    output=response_message,
                    server_node_id=respondent,
                )
            )
            return False
        entry: Register = [reg for reg in node.registry if reg.name == register_name][0]
        client = self.node.make_client(RegisterAccess, server_node_id=server_node_id)
        try:
            if entry.type != "Empty":
                converted_value = ast.literal_eval(register_value)  # Convert string to appropriate type
            else:
                converted_value = None

            if entry.type == "Empty":
                value = Value(empty=Empty())
            elif entry.type == "String":
                value = Value(string=uavcan.primitive.String_1_0(value=converted_value))
            elif entry.type == "Bit":
                value = Value(bit=uavcan.primitive.Boolean_1_0(value=converted_value))
            elif entry.type == "Unstructured":
                value = Value(unstructured=uavcan.primitive.Unstructured_1_0(value=bytes.fromhex(converted_value)))
            elif entry.type == "Integer8":
                value = Value(integer8=uavcan.primitive.array.Integer8_1_0(value=converted_value))
            elif entry.type == "Integer16":
                value = Value(integer16=uavcan.primitive.array.Integer16_1_0(value=converted_value))
            elif entry.type == "Integer32":
                value = Value(integer32=uavcan.primitive.array.Integer32_1_0(value=converted_value))
            elif entry.type == "Integer64":
                value = Value(integer64=uavcan.primitive.array.Integer64_1_0(value=converted_value))
            elif entry.type == "Natural8":
                value = Value(natural8=uavcan.primitive.array.Natural8_1_0(value=converted_value))
            elif entry.type == "Natural16":
                value = Value(natural16=uavcan.primitive.array.Natural16_1_0(value=converted_value))
            elif entry.type == "Natural32":
                value = Value(natural32=uavcan.primitivearray.Natural32_1_0(value=converted_value))
            elif entry.type == "Natural64":
                value = Value(natural64=uavcan.primitive.array.Natural64_1_0(value=converted_value))
            elif entry.type == "Real16":
                value = Value(real16=uavcan.primitive.array.Real16_1_0(value=converted_value))
            elif entry.type == "Real32":
                value = Value(real32=uavcan.primitive.array.Real32_1_0(value=converted_value))
            elif entry.type == "Real64":
                value = Value(real64=uavcan.primitive.array.Real64_1_0(value=converted_value))
            else:
                response_message = (
                    f"Register {register_name} on Node ID {server_node_id} has unknown type {entry.type}."
                )
                self.results.append(
                    make_result(
                        status=Status.DID_NOT_SEND,
                        output=response_message,
                        server_node_id=respondent,
                    )
                )
            request = RegisterAccess.Request(name=uavcan.register.Name_1_0(register_name), value=value)
            response = await client.call(request)
            if response is None:
                # timeout
                status = Status.TIMEOUT
                response_message = f"Command {command} to Node ID {server_node_id} timed out."
                return False  # the finally will still log the status
            msg, metadata = response
            assert isinstance(msg, RegisterAccess.Response)
            if metadata.source_node_id is not None:
                respondent = metadata.source_node_id
            else:
                respondent = server_node_id
            if msg.value == value:
                status = Status.SUCCESS
                response_message = "Register access successful."
            else:
                status = Status.FAILURE
                response_message = "Register access failed: returned value does not match sent value."

        except Exception as e:
            response_message = (
                f"Error sending RegisterAccess to Node ID {server_node_id}: {e}\n{traceback.format_exc()}"
            )
            with open(EXCEPTIONS_LOGFILE, "a") as f:
                f.write(f"{response_message}\n")
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

    async def run(self) -> None:

        ###############################
        # Setup Subscription Callbacks
        ###############################

        def on_heartbeat(msg: Heartbeat, txfr: pycyphal.transport.TransferFrom) -> None:
            if txfr.source_node_id is None:
                return  # can't do much if it's not known
            if txfr.source_node_id not in self.known_nodes.keys():
                self.known_nodes[txfr.source_node_id] = make_cyphal_node(
                    txfr.source_node_id,
                    Health(msg.health.value),
                    Mode(msg.mode.value),
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
            self.receive_event.set()

        self.subscribers["heartbeat"].receive_in_background(on_heartbeat)

        def on_portlist(msg: PortList, txfr: pycyphal.transport.TransferFrom) -> None:
            if txfr.source_node_id in self.known_nodes.keys():
                self.known_nodes[txfr.source_node_id].publishers = self.subjectlist_to_list(msg.publishers)
                self.known_nodes[txfr.source_node_id].subscribers = self.subjectlist_to_list(msg.subscribers)
                self.known_nodes[txfr.source_node_id].clients = self.mask_to_list(msg.clients.mask)
                self.known_nodes[txfr.source_node_id].servers = self.mask_to_list(msg.servers.mask)
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
            self.time_sync.on_receive_previous_time_microseconds(msg.previous_transmission_timestamp_microsecond)
            self.diagnostics.append(
                make_log(
                    timestamp_microseconds=self.time_sync.get_current_time_microseconds(),
                    level=Severity.INFO,
                    message=f"Received previous time microsecond sync from Node ID {txfr.source_node_id}: {msg.previous_transmission_timestamp_microsecond}",
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
                    self.receive_event.set()
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
                    self.receive_event.set()
                    msg, metadata = response
                    assert isinstance(msg, TransportStatistics.Response)
                    self.known_nodes[node_id].delta_emitted = (
                        msg.transfer_statistics.num_emitted - self.known_nodes[node_id].number_emitted
                    )
                    self.known_nodes[node_id].delta_received = (
                        msg.transfer_statistics.num_received - self.known_nodes[node_id].number_received
                    )
                    self.known_nodes[node_id].delta_error = (
                        msg.transfer_statistics.num_errored - self.known_nodes[node_id].number_error
                    )
                    self.known_nodes[node_id].number_emitted = msg.transfer_statistics.num_emitted
                    self.known_nodes[node_id].number_received = msg.transfer_statistics.num_received
                    self.known_nodes[node_id].number_error = msg.transfer_statistics.num_errored
                finally:
                    client.close()
            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
            next_update_at += UPDATE_PERIOD

    def close(self) -> None:
        self.running = False
        self.node.close()
