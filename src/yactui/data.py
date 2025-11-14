import enum
from dataclasses import dataclass
from typing import Tuple, List, Dict, Any


class Health(enum.IntEnum):
    """Health status for heartbeat publisher"""

    NOMINAL = 0
    ADVISORY = 1
    CAUTION = 2
    WARNING = 3


class Mode(enum.IntEnum):
    """Mode status for heartbeat publisher"""

    OPERATIONAL = 0
    INITIALIZATION = 1
    MAINTENANCE = 2
    SOFTWARE_UPDATE = 3


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


@dataclass
class Node:
    node_id: int
    name: str
    health: Health
    mode: Mode
    uptime: int  # seconds
    vendor_specific_status_code: int
    software_version: Tuple[int, int]
    hardware_version: Tuple[int, int]
    revision: int  # 64 bit number, hexadecimal
    crc64we: int  # 64 bit number, hexadecimal
    unique_id: bytes  # 16 bytes
    certificate: bytes  # variable length
    publishers: List[int]  # List of Subject IDs
    subscribers: List[int]  # List of Subject IDs
    clients: List[int]  # List of Service IDs
    servers: List[int]  # List of Service IDs


def make_cyphal_node(
    id: int = 0,
    health: Health = Health.NOMINAL,
    mode: Mode = Mode.OPERATIONAL,
    uptime: int = 0,
    vssc: int = 0,
) -> Node:
    return Node(
        node_id=id,
        name="",
        health=health,
        mode=mode,
        uptime=uptime,
        vendor_specific_status_code=vssc,
        software_version=(0, 0),
        hardware_version=(0, 0),
        revision=0,
        crc64we=0,
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
