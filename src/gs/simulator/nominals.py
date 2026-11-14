"""Nominal value and noise sigma per mission parameter."""

# (nominal, sigma) per design 3.3; test_sim_matches_mission_db keeps these inside the limits
NOMINALS: dict[str, tuple[float, float]] = {
    "cabin_pressure_kpa": (101.3, 0.02),
    "cabin_temp_c": (22.0, 0.05),
    "bus_voltage_v": (28.0, 0.05),
    "bus_current_a": (5.0, 0.05),
    "tank_pressure_bar": (20.0, 0.02),
    "rate_x_dps": (0.0, 0.01),
    "rate_y_dps": (0.0, 0.01),
    "rate_z_dps": (0.0, 0.01),
}
