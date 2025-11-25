import time
import pytest
import yactui

from typing import List
from yactui import TimeSyncer, MonotonicClock


class JumpClock(MonotonicClock):
    """A clock that jumps forward by a fixed amount each time it's queried"""

    def __init__(self) -> None:
        self.current_time_microseconds = 0

    def jump(self, jump_microseconds: int) -> None:
        self.jump_microseconds = jump_microseconds
        self.current_time_microseconds += jump_microseconds

    def get_time_microseconds(self) -> int:
        return self.current_time_microseconds


def receive_time_sync(ts: TimeSyncer, basis_seconds: float, count: int, jumper: JumpClock) -> None:
    basis_microseconds = int(basis_seconds * 1_000_000)
    for i in range(count):
        jumper.jump(1_000_000)
        ts.on_receive_previous_time_microseconds(basis_microseconds + (i * 1_000_000))
        now = ts.get_current_time_seconds()
        if ts.is_client():
            current_time_seconds = (basis_microseconds + (i * 1_000_000) + 1_000_000) / 1_000_000.0
            assert now >= current_time_seconds, "Must be at least this value"
        print(f"Received time sync {i}, current time: {now}")


def check_time_with_jumps(ts: TimeSyncer, jumper: JumpClock, jumps: List[int]):
    # the sum of the jumps can't exceed the timeout for the client to remain a client
    assert sum(jumps) < ts.TIMEOUT_MICROSECONDS

    old_time = ts.get_current_time_microseconds()
    for jump in jumps:
        jumper.jump(jump)
        new_time = ts.get_current_time_microseconds()
        assert new_time > old_time
        assert new_time - old_time >= jump
        assert new_time - old_time < jump + 2  # should be close to jump
        old_time = new_time


def test_yactui_TimeSyncer_types():
    jumper = JumpClock()
    ts = TimeSyncer(clock=jumper, client_not_server=True)
    assert isinstance(ts.get_current_time_microseconds(), int)
    assert isinstance(ts.get_current_time_seconds(), float)


def test_yactui_TimeSyncer_as_Client():
    # only use the public methods, don't touch the internal state directly
    jumper = JumpClock()
    # inject our jump clock into the TimeSyncer
    ts = TimeSyncer(clock=jumper, client_not_server=True)
    assert ts.is_client()
    assert not ts.is_server()

    # receive the time syncs
    receive_time_sync(ts, 42.0, 5, jumper)
    old_time = ts.get_current_time_seconds()
    receive_time_sync(ts, 47.0, 1, jumper)
    # get the current time again
    new_time = ts.get_current_time_seconds()
    assert new_time > old_time
    assert new_time - old_time >= 1.0
    assert new_time - old_time < 1.000002  # should be close to 1 second
    old_time = new_time
    # now test if the client switches to server mode after timeout
    jumper.jump(int((ts.TIMEOUT_MICROSECONDS + 1.0) * 1_000_000))
    new_time = ts.get_current_time_seconds()
    assert new_time > old_time
    assert new_time - old_time >= (ts.TIMEOUT_MICROSECONDS + 1.0) / 1_000_000.0
    assert not ts.is_client()
    assert ts.is_server()


def test_yactui_TimeSyncer_as_Server():
    jumper = JumpClock()
    # only use the public methods, don't touch the internal state directly
    ts = TimeSyncer(clock=jumper, client_not_server=False, time_basis_seconds=3.0)
    assert not ts.is_client()
    assert ts.is_server()

    old_time = ts.get_current_time_seconds()
    # sleep for a second
    jumper.jump(123456)
    new_time = ts.get_current_time_seconds()
    assert new_time > old_time
    assert new_time - old_time >= 0.123456
    assert new_time - old_time < 0.123458  # should be close to 0.123456 seconds
    # now simulate receiving time syncs to switch to client mode
    receive_time_sync(ts, 42.0, ts.SERVER_LIMIT + ts.CLIENT_LIMIT, jumper)
    assert ts.is_client()
    assert not ts.is_server()
    # after switching to client, receiving time syncs should update the time basis
    # we should not compare two time basis values directly, measure again
    ts.on_receive_previous_time_microseconds(47_000_000)  # time is now 49 seconds
    jumper.jump(1_000_000)
    ts.on_receive_previous_time_microseconds(48_000_000)  # time is now 50 seconds
    jumper.jump(500_000)
    old_time = ts.get_current_time_seconds()
    jumper.jump(500_000)
    ts.on_receive_previous_time_microseconds(49_000_000)  # time is now 51 seconds
    jumper.jump(500_000)
    new_time = ts.get_current_time_seconds()
    assert new_time - old_time >= 1.0
    assert new_time - old_time < 1.000002  # should be close to 1 second

    check_time_with_jumps(ts, jumper, [100_000, 200_000, 300_000, 400_000, 500_000])  # total 1.5 seconds
