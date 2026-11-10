"""Passive acoustic sensor node software.

This package captures synchronized multichannel microphone audio, flags
rotor-like sound candidates with a transparent heuristic, and estimates the
apparent bearing of the sound source. It does not identify aircraft and it
does not estimate range from a single array.
"""

__version__ = "0.2.2"
REVISION = "B"
STATUS_SCHEMA = "pasensor.status.v1"
EVENT_SCHEMA = "pasensor.event.v1"
OBSERVATION_SCHEMA = "pasensor.observation.v1"
RECORDING_SCHEMA = "pasensor.recording.v1"
CONFIG_SCHEMA = "pasensor.node-config.v2"
