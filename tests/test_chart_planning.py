from src.presentation import ChartIntentKind, build_chart_plan


def test_chart_plan_uses_comparisons_and_time_series_without_domain_hints() -> None:
    plan = build_chart_plan(
        numeric_series=[
            {"period": "May 2026", "metric": "Revenue", "actual": 120.0, "budget": 100.0, "forecast": 130.0},
            {"period": "Jun 2026", "metric": "Revenue", "actual": 140.0, "budget": 110.0, "forecast": 150.0},
        ],
        dimensions=["metric"],
        time_periods=["May 2026", "Jun 2026"],
        comparisons=[{"metric": "Revenue", "delta": 20.0}],
        available_fields=["actual", "budget", "forecast", "variance"],
    )

    assert plan.available_fields == ["actual", "budget", "forecast", "variance"]
    assert plan.requests[0].kind == ChartIntentKind.COMPARISON
    assert any(request.kind == ChartIntentKind.TREND for request in plan.requests)


def test_chart_plan_falls_back_to_quantifiable_mix_when_only_numeric_series_exists() -> None:
    plan = build_chart_plan(
        numeric_series=[{"category": "North America", "value": 42.0}],
        dimensions=["category"],
        time_periods=[],
        comparisons=[],
        available_fields=["value"],
    )

    assert plan.requests
    assert plan.requests[0].kind == ChartIntentKind.MIX
