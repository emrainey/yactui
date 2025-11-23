from curses.ascii import isdigit
import enum
from logging import info
import textual
import asyncio

from typing import Any, Dict, List, Optional, Tuple
from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    Static,
    Tree,
    RichLog,
    Input,
    Label,
    Button,
    Switch,
    # UnknownNodeID,
)
from textual.validation import Function, Number, ValidationResult, Validator
from textual.containers import Vertical, Horizontal

# from textual.reactive import Reactive
from .node import CyphalNode

from .data import Health, Mode, Node, Status, Command


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
    selected_node: Optional[int]  # Uses the Node ID of the selected node in the tree
    verbose: bool = False

    CSS_PATH = "yactui.tcss"
    BINDINGS = [("q", "quit", "Quit the application")]

    def __init__(self, nodes: List[CyphalNode], verbose: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cyphal_nodes = nodes
        self.node_id = 0
        self.selected_node = None
        self.verbose = verbose

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="left-pane"):  # Container for node tree
            self.node_tree = Tree(id="node-tree", label="Networks")
            self.node_tree.root.expand()
            yield self.node_tree
        with Vertical(id="right-pane"):  # Container for info and log viewers
            yield Static(id="info-viewer")
            with Horizontal(id="command-bar"):
                with Vertical(id="cmd-node-area"):
                    yield Label("Node ID:")
                    yield Input(
                        placeholder="int 1-127",
                        id="cmd-node",
                        validate_on=["changed"],
                        validators=[Number(minimum=1, maximum=127)],
                    )
                with Vertical(id="cmd-command-area"):
                    yield Label("Command:")
                    yield Input(
                        placeholder="int or word",
                        id="cmd",
                        validate_on=["changed"],
                        validators=[],
                    )
                with Vertical(id="cmd-args-area"):
                    yield Label("Args:")
                    yield Input(
                        id="cmd-args",
                        placeholder="optional arguments",
                        valid_empty=True,
                    )
                with Vertical(id="cmd-send-area"):
                    yield Label(" ")
                    yield Button(label="Send", id="btn-send", variant="success")
                with Vertical(id="time-switch-area"):
                    yield Label("TimeSync:")
                    yield Switch(id="time-switch", value=True)
            yield RichLog(id="log-viewer")
        yield Footer()

    async def on_mount(self) -> None:
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
        info_viewer.update("<== Select a node to see details here.")
        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.clear()
        log_viewer.write("Subscribed to uavcan.diagnostic.Record...")
        asyncio.create_task(self.main())

    def heartbeat_to_string(self, node: Node) -> str:
        return f"{node.node_id}: Health: {node.health}, Mode: {node.mode}, Uptime: {node.uptime} VSSC: {hex(node.vendor_specific_status_code)}"

    def node_to_string(self, node: Node) -> str:
        return f"""Node ID: {node.node_id}
Name: {node.name}
Software Version: {node.software_version[0]}.{node.software_version[1]}
Hardware Version: {node.hardware_version[0]}.{node.hardware_version[1]}
Revision: {hex(node.revision)}
CRC64WE: {hex(node.crc64we[0]) if len(node.crc64we) > 0 else 'N/A'}
UUID: {node.unique_id.hex()}
Publishers: {node.publishers}
Subscribers: {node.subscribers}
Clients: {node.clients}
Servers: {node.servers}
TX: {node.number_emitted} RX: {node.number_received} ERR: {node.number_error}
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
                if cyphal_node.transport_type == "UDP":
                    subnet = self.udp_nodes
                elif cyphal_node.transport_type == "CAN":
                    subnet = self.can_nodes
                elif cyphal_node.transport_type == "Serial":
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

        if self.selected_node is not None:
            # Update info viewer for selected Tree node
            info_viewer = self.query_one("#info-viewer", Static)
            # convert the label to a Cyphal node ID (different)
            try:
                node = self.node_tree.get_node_by_id(self.selected_node)
                label = node.label()
                self.node_id = self.get_node_id_from_label(label) or 0
            except Exception as e:
                label = ""
                self.node_id = 0
            if self.node_id > 0:
                for cyphal_node in self.cyphal_nodes:
                    if self.node_id in cyphal_node.known_nodes.keys():
                        info_viewer.update(self.node_to_string(cyphal_node.known_nodes[self.node_id]))
                        break

        log_viewer = self.query_one("#log-viewer", RichLog)
        # do not clear the log! just append new entries
        for cyphal_node in self.cyphal_nodes:
            while len(cyphal_node.diagnostics) > 0:
                log = cyphal_node.diagnostics.popleft()
                log_viewer.write(f"[{log.node_id}][{log.timestamp}] {log.level}: {log.message}")

            while len(cyphal_node.results) > 0:
                result = cyphal_node.results.popleft()
                if result.status == Status.DID_NOT_SEND:
                    log_viewer.write(f"[{result.server_node_id}] Command Result: DID NOT SEND")
                else:
                    log_viewer.write(f"[{result.server_node_id}] Command Result: {result.status} - {result.output}")

    def get_node_id_from_label(self, label: str) -> Optional[int]:
        """Extract the Node ID from a tree node label."""
        if ":" in label:
            node_id_str = label.split(":")[0]
            try:
                return int(node_id_str)
            except ValueError:
                return None
        return None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Submitted Command Input"""
        input_id = event.input.id
        input_value = event.value.strip()

        if input_id == "cmd-node":
            self.node_id = int(input_value)
        elif input_id == "cmd":
            self.command = input_value
        elif input_id == "cmd-args":
            self.command_args = input_value

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Handle Time Sync Switch Changed"""
        if event.switch.id != "time-switch":
            return
        time_sync_enabled = event.value
        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.write(f"Time Synchronization {'enabled' if time_sync_enabled else 'disabled'}.")
        for cyphal_node in self.cyphal_nodes:
            cyphal_node.time_sync_enabled = time_sync_enabled

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Send Button Pressed"""
        log_viewer = self.query_one("#log-viewer", RichLog)
        if event.button.id != "btn-send":
            return
        # get the node, command, and args from the input fields
        cmd_node_input = self.query_one("#cmd-node", Input)
        cmd_input = self.query_one("#cmd", Input)
        cmd_args_input = self.query_one("#cmd-args", Input)
        self.node_id = int(cmd_node_input.value)
        cmd = cmd_input.value.strip()
        try:
            self.command = int(cmd)
        except ValueError:
            self.command = cmd  # as str
        self.command_args = cmd_args_input.value.strip()
        if self.verbose:
            log_viewer.write(f"Button '{event.button.id}' pressed. Node Id: {self.node_id}")
        # Send command to the selected node to the node which knows it
        for cyphal_node in self.cyphal_nodes:
            if self.node_id in cyphal_node.known_nodes.keys():
                if self.verbose:
                    log_viewer.write(
                        f"Sending command '{self.command}' with args '{self.command_args}' to Node ID {self.node_id} via {cyphal_node.transport_type}"
                    )
                asyncio.create_task(
                    cyphal_node.send_command(
                        server_node_id=self.node_id,
                        command=self.command,
                        args=self.command_args,
                    )
                )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection in the tree to display node details."""
        log_viewer = self.query_one("#log-viewer", RichLog)
        info_viewer = self.query_one("#info-viewer", Static)

        label = str(event.node.label)
        self.selected_node = event.node.id
        if self.verbose:
            log_viewer.write(f"Selected node: {label}:{self.selected_node}")
        self.node_id = self.get_node_id_from_label(label) or 0
        # update the cmd node input box
        cmd_node_input = self.query_one("#cmd-node", Input)
        cmd_node_input.value = str(self.node_id)
        for node in self.cyphal_nodes:
            if self.node_id in node.known_nodes.keys():
                info_viewer.update(self.node_to_string(node.known_nodes[self.node_id]))
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
            # display update
            self.refresh_display()
            # let some time pass
            next_update_at += UPDATE_PERIOD
            await asyncio.gather(
                *[node.receive_event.wait() for node in self.cyphal_nodes],
                asyncio.sleep(next_update_at - asyncio.get_running_loop().time()),
            )
            for node in self.cyphal_nodes:
                if node.receive_event.is_set():
                    node.receive_event.clear()
