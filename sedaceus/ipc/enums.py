from enum import Enum, unique


__all__ = (
    "EngineEvents",
    "IPCClassType",
    "IPCPayloadType",
)


class EngineEvents(Enum):
    # Base Engine Events
    ENGINE_READY = "ENGINE_READY"
    ENGINE_CLOSING = "ENGINE_CLOSING"
    # Node addition/removal.
    WS_NODE_ADDED = "WS_NODE_ADDED"
    NODE_ADDED = "NODE_ADDED"
    WS_NODE_REMOVED = "WS_NODE_REMOVED"
    NODE_REMOVED = "NODE_REMOVED"
    # Role addition/removal.
    WS_ROLE_ADDED = "WS_ROLE_ADDED"
    LOCAL_ROLE_ADDED = "LOCAL_ROLE_ADDED"
    ROLE_ADDED = "ROLE_ADDED"
    WS_ROLE_REMOVED = "WS_ROLE_REMOVED"
    LOCAL_ROLE_REMOVED = "LOCAL_ROLE_REMOVED"
    ROLE_REMOVED = "ROLE_REMOVED"
    # Device addition/removal.
    # Packet handling.
    WS_PACKET = "WS_PACKET"
    LOCAL_PACKET = "LOCAL_PACKET"
    PACKET = "PACKET"
    COMMUNICATION = "COMMUNICATION"


@unique
class IPCClassType(Enum):
    ENGINE = 0
    ROLE = 1
    DEVICE = 2


@unique
class IPCPayloadType(Enum):
    COMMUNICATION = 0
    """Valid destination: ENGINE, ROLE, DEVICE


    Packet data: IPCPacket dictionary
    """
    DISCOVERY = 1
    """Valid destination: ENGINE

    Packet data: "Node UUID"
    """
    ROLE_ADD = 2
    """Valid destination: ENGINE

    Packet data: "Role name here"
    """
    ROLE_REMOVE = 3
    """Valid destination: ENGINE

    Packet data: "Role name here"
    """
    DEVICE_ADD = 4
    """Valid destination: ENGINE

    Packet data: {"uuid": "Device UUID Here", "role": "Role name here"}
    """
    DEVICE_REMOVE = 5
    """Valid destination: ENGINE

    Packet data: {"uuid": "Device UUID Here", "role": "Role name here"}
    """