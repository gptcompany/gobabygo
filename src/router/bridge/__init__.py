"""GSD-Router Event Bridge.

Wraps GSD command activity into CloudEvent envelopes, validates against
JSON Schema, maps to semantic steps via YAML rules, and dispatches via
pluggable transports with offline fallback buffering.
"""
