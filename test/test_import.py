import pytest


def test_import_yactui():
    try:
        import yactui  # noqa: F401
    except ImportError as e:
        pytest.fail(f"Importing yactui failed: {e}")


def test_import_yactui_TimeSyncer():
    try:
        from yactui import TimeSyncer  # noqa: F401
    except ImportError as e:
        pytest.fail(f"Importing TimeSyncer from yactui failed: {e}")


def test_import_yactui_exemplar():
    try:
        from yactui.exemplar import ExemplarNode  # noqa: F401
    except ImportError as e:
        pytest.fail(f"Importing exemplar from yactui failed: {e}")
