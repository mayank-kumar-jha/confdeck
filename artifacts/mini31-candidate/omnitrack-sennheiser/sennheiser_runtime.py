"""Run ConfDeck's microphone and seat engine without a second camera/media owner."""
import asyncio
import os
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse
from confdeck.app import create_app
from confdeck.runtime import Runtime
from confdeck.security import setup_logs


class MicrophoneRuntime(Runtime):
    async def start(self):
        self.follow.live = False
        if self.settings.mic.enabled:
            await self.connect_mic(self.settings.mic)
        for settings in self.settings.microphones:
            await self.connect_mic(settings, settings.id)
        self.background(self.monitor())
        self.background(self.follow.run())
        self.background(self.poll_tracking_position())


def build_app(root):
    root.mkdir(parents=True, exist_ok=True)
    setup_logs(root)
    runtime = MicrophoneRuntime(root)
    original_mode = runtime.follow.mode

    def microphone_mode(live):
        if live:
            raise ValueError('Camera preset control belongs to OmniTrack')
        return original_mode(False)

    runtime.follow.mode = microphone_mode
    app = create_app(runtime, lifecycle=True)
    allowed = {
        ('GET', '/api/state'), ('GET', '/api/tracking'),
        ('POST', '/api/tracking/mode'), ('PUT', '/api/tracking/settings'),
        ('POST', '/api/mic'), ('GET', '/api/mic/device-settings'),
        ('POST', '/api/mic/device-settings'), ('POST', '/api/mic/device-settings/revert'),
    }

    @app.middleware('http')
    async def microphone_only(request, call_next):
        if (request.method, request.url.path) not in allowed:
            return JSONResponse({'error': 'Use the OmniTrack interface'}, status_code=404)
        return await call_next(request)

    return app


if __name__ == '__main__':
    root = Path(os.environ['CONFDECK_MIC_DATA'])
    uvicorn.run(build_app(root), host='127.0.0.1', port=8765, log_level='warning')
