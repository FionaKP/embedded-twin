# ADR-0002: Behavioral event-driven simulation, not whole-board SPICE

**Status:** accepted

## Context
"Probe-on-the-pad" fidelity suggests analog circuit simulation. But whole-board SPICE is intractable (no vendor models for digital ICs, hours per second of sim time) and answers the wrong questions — system troubleshooting is about logic, protocol, timing, power states, and environment interaction.

## Decision
Discrete-event behavioral simulation at nanosecond resolution. Nets resolve digital levels by drive strength and carry analog voltages where meaningful (rails, dividers, ADC pins). Component behavior = state machines + transfer functions from datasheet specs. Power = state-based current draw integrated over time.

## Consequences
- Simulates hours of device time in seconds of wall time; determinstic and assertable.
- Analog corner cases (regulator transient response, signal integrity) are approximated, not solved — acceptable for v0.1's mission; ngspice co-sim for flagged subcircuits is the v0.2 escape hatch.
- Component models are auditable Python, which is what makes an agentic datasheet→model factory feasible later.
