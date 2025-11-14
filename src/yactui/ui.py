import enum
from logging import info
import textual
import asyncio

from typing import Any, Dict, List, Tuple
from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    Static,
    Tree,
    RichLog,
    Input,
)
from textual.containers import Vertical, Horizontal

# from textual.reactive import Reactive
from .node import CyphalNode

from .data import Health, Mode, Node


class CyphalTUI(App):
    """
    A Textual TUI application for Cyphal network management.
    The Network Tree Displays known nodes on the network based on received heartbeats per each transport.
    Network:
    -> Cyphal/UDP (Node ID: Node Name)
        -> Node ID: Node Name
    -> Cyphal/CAN (Node ID: Node Name)
        -> Node ID: Node Name
    -> Cyphal/Serial (Node ID: Node Name)
        -> Node ID: Node Name

    """

    node_tree: Tree
    cyphal_nodes: List[CyphalNode]
    udp_nodes: Any
    can_nodes: Any
    serial_nodes: Any
    nodes: Dict[int, Node]
    node_id: int

    CSS_PATH = "yactui.tcss"
    BINDINGS = [("q", "quit", "Quit the application")]

    def __init__(self, nodes: List[CyphalNode], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cyphal_nodes = nodes
        self.node_id = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="left-pane"):  # Container for node tree
            self.node_tree = Tree(id="node-tree", label="Networks")
            self.node_tree.root.expand()
            yield self.node_tree
        with Vertical(id="right-pane"):  # Container for info and log viewers
            yield Static(id="info-viewer")
            with Horizontal(id="command-bar"):
                yield Input("Node ID:", id="cmd-node")
                yield Input("Command:", id="cmd")
                yield Input("Args:", id="cmd-args")
            yield RichLog(id="log-viewer")
        yield Footer()

    async def on_mount(self):
        self.node_tree.clear()
        # Populate the node tree with dummy data for demonstration
        # add a branch for Cyphal/UDP
        self.udp_nodes = self.node_tree.root.add("Cyphal/UDP", expand=True)
        # add a branch for Cyphal/CAN
        self.can_nodes = self.node_tree.root.add("Cyphal/CAN", expand=True)
        # add a branch for Cyphal/Serial
        self.serial_nodes = self.node_tree.root.add("Cyphal/Serial", expand=True)

        info_viewer = self.query_one("#info-viewer", Static)
        # Set initial content for info and log viewers
        info_viewer.update("\n\tSelect a node to see details here.")
        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.clear()
        # log_viewer.write("Subscribed to uavcan.diagnostic.Record...")
        # log_viewer.write("Listening for Cyphal network traffic...")
        asyncio.create_task(self.main())

    def heartbeat_to_string(self, node: Node) -> str:
        return f"{node.node_id}: Health: {node.health}, Mode: {node.mode}, Uptime: {node.uptime} VSSC: {hex(node.vendor_specific_status_code)}"

    def node_to_string(self, node: Node) -> str:
        return f"""
    Node ID: {node.node_id}
    Name: {node.name}
    Software Version: {node.software_version[0]}.{node.software_version[1]}
    Hardware Version: {node.hardware_version[0]}.{node.hardware_version[1]}
    Revision: {hex(node.revision)}
    CRC64WE: {hex(node.crc64we)}
    UUID: {node.unique_id.hex()}
    Publishers: {node.publishers}
    Subscribers: {node.subscribers}
    Clients: {node.clients}
    Servers: {node.servers}
"""

    def refresh_display(self) -> None:
        """Refresh the TUI display."""
        # Update the node tree with current heartbeats
        self.udp_nodes.remove_children()
        self.can_nodes.remove_children()
        self.serial_nodes.remove_children()
        for cyphal_node in self.cyphal_nodes:
            for node_id, node in cyphal_node.known_nodes.items():
                item = self.heartbeat_to_string(node)
                subnet = None
                if cyphal_node.transport == "UDP":
                    subnet = self.udp_nodes
                elif cyphal_node.transport == "CAN":
                    subnet = self.can_nodes
                elif cyphal_node.transport == "Serial":
                    subnet = self.serial_nodes
                else:
                    continue
                existing = []
                for leaf in subnet.children:
                    label = str(leaf.label)
                    if label.startswith(f"{node_id}:"):
                        existing.append(leaf)
                if not any(existing):
                    subnet.add_leaf(item)
        self.node_tree.refresh()

        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.clear()
        for cyphal_node in self.cyphal_nodes:
            if cyphal_node.diagnostics:
                for node_id, logs in cyphal_node.diagnostics.items():
                    if self.node_id and node_id:
                        for log in logs:
                            log_viewer.write(
                                f"[{log.timestamp_microseconds}] {log.level}: {log.message}"
                            )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection in the tree to display node details."""
        # log_viewer = self.query_one("#log-viewer", RichLog)
        info_viewer = self.query_one("#info-viewer", Static)

        label = str(event.node.label)
        # log_viewer.write(f"Selected node: {label}")
        if ":" in label:
            node_id_str = label.split(":")[0]
            try:
                self.node_id = int(node_id_str)
            except ValueError:
                return
            for node in self.cyphal_nodes:
                if self.node_id in node.known_nodes.keys():
                    info_viewer.update(
                        self.node_to_string(node.known_nodes[self.node_id])
                    )
                    break

    def action_quit(self) -> None:
        """Action to quit the application."""
        for node in self.cyphal_nodes:
            node.close()
        self.exit()

    async def main(self) -> None:
        """Main processing loop for the Cyphal TUI application. Each node provides a receive event which is used to trigger display updates."""
        UPDATE_PERIOD = 0.5  # seconds
        next_update_at = asyncio.get_running_loop().time()
        while True:
            # TODO wait for something to happen on all nodes, gather?
            await self.cyphal_nodes[0].receive_event.wait()
            # clear the event (seems like this should be done automatically? this will be a race condition otherwise)
            self.cyphal_nodes[0].receive_event.clear()
            # display update
            self.refresh_display()
            # let some time pass
            next_update_at += UPDATE_PERIOD
            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
