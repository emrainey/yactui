from curses.ascii import isdigit
import enum
from logging import info
import textual
import asyncio

from typing import Any, Coroutine, Dict, List, Optional, Tuple
from textual import on
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
    DataTable,
    Digits,
    TabPane,
    TabbedContent,
    # UnknownNodeID,
)
from textual.validation import Function, Number, ValidationResult, Validator
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.coordinate import Coordinate

# from textual.reactive import Reactive
from yactui.node import CyphalNode

from yactui.data import Health, Mode, Node, Result, Status, Command


class ValueEditScreen(ModalScreen[str]):
    """A modal screen for editing a register value."""

    BINDINGS = [("escape", "app.pop_screen", "Pop screen")]

    def __init__(self, register_name: str = "") -> None:
        super().__init__()
        self.register_name = register_name

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-pane"):
            yield Static(f"Editing {self.register_name}", id="edit-register-name")
            yield Input(placeholder="new value", id="edit-input")
            with Horizontal(id="edit-button-area"):
                yield Button(label="Submit", id="edit-submit", variant="success")
                yield Button(label="Cancel", id="edit-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the edit modal."""
        if event.button.id == "edit-submit":
            input_widget = self.query_one("#edit-input", Input)
            self.dismiss(input_widget.value)
        elif event.button.id == "edit-cancel":
            self.dismiss(None)


class CyphalTUI(App[int]):
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

    node_tree: Tree[str]
    cyphal_nodes: List[CyphalNode]
    udp_nodes: Any
    can_nodes: Any
    serial_nodes: Any
    nodes: Dict[int, Node]
    node_id: int
    selected_node: Optional[int]  # Uses the Node ID of the selected node in the tree
    verbose: bool = False
    _running: bool
    _freeze: bool
    selected_register_row: Optional[int]
    node_info: Dict[int, Dict[str, Any]]

    CSS_PATH = "yactui.tcss"
    BINDINGS = [("Ctrl+Q", "quit", "Quit the application"), ("F", "freeze", "Freeze/Unfreeze the display")]

    def __init__(self, nodes: List[CyphalNode], verbose: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cyphal_nodes = nodes
        self.node_id = 0
        self.selected_node = None
        self.selected_register_row = None
        self.verbose = verbose
        self._running = True
        self._freeze = False
        self.node_info = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="left-pane"):  # Container for node tree
            self.node_tree = Tree(id="node-tree", label="Networks")
            self.node_tree.root.expand()
            yield self.node_tree
            with Horizontal(id="time-area"):
                with Vertical(id="time-switch-area"):
                    yield Label("Sync:")
                    yield Switch(id="time-switch", value=True)
                with Vertical(id="time-numbers-area"):
                    yield Label("Time (µs):")
                    yield Digits(id="timestamp")
        with Vertical(id="right-pane"):  # Container for info and log viewers
            with TabbedContent(id="data-tabs", initial="GetInfoView"):
                with TabPane("GetInfo", id="GetInfoView"):
                    yield Static(id="info-viewer")
                with TabPane("Registry", id="RegistryView"):
                    yield DataTable(id="register-viewer", zebra_stripes=True)
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
                    yield Label(" ")  # spacer
                    yield Button(label="SEND", id="btn-send", variant="success")
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

        register_viewer = self.query_one("#register-viewer", DataTable)
        register_viewer.add_columns("Name", "Value", "Type", "Default", "Minimum", "Maximum", "Mutable", "Persistent")

        info_viewer = self.query_one("#info-viewer", Static)
        # Set initial content for info and log viewers
        info_viewer.update("<== Select a node to see details here.")
        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.clear()
        log_viewer.write("Subscribed to uavcan.diagnostic.Record...")
        digits = self.query_one("#timestamp", Digits)
        digits.update("0")
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
TX: {node.number_emitted} dTX:{node.delta_emitted} RX: {node.number_received} dRX:{node.delta_received} ERR: {node.number_error} dERR:{node.delta_error}
"""

    def refresh_display(self) -> None:
        """Refresh the TUI display."""
        # Update the node tree with current heartbeats
        digits = self.query_one("#timestamp", Digits)
        digits.update(str(self.cyphal_nodes[0].time_sync.get_current_time_microseconds()))

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
            if self.node_id > 0:
                for cyphal_node in self.cyphal_nodes:
                    if self.node_id in cyphal_node.known_nodes.keys():
                        info_viewer.update(self.node_to_string(cyphal_node.known_nodes[self.node_id]))
                        break

        register_viewer = self.query_one("#register-viewer", DataTable)
        register_viewer.clear()
        for cyphal_node in self.cyphal_nodes:
            for node_id, node in cyphal_node.known_nodes.items():
                if node_id == self.node_id:
                    for register in node.registry:
                        register_viewer.add_row(
                            register.name,
                            str(register.value),
                            register.type,
                            "",  # Default
                            "",  # Minimum
                            "",  # Maximum
                            "Y" if register.mutable else "N",
                            "Y" if register.persistent else "N",
                        )
                    if self.selected_register_row is not None:
                        register_viewer.move_cursor(
                            row=self.selected_register_row, column=0, animate=False, scroll=True
                        )

        log_viewer = self.query_one("#log-viewer", RichLog)
        # do not clear the log! just append new entries
        for cyphal_node in self.cyphal_nodes:
            while len(cyphal_node.diagnostics) > 0:
                log = cyphal_node.diagnostics.popleft()
                log_viewer.write(f"[{log.node_id}][{log.timestamp}] {log.level}: {log.message}")

            while len(cyphal_node.results) > 0:
                result: Result = cyphal_node.results.popleft()
                if result.status == Status.DID_NOT_SEND:
                    log_viewer.write(f"[{result.server_node_id}] Command Result: DID NOT SEND - {result.output}")
                else:
                    log_viewer.write(
                        f"[{result.server_node_id}] Command Result: {result.status.name} - {result.output}"
                    )

    def get_node_id_from_label(self, label: str) -> Optional[int]:
        """Extract the Node ID from a tree node label."""
        if ":" in label:
            node_id_str = label.split(":")[0]
            try:
                return int(node_id_str)
            except ValueError:
                return None
        return None

    # def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
    #     """Handle register row highlight to possibly edit the value."""
    #     log_viewer = self.query_one("#log-viewer", RichLog)
    #     self.selected_register_row = event.coordinate.row
    #     if self.verbose:
    #         log_viewer.write(
    #             f"Highlighted register row: {self.selected_register_row} for Node ID: {self.node_id} @{event.coordinate.row}x{event.coordinate.column}"
    #         )

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle register row selection to possibly edit the value."""
        log_viewer = self.query_one("#log-viewer", RichLog)
        registry_view = self.query_one("#register-viewer", DataTable)
        self.selected_register_row = event.coordinate.row
        if self.verbose:
            log_viewer.write(
                f"Selected register row: {self.selected_register_row} for Node ID: {self.node_id} @{event.coordinate.row}x{event.coordinate.column}"
            )
        register_name: str = registry_view.get_cell_at(Coordinate(event.coordinate.row, 0))
        register_type: str = registry_view.get_cell_at(Coordinate(event.coordinate.row, 2))

        def assign_register_value(value: Optional[str]) -> None:
            if value is None and self.verbose:
                log_viewer.write(">> Register edit cancelled.<<")
                return
            for cyphal_node in self.cyphal_nodes:
                # send the new register value to the node to convert and set it via Cyphal
                if self.node_id in cyphal_node.known_nodes.keys():
                    if self.verbose:
                        log_viewer.write(
                            f"New value for register {self.node_id}:'{register_name}': {register_type} => {value}"
                        )
                    asyncio.create_task(
                        cyphal_node.send_register_access(
                            server_node_id=self.node_id,
                            register_name=register_name,
                            register_type=register_type,
                            register_value=value,
                        )
                    )

        self.push_screen(ValueEditScreen(register_name), assign_register_value)

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
        self.node_id = int(cmd_node_input.value or 0)
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
        log_viewer.write(f"Selected Node ID: {self.node_id}")
        # update the cmd node input box
        cmd_node_input = self.query_one("#cmd-node", Input)
        cmd_node_input.value = str(self.node_id)
        for node in self.cyphal_nodes:
            if self.node_id in node.known_nodes.keys():
                info_viewer.update(self.node_to_string(node.known_nodes[self.node_id]))
                break

    @on(TabbedContent.TabActivated, "#data-tabs")
    def on_data_tabs_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab activation to refresh display."""
        if self.verbose:
            log_viewer = self.query_one("#log-viewer", RichLog)
            log_viewer.write(f"Activated tab: {event.tab.id}, looking for Node ID: {self.node_id}")
        if "RegistryView" in event.tab.id:
            # start the register listing of the selected node
            for node in self.cyphal_nodes:
                if self.node_id in node.known_nodes.keys():
                    asyncio.create_task(node.request_register_list(self.node_id))
                    break

    def action_quit(self) -> None:
        """Action to quit the application."""
        self._running = False

    def action_freeze(self) -> None:
        """Action to freeze/unfreeze the display updates."""
        self._freeze = not self._freeze
        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.write(f"Display {'frozen' if self._freeze else 'unfrozen'}.")

    async def main(self) -> None:
        """Main processing loop for the Cyphal TUI application. Each node provides a receive event which is used to trigger display updates."""
        UPDATE_PERIOD = 0.5  # seconds
        next_update_at = asyncio.get_running_loop().time()
        while self._running:
            if not self._freeze:
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
        for node in self.cyphal_nodes:
            node.close()
        self.exit(0)
