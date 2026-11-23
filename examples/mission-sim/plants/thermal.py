"""Thermal plant of Payload's heated panel, stepped with the simulation (docs/dynamics.md).

A lumped model: the panel's heat capacity C, warmed by the heater's power P when the heater
pin is high, and losing heat to its surroundings through a conductance G:

    C dT/dt = P * heater - G * (T - T_env)

Integrated with fixed sub-steps of RK4, so the result is the same however the steps fall.
With the example's values the panel warms from 21.5 C at about 2 K/s with the heater on and
reaches the over-temperature switch's 45 C in about 14 s; with it off it cools back with a
time constant of C / G = 50 s.

The same model is an FMU in plants/thermal_fmu, built from C.

Parameters (the manifest's `params`): C (J/K), P (W), G (W/K), T_env and T0 (C), substep (s).
Input: heater (0 or 1). Output: T (C).
"""


class ThermalPlant:
    def __init__(self, C=5.0, P=10.0, G=0.1, T_env=21.5, T0=21.5, substep=0.01):
        self.c, self.p, self.g, self.t_env = float(C), float(P), float(G), float(T_env)
        self.temperature = float(T0)
        self.substep = float(substep)

    def derivative(self, temperature, heater):
        return (self.p * heater - self.g * (temperature - self.t_env)) / self.c

    def step(self, t, dt, inputs):
        heater = 1.0 if inputs.get("heater") else 0.0
        count = max(1, int(round(dt / self.substep)))
        h = dt / count
        temperature = self.temperature
        for _ in range(count):
            k1 = self.derivative(temperature, heater)
            k2 = self.derivative(temperature + h / 2 * k1, heater)
            k3 = self.derivative(temperature + h / 2 * k2, heater)
            k4 = self.derivative(temperature + h * k3, heater)
            temperature += h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        self.temperature = temperature
        return {"T": temperature}


def create(params):
    return ThermalPlant(**params)
