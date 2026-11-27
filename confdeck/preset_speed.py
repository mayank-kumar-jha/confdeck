"""Explicitly confirmed experiment using two already-saved reserved presets."""
import asyncio

from confdeck.visca import ViscaIpDriver


async def speed_test(camera, origin, destination):
    slots = (origin, destination)
    if origin == destination or any(not str(s).isdigit() or not camera.preset_start <= int(s) <= camera.preset_end or s not in camera.preset_records for s in slots):
        raise ValueError("Select two different, already-saved presets inside the reserved range.")
    driver = ViscaIpDriver(camera)
    if driver.canon:
        return {"honored": False, "reason": "Canon preset speed/time is set on the camera page."}
    camera.preset_speed_verified = False
    rows = []
    stage = "initial position inquiry"
    speed = None
    positions = {}
    moved = False
    try:
        await driver.position()  # Fail before movement if either inquiry is unavailable.
        stage = "initial origin recall"
        moved = True
        await driver.recall(origin)
        stage = "initial origin settling"
        a = (await driver.settled())["position"]
        positions["origin"] = a
        stage = "normal destination recall"
        start = asyncio.get_running_loop().time()
        await driver.recall(destination)
        stage = "normal destination settling"
        b = (await driver.settled())["position"]
        positions["destination"] = b
        rows.append({"mode": "normal recall", "ms": (asyncio.get_running_loop().time()-start)*1000})
        if a[:2] == b[:2]:
            return {"honored": False, "reason": "Choose reserved shots with different pan/tilt positions.", "measurements": rows}
        for speed in (.15, .5, 1.):
            stage = "return to origin recall"
            await driver.recall(origin)
            stage = "return to origin verification"
            await driver.settled(a)
            start = asyncio.get_running_loop().time()
            stage = "absolute move command"
            await driver.absolute(b, speed)
            stage = "absolute destination verification"
            await driver.settled(b, timeout=60)
            rows.append({"mode": "absolute", "speed": speed, "ms": (asyncio.get_running_loop().time()-start)*1000})
        honored = rows[-1]["ms"] < rows[1]["ms"]*.8 and rows[2]["ms"] < rows[1]["ms"]
        camera.preset_speed_verified = honored
        if honored:
            camera.preset_positions.update({origin: a, destination: b})
        return {"honored": honored, "measurements": rows, "reason": "Speed bytes verified by position readback" if honored else "Preset recall speed is set in the camera's own settings"}
    except (ValueError, OSError, TimeoutError) as exc:
        return {"honored": False, "inconclusive": True, "failed_stage": stage, "speed": speed, "positions": positions, "measurements": rows, "detail": str(exc), "reason": "Speed support is not yet verified. Keep normal recall; inspect the failed stage before retrying."}
    finally:
        if moved:
            try:
                await driver.stop()
            except (ValueError, OSError, TimeoutError) as exc:
                rows.append({"mode": "stop", "error": str(exc)})
