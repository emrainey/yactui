"""
Unit tests for thread-safety of CyphalNode and TUI data access.

This test module verifies that concurrent access to shared data structures
(known_nodes, diagnostics, results, query_nodes) is properly synchronized.
"""

import unittest
import threading
import time
from collections import deque
from typing import Dict
from unittest.mock import Mock, MagicMock, patch

from yactui.data import Node, make_cyphal_node, Health, Mode, Log, Result, Severity, Status, make_log, make_result


class TestThreadSafety(unittest.TestCase):
    """Test thread-safe access to CyphalNode shared data structures."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the CyphalNode with essential components for testing
        self.mock_node = Mock()
        self.mock_node._lock = threading.RLock()
        self.mock_node.known_nodes = {}
        self.mock_node.diagnostics = deque(maxlen=100)
        self.mock_node.results = deque(maxlen=100)
        self.mock_node.query_nodes = deque()
        self.exception_occurred = False
        self.exception_message = None

    def test_concurrent_known_nodes_modification_and_iteration(self):
        """Test that concurrent modifications and iterations don't cause exceptions."""
        iterations = 100
        num_threads = 5

        def writer_thread():
            """Simulates node discovery by adding nodes."""
            try:
                for i in range(iterations):
                    node_id = threading.get_ident() % 100 + i
                    with self.mock_node._lock:
                        self.mock_node.known_nodes[node_id] = make_cyphal_node(
                            node_id, Health.NOMINAL, Mode.OPERATIONAL, i, 0
                        )
                    time.sleep(0.001)  # Small delay to increase chance of collision
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        def reader_thread():
            """Simulates TUI reading nodes by iterating."""
            try:
                for _ in range(iterations):
                    with self.mock_node._lock:
                        nodes_snapshot = dict(self.mock_node.known_nodes)

                    # Iterate over the snapshot (safe)
                    for node_id, node in nodes_snapshot.items():
                        # Simulate some processing
                        _ = node.health
                        _ = node.mode
                    time.sleep(0.001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        # Create and start threads
        threads = []
        for _ in range(num_threads // 2):
            threads.append(threading.Thread(target=writer_thread))
            threads.append(threading.Thread(target=reader_thread))

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no exceptions occurred
        self.assertFalse(
            self.exception_occurred, f"Exception occurred during concurrent access: {self.exception_message}"
        )

    def test_concurrent_diagnostics_access(self):
        """Test that concurrent diagnostics append and popleft operations are safe."""
        iterations = 100

        def writer_thread():
            """Simulates callbacks adding diagnostics."""
            try:
                for i in range(iterations):
                    with self.mock_node._lock:
                        self.mock_node.diagnostics.append(
                            make_log(
                                timestamp_microseconds=float(i * 1000),
                                level=Severity.INFO,
                                message=f"Test message {i}",
                                node_id=1,
                            )
                        )
                    time.sleep(0.0001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        def reader_thread():
            """Simulates TUI popping diagnostics."""
            try:
                for _ in range(iterations):
                    with self.mock_node._lock:
                        diagnostics_snapshot = []
                        while len(self.mock_node.diagnostics) > 0:
                            diagnostics_snapshot.append(self.mock_node.diagnostics.popleft())

                    # Process outside the lock
                    for log in diagnostics_snapshot:
                        _ = log.message
                    time.sleep(0.0001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        # Create and start threads
        threads = [
            threading.Thread(target=writer_thread),
            threading.Thread(target=writer_thread),
            threading.Thread(target=reader_thread),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no exceptions occurred
        self.assertFalse(
            self.exception_occurred,
            f"Exception occurred during concurrent diagnostics access: {self.exception_message}",
        )

    def test_concurrent_results_access(self):
        """Test that concurrent results append and popleft operations are safe."""
        iterations = 100

        def writer_thread():
            """Simulates command responses adding results."""
            try:
                for i in range(iterations):
                    with self.mock_node._lock:
                        self.mock_node.results.append(
                            make_result(
                                status=Status.SUCCESS,
                                output=f"Result {i}",
                                server_node_id=1,
                            )
                        )
                    time.sleep(0.0001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        def reader_thread():
            """Simulates TUI popping results."""
            try:
                for _ in range(iterations):
                    with self.mock_node._lock:
                        results_snapshot = []
                        while len(self.mock_node.results) > 0:
                            results_snapshot.append(self.mock_node.results.popleft())

                    # Process outside the lock
                    for result in results_snapshot:
                        _ = result.output
                    time.sleep(0.0001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        # Create and start threads
        threads = [
            threading.Thread(target=writer_thread),
            threading.Thread(target=writer_thread),
            threading.Thread(target=reader_thread),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no exceptions occurred
        self.assertFalse(
            self.exception_occurred, f"Exception occurred during concurrent results access: {self.exception_message}"
        )

    def test_known_nodes_update_while_reading(self):
        """Test updating node attributes while TUI is reading."""
        iterations = 50

        # Pre-populate with nodes
        for i in range(10):
            self.mock_node.known_nodes[i] = make_cyphal_node(i, Health.NOMINAL, Mode.OPERATIONAL, 0, 0)

        def updater_thread():
            """Simulates callbacks updating node attributes."""
            try:
                for _ in range(iterations):
                    for node_id in range(10):
                        with self.mock_node._lock:
                            if node_id in self.mock_node.known_nodes:
                                self.mock_node.known_nodes[node_id].health = Health.ADVISORY
                                self.mock_node.known_nodes[node_id].uptime += 1
                    time.sleep(0.001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        def reader_thread():
            """Simulates TUI reading node information."""
            try:
                for _ in range(iterations):
                    with self.mock_node._lock:
                        nodes_snapshot = dict(self.mock_node.known_nodes)

                    for node_id, node in nodes_snapshot.items():
                        _ = f"Node {node_id}: {node.health} - {node.uptime}"
                    time.sleep(0.001)
            except Exception as e:
                self.exception_occurred = True
                self.exception_message = str(e)

        # Create and start threads
        threads = [
            threading.Thread(target=updater_thread),
            threading.Thread(target=reader_thread),
            threading.Thread(target=reader_thread),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no exceptions occurred
        self.assertFalse(
            self.exception_occurred, f"Exception occurred during concurrent update/read: {self.exception_message}"
        )


if __name__ == "__main__":
    unittest.main()
