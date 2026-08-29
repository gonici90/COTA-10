"""Compatibility entrypoint.

Render services created manually can keep an old `uvicorn server:app` start command
and ignore render.yaml/Procfile changes. Always export the latest production app here.
"""
from server_v7 import app
