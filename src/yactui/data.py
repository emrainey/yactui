import enum
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any

# We re-define these here to avoid a dependency on pycyphal in data.py
# although this is somewhat redundant with node.py and the pycyphal definitions.


@enum.unique
class Health(enum.IntEnum):
    """Health status for heartbeat publisher"""

    NOMINAL = 0
    ADVISORY = 1
    CAUTION = 2
    WARNING = 3


@enum.unique
class Mode(enum.IntEnum):
    """Mode status for heartbeat publisher"""

    OPERATIONAL = 0
    INITIALIZATION = 1
    MAINTENANCE = 2
    SOFTWARE_UPDATE = 3


@enum.unique
class Severity(enum.IntEnum):
    """Severity levels for diagnostic records"""

    TRACE = 0
    DEBUG = 1
    INFO = 2
    NOTICE = 3
    WARNING = 4
    ERROR = 5
    CRITICAL = 6
    ALERT = 7


@enum.unique
class Command(enum.IntEnum):
    """Command codes for ExecuteCommand service"""

    RESTART = 65535  # Restart the node
    POWER_OFF = 65534  # Power off or shut down, but don't restart
    BEGIN_SOFTWARE_UPDATE = 65533  # Prepare for software update
    FACTORY_RESET = 65532  # Reset to factory defaults
    EMERGENCY_STOP = 65531  # Immediate cessation of all activity that moves
    STORE_PERSISTENT_STATES = 65530  # Save current config to NVM
    IDENTIFY = 65529  # e.g. blink lights


@enum.unique
class Status(enum.IntEnum):
    """Status codes for ExecuteCommand service response"""

    TIMEOUT = -2  # Used internally when a timeout occurs
    DID_NOT_SEND = -1  # Used internally when no request was sent
    SUCCESS = 0
    FAILURE = 1
    NOT_AUTHORIZED = 2
    BAD_COMMAND = 3
    BAD_PARAMETER = 4
    BAD_STATE = 5
    INTERNAL_ERROR = 6


@dataclass
class Node:
    node_id: int
    name: Optional[str]
    health: Health
    mode: Mode
    uptime: int  # seconds
    vendor_specific_status_code: int
    software_version: Tuple[int, int]
    hardware_version: Tuple[int, int]
    revision: int  # 64 bit number, hexadecimal
    crc64we: List[int]  # 64 bit number, hexadecimal
    unique_id: bytes  # 16 bytes
    certificate: bytes  # variable length
    publishers: List[int]  # List of Subject IDs
    subscribers: List[int]  # List of Subject IDs
    clients: List[int]  # List of Service IDs
    servers: List[int]  # List of Service IDs
    number_emitted: int = 0
    number_received: int = 0
    number_error: int = 0


def make_cyphal_node(
    id: int = 0,
    health: Health = Health.NOMINAL,
    mode: Mode = Mode.OPERATIONAL,
    uptime: int = 0,
    vssc: int = 0,
) -> Node:
    return Node(
        node_id=id,
        name=None,
        health=health,
        mode=mode,
        uptime=uptime,
        vendor_specific_status_code=vssc,
        software_version=(0, 0),
        hardware_version=(0, 0),
        revision=0,
        crc64we=[0],
        unique_id=b"",
        certificate=b"",
        publishers=[],
        subscribers=[],
        clients=[],
        servers=[],
    )


@dataclass
class Log:
    timestamp: float
    level: Severity
    message: str
    node_id: int


def make_log(
    timestamp_microseconds: float = 0.0,
    level: Severity = Severity.INFO,
    message: str = "",
    node_id: int = 0,
) -> Log:
    return Log(
        timestamp=timestamp_microseconds / 1e6,
        level=level,
        message=message,
        node_id=node_id,
    )


@dataclass
class Result:
    status: Status
    output: str
    server_node_id: int


def make_result(
    status: Status = Status.SUCCESS,
    output: str = "",
    server_node_id: int = 0,
) -> Result:
    return Result(
        status=status,
        output=output,
        server_node_id=server_node_id,
    )
