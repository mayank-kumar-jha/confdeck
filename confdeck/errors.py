"""Operator-facing problem catalog: happened, likely cause, next action."""

CATALOG = {
    "unreachable": (
        "Camera is not reachable",
        "The camera may be off, disconnected or on a different network.",
        "Check power and cable, then check its IP address.",
    ),
    "password": (
        "Wrong password",
        "The device rejected the saved sign-in details.",
        "Enter the device username and password again.",
    ),
    "path": (
        "Stream path was not found",
        "This camera uses a different video address.",
        "Try another stream path or enter the path from the camera manual.",
    ),
    "limit": (
        "Camera is busy",
        "Another connection may be using this stream, or the network is not responding.",
        "Close another camera viewer, then retry. ConfDeck uses one camera connection.",
    ),
    "codec": (
        "Unsupported codec",
        "This stream is not H.264.",
        "In the camera's own settings, choose an H.264 stream, then retry.",
    ),
    "timeout": (
        "Waiting for camera",
        "The camera is restarting or not sending video.",
        "Check its power and cable. ConfDeck will keep trying.",
    ),
    "media": (
        "Video relay is unavailable",
        "MediaMTX stopped, is missing, or its port is in use.",
        "Open Health and retry the relay; run confdeck doctor if it persists.",
    ),
    "mic": (
        "Waiting for microphone",
        "The IP or HTTPS port may be wrong, or the firewall is blocking the connection.",
        "Check the microphone IP and cable; allow HTTPS connections. Retry the connection.",
    ),
    "tls": (
        "Microphone certificate could not be verified",
        "The device uses a private certificate.",
        "Enter its verified SHA-256 certificate fingerprint in Microphone settings.",
    ),
    "protocol": (
        "This microphone did not provide beam data",
        "The model or firmware may not expose position using this protocol.",
        "Confirm the model and third-party access in Sennheiser Control Cockpit.",
    ),
    "mic_unreachable": (
        "Microphone is not reachable",
        "The device may be off, unplugged, or on a different subnet.",
        "Check the control cable (not the Dante cable), power, and IP address (192.168.1.92 for the TCC M).",
    ),
    "mic_tls": (
        "TLS certificate problem",
        "The device certificate could not be verified.",
        "Run Connect & test to see the fingerprint, verify it matches the device label, then save it.",
    ),
    "mic_auth": (
        "Authentication failed (401)",
        "The username or password was rejected, or third-party API access is not enabled.",
        "In Sennheiser Control Cockpit › Access tab, enable third-party access. Username is 'api'.",
    ),
    "mic_forbidden": (
        "Access denied (403)",
        "The device refused the request.",
        "Check third-party access is enabled in Sennheiser Control Cockpit.",
    ),
    "mic_not_found": (
        "Resource not found (404)",
        "The resource path does not exist on this firmware version.",
        "Edit the resource path list in Microphone settings and try another path.",
    ),
    "mic_conflict": (
        "Conflict (409)",
        "The subscription already exists or the session UUID is in use.",
        "Disconnect and reconnect to create a fresh subscription.",
    ),
    "mic_bad_request": (
        "Bad request (400 / 422)",
        "The device rejected a malformed request.",
        "Check the resource paths for typos.",
    ),
    "mic_server": (
        "Device server error (5xx)",
        "The microphone's internal server reported an error.",
        "Wait 30 seconds and retry. Reboot the device if it persists.",
    ),
    "mic_stream_closed": (
        "Event stream closed by device",
        "The device closed the subscription stream.",
        "ConfDeck will reconnect automatically.",
    ),
    "mic_fingerprint_changed": (
        "Certificate fingerprint changed",
        "The device certificate no longer matches the stored fingerprint.",
        "If the device was replaced or reset, clear the fingerprint and run Connect & test again.",
    ),
    "mic_no_identity": (
        "Could not read device identity",
        "GET /api/device/identity did not return a product name.",
        "Confirm the device firmware and that third-party access is enabled.",
    ),
    "mic_wrong_model": (
        "Unexpected device model",
        "The identity response does not look like a TeamConnect Ceiling Medium.",
        "Verify the IP address belongs to the Sennheiser TeamConnect Ceiling Medium.",
    ),
    "mic_no_subscription": (
        "Subscription could not be opened",
        "The SSE stream did not return a valid subscription UUID.",
        "Check the device firmware; TCC M needs Control Cockpit with third-party access enabled.",
    ),
    "mic_malformed_json": (
        "Malformed data from device",
        "The device sent data that could not be parsed as JSON.",
        "Check the device firmware version. ConfDeck will keep trying.",
    ),
    "disk": (
        "Disk space is low",
        "Less than 250 MB is free.",
        "Free space on the ConfDeck data drive.",
    ),
    "permission": (
        "Could not save this change",
        "The local data folder is not writable.",
        "Use a folder owned by your Windows or Ubuntu account.",
    ),
    "invalid": (
        "Check the entered values",
        "One or more values are not valid.",
        "Review the highlighted form and retry.",
    ),
    "internal": (
        "This action could not finish",
        "A local component returned an unexpected response.",
        "Retry the action. Copy diagnostics from Health if it continues.",
    ),
}


def problem(code: str, detail: str = "") -> dict:
    """Create a safe, consistent error payload without exceptions or secrets."""
    title, cause, fix = CATALOG.get(code, CATALOG["internal"])
    return {"code": code, "title": title, "cause": cause, "fix": fix, "detail": detail}


def classify_camera(message: str) -> str:
    lower = message.lower()
    if any(t in lower for t in ("401", "403", "unauthorized", "authentication")):
        return "password"
    if any(t in lower for t in ("453", "503", "refused", "reset", "timeout", "timed out", "too many", "limit reached", "not enough bandwidth")):
        return "limit"
    if "404" in lower or "not found" in lower:
        return "path"
    if any(t in lower for t in ("unreachable", "no route")):
        return "unreachable"
    return "timeout"
