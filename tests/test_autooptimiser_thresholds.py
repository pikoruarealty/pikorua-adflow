from pikorua_adflow.api.config import (
    AO_BENCHMARK_CPL,
    AO_FREQ_SATURATED,
    AO_FREQ_EXHAUSTED,
    AO_CPL_CEILING,
    AO_CPL_RISING_RATIO,
    AO_QUALITY_LEAD_MIN,
    AO_COOLDOWN_DAYS,
)

def test_ao_thresholds_defaults():
    # Verify that the constants are loaded as the correct types
    assert isinstance(AO_BENCHMARK_CPL, int)
    assert isinstance(AO_FREQ_SATURATED, float)
    assert isinstance(AO_FREQ_EXHAUSTED, float)
    assert isinstance(AO_CPL_CEILING, int)
    assert isinstance(AO_CPL_RISING_RATIO, float)
    assert isinstance(AO_QUALITY_LEAD_MIN, int)
    assert isinstance(AO_COOLDOWN_DAYS, int)

    # Basic sanity checks on the values
    assert AO_BENCHMARK_CPL > 0
    assert AO_FREQ_SATURATED > 1.0
    assert AO_FREQ_EXHAUSTED > AO_FREQ_SATURATED
    assert AO_CPL_CEILING > AO_BENCHMARK_CPL
    assert AO_COOLDOWN_DAYS > 0
