from unittest.mock import patch
from pikorua_adflow.analytics.llm_strategist import _build_user_message, run_daily_pass, _STATE_PATH

def test_build_user_message():
    evals = [
        {
            "campaign_name": "Test Campaign",
            "clientele_type": "residential",
            "verdict": {"state": "bleeding"},
            "metrics": {
                "d7": {"cpl": 600, "ctr": 0.5, "frequency": 2.1, "spend": 1200, "leads": 2},
                "d30": {"cpl": 450},
                "cpl_rising": True,
            },
            "quality": {"metric_used": "quality_cpl", "value": 750, "building": False},
            "fixes": [{"fix_type": "dayparting"}]
        }
    ]
    crm = {"total_leads": 100, "quality_rate": "40%"}
    settled = [{"basis": "radius", "action": "broaden", "actual_pct": 10.5}]
    
    msg = _build_user_message(evals, crm, settled, deep=False)
    assert "Test Campaign" in msg
    assert "residential" in msg
    assert "dayparting" in msg
    assert "quality_cpl" in msg

@patch("pikorua_adflow.analytics.llm_strategist._call_llm")
@patch("pikorua_adflow.analytics.llm_strategist._load_state")
@patch("pikorua_adflow.analytics.llm_strategist._save_state")
def test_run_daily_pass(mock_save, mock_load, mock_call):
    mock_load.return_value = {"last_daily": None, "last_weekly": None}
    mock_call.return_value = {"explanations": [], "model_used": "mocked"}
    
    # Run the pass
    res = run_daily_pass([], {}, [])
    assert res.get("pass_type") == "weekly_deep" # because last_weekly is None
    assert res.get("model_used") == "mocked"
    mock_save.assert_called_once()
