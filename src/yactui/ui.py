import textual
import asyncio

from typing import Any
from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    Static,
    Tree,
    RichLog,
)
from textual.containers import Vertical
from textual.reactive import Reactive


class CyphalTUI(App):
    """A Textual TUI application for Cyphal network management."""

    root_nodes: Any

    CSS = """
    Screen {
        layout: horizontal;
    }
    #left-pane {
        width: 20%;
        height: 100%;
        padding: 1;
        margin: 1;
        background: $primary;
    }
    #right-pane {
        width: 80%;
        height: 100%;
        padding: 1;
        margin: 1;
        background: $primary;
    }
    #node-tree {
        height: 100%;
        background: $primary-muted;
    }
    #info-viewer {
        height: 70%;
        border: solid $accent;
        background: $secondary;
    }
    #log-viewer {
        height: 30%;
        border: solid $accent;
        background: $secondary;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="left-pane"):  # Container for node tree
            self.node_tree = Tree(id="node-tree", label="Cyphal Nodes")
            self.node_tree.root.expand()
            yield self.node_tree
        with Vertical(id="right-pane"):  # Container for info and log viewers
            yield Static(id="info-viewer")
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

        self.udp_nodes.add_leaf("{}: {}".format(55, "org.cyphal.node.NodeA"))
        self.can_nodes.add_leaf("{}: {}".format(12, "org.cyphal.node.NodeB"))
        self.serial_nodes.add_leaf("{}: {}".format(22, "org.cyphal.node.NodeC"))

        info_viewer = self.query_one("#info-viewer", Static)
        # Set initial content for info and log viewers
        info_viewer.update(
            """
            Select a node to see details here.
            Node Name: {}
            Uptime: {}
            """.format(
                "N/A", float(0.0)
            )
        )

        log_viewer = self.query_one("#log-viewer", RichLog)
        log_viewer.clear()
        log_viewer.write("Subscribed to uavcan.diagnostic.Record...")
        log_viewer.write("Listening for Cyphal network traffic...")

    def refresh_display(self) -> None:
        """Refresh the TUI display."""
        pass

    async def main() -> None:
        """Main entry point for the Cyphal TUI application."""
        UPDATE_PERIOD = 0.5  # seconds
        next_update_at = asyncio.get_running_loop().time()
        while True:
            next_update_at += UPDATE_PERIOD
            await asyncio.sleep(next_update_at - asyncio.get_running_loop().time())
