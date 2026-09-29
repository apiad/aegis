"""Network probes and the service that caches them.

Three readings that differ by a factor of three thousand in cost — a TCP
handshake, a 300-byte lookup, a megabyte download — so they cannot share a
cadence. `probe` holds the pure functions; `service` holds the one that
polls them and the state the UI reads.
"""
