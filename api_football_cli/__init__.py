"""Live AI football commentators on top of api-football.

Layered clean architecture: domain / application (ports + services) /
adapters (inbound web + cli, outbound api + persistence + messaging + model),
wired together in the composition root (main.py).
"""
