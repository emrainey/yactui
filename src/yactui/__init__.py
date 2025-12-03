from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Union
import logging

import pycyphal
import pycyphal.transport.udp
import pycyphal.transport.can
import pycyphal.transport.serial

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

EXCEPTIONS_LOGFILE = "yactui-exceptions.log"


class MonotonicClock(ABC):
    """Abstract base class for a monotonic clock"""

    @abstractmethod
    def get_time_microseconds(self) -> int:
        """Get the current time in microseconds"""
        pass


class TimeSyncer:
    """
    Time synchronization mechanism. It itself is not a monotonic clock, as it can switch between
    being a client or a server. As a client, it receives time syncs and computes the time basis.
    As a server, it serves time monotonically based on its time basis.
    """

    # The number of received time sync calls
    received_time_sync_count: int
    # The number of computed time sync calls
    computed_time_sync_count: int
    # The time in monotonic units when we last computed the time basis
    last_computed_monotonic_microseconds: int
    # The time in monotonic units when we last received the time sync
    last_received_monotonic_microseconds: int
    # The computed time basis (time at last sync)
    time_basis_microseconds: int
    # Is this a server or a client?
    time_sync_server: bool
    # The monotonic clock to use
    clock: MonotonicClock

    TIMEOUT_MICROSECONDS: int = 30_000_000  # microseconds
    SERVER_LIMIT: int = 3  # number of syncs before switching modes
    CLIENT_LIMIT: int = 1  # number of syncs needed before a new basis is computed

    def __init__(self, clock: MonotonicClock, client_not_server: bool = True, time_basis_seconds: float = 0.0) -> None:
        """
        Constructor
        :param client_not_server: Whether to start as a client (True) or server (False)
        :param time_basis_seconds: The initial time basis in seconds (only used if server)
        """
        self.clock = clock
        now = self.clock.get_time_microseconds()
        if client_not_server:
            logger.debug("Initialized as Time Sync Client")
            self.time_sync_server = False
            self.time_basis_microseconds = 0  # this is what we'll figure out later
        else:
            logger.debug("Initialized as Time Sync Server")
            self.time_sync_server = True
            self.time_basis_microseconds = self._seconds_to_microseconds(time_basis_seconds)
        self.last_computed_monotonic_microseconds = now
        self.last_received_monotonic_microseconds = now
        self.computed_time_sync_count = 0
        self.received_time_sync_count = 0

    def _seconds_to_microseconds(self, seconds: float) -> int:
        """Convert seconds to microseconds"""
        return int(seconds * 1_000_000)

    def _microseconds_to_seconds(self, microseconds: int) -> float:
        """Convert microseconds to seconds"""
        return microseconds / 1_000_000.0

    def get_current_time_microseconds(self) -> int:
        """Get the current time based on the difference between now and last_computed_monotonic_microseconds plus the know time basis"""
        now: int = self.clock.get_time_microseconds()
        # if we're a client and we haven't receive any time syncs within the timeout, become a server
        diff = now - self.last_received_monotonic_microseconds
        if not self.time_sync_server and diff > self.TIMEOUT_MICROSECONDS:
            # this means we'll ignore up to LIMIT received time syncs before switching back to client mode
            logger.debug("No time syncs received, switching to Time Sync Server mode")
            self.time_sync_server = True
            # reset the received time sync count to zero so that we can switch back to client mode later if we begin receiving time syncs again
            self.received_time_sync_count = 0
            # don't reset the time basis, just start serving time from what we have
        latest_time_basis_modification_microseconds = max(
            self.last_computed_monotonic_microseconds, self.last_received_monotonic_microseconds
        )
        delta: int = now - latest_time_basis_modification_microseconds
        self.time_basis_microseconds += delta
        self.last_computed_monotonic_microseconds = now
        self.computed_time_sync_count += 1
        return self.time_basis_microseconds

    def get_current_time_seconds(self) -> float:
        """Get the current time in seconds"""
        return self._microseconds_to_seconds(self.get_current_time_microseconds())

    def on_receive_previous_time_microseconds(self, previous_timestamp_microseconds: int) -> None:
        """
        Handle a received previous time synchronization value in microseconds,
        computing the basis from the difference of the last received time and now
        """
        now = self.clock.get_time_microseconds()
        self.received_time_sync_count += 1
        # if the server receives this a certain number of times, it switches to being a client
        if self.time_sync_server:
            if self.received_time_sync_count > self.SERVER_LIMIT:
                logger.debug("Switching to Time Sync Client mode")
                self.time_sync_server = False
                # if we're a client, we can't pretend to know the time basis and it _has_ to be computed from received time syncs
                self.time_basis_microseconds = 0
            else:
                # ignore the received time syncs until we exceed the limit
                logger.debug("Ignoring received time sync as we are still a server")
        # we could have switched modes above, so only compute the time basis
        if not self.time_sync_server:
            if self.received_time_sync_count >= self.CLIENT_LIMIT:
                # Compute the time delta since we last received a time sync, without this we'll just report the basis
                delta: int = now - self.last_received_monotonic_microseconds
                # Compute the new time basis
                self.time_basis_microseconds = previous_timestamp_microseconds + delta
                logger.debug("Updated time base to: %d", self.time_basis_microseconds)
        self.last_received_monotonic_microseconds = now

    def is_server(self) -> bool:
        """Return whether we are currently a server"""
        return self.time_sync_server

    def is_client(self) -> bool:
        """Return whether we are currently a client"""
        return not self.time_sync_server


def make_transport(ip: Optional[str], inf: Optional[str], mtu: int, node_id: int) -> tuple[
    str,
    Union[
        pycyphal.transport.udp.UDPTransport,
        pycyphal.transport.can.CANTransport,
        pycyphal.transport.serial.SerialTransport,
    ],
]:
    """Create a Cyphal transport based on the given parameters"""
    transport: Union[
        pycyphal.transport.udp.UDPTransport,
        pycyphal.transport.can.CANTransport,
        pycyphal.transport.serial.SerialTransport,
    ]
    if ip is not None and ip != "":
        transport_type = "UDP"
        transport = pycyphal.transport.udp.UDPTransport(
            local_ip_address=ip,
            local_node_id=node_id,
            mtu=mtu,
        )
    elif inf is not None and "can" in inf:
        # TODO UNTESTED!
        transport_type = "CAN"
        transport = pycyphal.transport.can.CANTransport(
            media=inf,  # TODO this is not correct! Must supply a CAN media object with timings
            local_node_id=node_id,
        )
    elif inf is not None:
        # TODO UNTESTED!
        transport_type = "Serial"
        transport = pycyphal.transport.serial.SerialTransport(
            serial_port=inf,
            local_node_id=node_id,
            mtu=mtu,
        )
    else:
        raise ValueError("Either ip or inf must be provided to create a transport")
    return transport_type, transport


@dataclass
class NodeStatistics:
    """Holds the statistics for a Cyphal Node but without transport-specific details"""

    number_emitted: int = 0
    number_received: int = 0
    number_error: int = 0
