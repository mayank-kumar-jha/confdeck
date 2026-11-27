
import pytest

from confdeck.media import spawn, terminate


async def test_owned_child_shutdown_is_bounded_and_reaped():
    import sys

    process = await spawn(sys.executable, "-c", "import time; time.sleep(30)")
    await terminate(process)
    assert process.returncode is not None
    await terminate(process)  # idempotent


async def test_duplicate_instance_lock(tmp_path):
    from confdeck.cli import InstanceLock

    first = InstanceLock(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path)
    finally:
        first.close()
    replacement = InstanceLock(tmp_path)
    replacement.close()
