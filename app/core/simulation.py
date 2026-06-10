from __future__ import annotations

import math


def estimate_speed_kph(
    power_watts: int | float | None,
    rider_weight_kg: float,
    grade_percent: float,
    bike_weight_kg: float = 10.0,
    cda: float = 0.32,
    crr: float = 0.004,
    air_density: float = 1.225,
    drivetrain_efficiency: float = 0.97,
) -> float:
    """Estimate steady-state cycling speed from power and road grade.

    This is a simple physics model for display and route simulation, not a lab
    calibration model. It solves:
    power = (gravity + rolling + aerodynamic drag) * velocity.
    """
    power = max(0.0, float(power_watts or 0.0)) * drivetrain_efficiency
    rider_weight = min(200.0, max(30.0, float(rider_weight_kg)))
    grade = min(25.0, max(-20.0, float(grade_percent))) / 100.0

    total_mass = rider_weight + bike_weight_kg
    angle = math.atan(grade)
    gravity_force = total_mass * 9.80665 * math.sin(angle)
    rolling_force = crr * total_mass * 9.80665 * math.cos(angle)

    if power <= 0 and grade >= 0:
        return 0.0

    def required_power(v_mps: float) -> float:
        aero_force = 0.5 * air_density * cda * v_mps * v_mps
        return (gravity_force + rolling_force + aero_force) * v_mps

    low = 0.0
    high = 35.0
    for _ in range(52):
        mid = (low + high) / 2.0
        if required_power(mid) < power:
            low = mid
        else:
            high = mid

    return round(min(high * 3.6, 110.0), 2)


def advance_speed_mps(
    current_speed_mps: float,
    power_watts: int | float | None,
    rider_weight_kg: float,
    bike_weight_kg: float,
    grade_percent: float,
    dt: float,
    cda: float = 0.32,
    crr: float = 0.004,
    air_density: float = 1.225,
    drivetrain_efficiency: float = 0.97,
) -> float:
    """Advance speed with a simple inertia-aware cycling physics model."""
    if dt <= 0:
        return max(0.0, current_speed_mps)

    power = max(0.0, float(power_watts or 0.0)) * drivetrain_efficiency
    rider_weight = min(200.0, max(30.0, float(rider_weight_kg)))
    bike_weight = min(30.0, max(5.0, float(bike_weight_kg)))
    total_mass = rider_weight + bike_weight
    grade = min(25.0, max(-20.0, float(grade_percent))) / 100.0
    angle = math.atan(grade)
    speed = max(0.0, float(current_speed_mps))

    # Convert power to tractive force. Clamp the very low-speed region so a
    # standing start accelerates smoothly instead of producing unrealistic force.
    effective_speed = max(speed, 2.0)
    drive_force = min(power / effective_speed, 900.0)

    gravity_force = total_mass * 9.80665 * math.sin(angle)
    rolling_force = crr * total_mass * 9.80665 * math.cos(angle)
    aero_force = 0.5 * air_density * cda * speed * speed
    net_force = drive_force - gravity_force - rolling_force - aero_force

    acceleration = max(-6.0, min(4.0, net_force / total_mass))
    next_speed = speed + acceleration * min(dt, 1.0)

    # Avoid fake perpetual motion on flats/uphill when there is no power.
    if power <= 0 and grade >= 0 and next_speed < 0.08:
        next_speed = 0.0

    return max(0.0, min(next_speed, 32.0))


def estimate_heart_rate(
    power_watts: int | float | None,
    rider_weight_kg: float,
    previous_bpm: int | None = None,
) -> int:
    """Generate a plausible simulated heart rate from power.

    Real heart rate requires a separate HR sensor. This MVP uses this function
    only for display/testing and keeps it deliberately conservative.
    """
    power = max(0.0, float(power_watts or 0.0))
    weight = min(200.0, max(30.0, float(rider_weight_kg)))
    watts_per_kg = power / weight
    target = 62.0 + 118.0 * (1.0 - math.exp(-0.62 * watts_per_kg))
    target = min(195.0, max(58.0, target))

    if previous_bpm is None:
        return int(round(target))

    # Smooth changes so the mock HR feels like physiology rather than telemetry.
    alpha = 0.18
    return int(round(previous_bpm + (target - previous_bpm) * alpha))
