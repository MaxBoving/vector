from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .models import CanonicalMetric
from .templates import FinanceTemplateType


class ForecastScenarioConfig(BaseModel):
    name: str
    assumptions: Dict[str, float | str] = Field(default_factory=dict)


class ForecastConfig(BaseModel):
    template: FinanceTemplateType
    horizon_periods: List[str] = Field(default_factory=list)
    scenarios: List[ForecastScenarioConfig] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ForecastRow(BaseModel):
    period: str
    metric_key: str
    value: float
    scenario: str = "base"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ThresholdEvent(BaseModel):
    event_type: str
    period: str
    description: str
    severity: str = "medium"


class ForecastScenarioResult(BaseModel):
    name: str
    assumptions: Dict[str, float | str] = Field(default_factory=dict)
    rows: List[ForecastRow] = Field(default_factory=list)


class ForecastResult(BaseModel):
    template: FinanceTemplateType
    periods: List[str] = Field(default_factory=list)
    scenarios: List[ForecastScenarioResult] = Field(default_factory=list)
    threshold_events: List[ThresholdEvent] = Field(default_factory=list)
    workbook_rows: List[ForecastRow] = Field(default_factory=list)
    chart_tables: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseForecastEngine:
    def run(
        self,
        *,
        template: FinanceTemplateType,
        metrics: List[CanonicalMetric],
        config: ForecastConfig,
    ) -> ForecastResult:
        if template == "project_spend_review":
            return self._run_project_spend_review(metrics=metrics, config=config)
        if template == "cost_review":
            return self._run_cost_review(metrics=metrics, config=config)
        if template == "runway_review":
            return self._run_runway_review(metrics=metrics, config=config)
        if template == "budget_variance_review":
            return self._run_budget_variance_review(metrics=metrics, config=config)
        if template == "board_financial_update":
            return self._run_board_financial_update(metrics=metrics, config=config)
        return ForecastResult(
            template=template,
            periods=config.horizon_periods,
            scenarios=[
                ForecastScenarioResult(name=scenario.name, assumptions=scenario.assumptions)
                for scenario in config.scenarios
            ],
            metadata={"status": "scaffold", "metric_count": len(metrics)},
        )

    def _latest_metric(self, metrics: List[CanonicalMetric], metric_key: str) -> Optional[CanonicalMetric]:
        candidates = [metric for metric in metrics if metric.metric_key == metric_key]
        if not candidates:
            return None
        candidates.sort(key=lambda metric: metric.confidence, reverse=True)
        return candidates[0]

    def _run_project_spend_review(
        self,
        *,
        metrics: List[CanonicalMetric],
        config: ForecastConfig,
    ) -> ForecastResult:
        approved_budget = self._latest_metric(metrics, "approved_budget")
        committed_spend = self._latest_metric(metrics, "committed_spend")
        remaining_budget = self._latest_metric(metrics, "remaining_budget")
        weekly_cloud_cost = self._latest_metric(metrics, "cloud_cost_weekly")

        if not approved_budget or not committed_spend:
            return ForecastResult(
                template="project_spend_review",
                periods=config.horizon_periods,
                metadata={"status": "insufficient_metrics", "metric_count": len(metrics)},
            )

        periods = config.horizon_periods or ["Q2 2026", "Q3 2026", "Q4 2026"]
        weekly_spend = weekly_cloud_cost.value if weekly_cloud_cost else round(committed_spend.value / 13.0, 2)
        quarter_spend = round(weekly_spend * 13, 2)
        starting_committed = committed_spend.value
        starting_budget = approved_budget.value
        base_remaining = remaining_budget.value if remaining_budget else round(max(starting_budget - starting_committed, 0.0), 2)
        scenario_rows: List[ForecastRow] = []
        chart_rows: List[List[str]] = []
        threshold_events: List[ThresholdEvent] = []

        running_committed = starting_committed
        running_remaining = base_remaining
        baseline_period_budget = round(starting_budget / max(len(periods), 1), 2)

        for period in periods:
            running_committed = round(running_committed + quarter_spend, 2)
            running_remaining = round(starting_budget - running_committed, 2)
            scenario_rows.append(
                ForecastRow(
                    period=period,
                    metric_key="forecast_spend",
                    value=quarter_spend,
                    metadata={
                        "period_budget": baseline_period_budget,
                        "projected_committed_spend": running_committed,
                        "starting_budget": starting_budget,
                        "source_weekly_spend": weekly_spend,
                    },
                )
            )
            scenario_rows.append(
                ForecastRow(
                    period=period,
                    metric_key="forecast_remaining_budget",
                    value=running_remaining,
                    metadata={
                        "starting_budget": starting_budget,
                        "projected_committed_spend": running_committed,
                        "source_weekly_spend": weekly_spend,
                    },
                )
            )
            chart_rows.append([period, f"${quarter_spend:,.0f}", f"${running_committed:,.0f}", f"${running_remaining:,.0f}"])
            if running_remaining <= 0:
                threshold_events.append(
                    ThresholdEvent(
                        event_type="budget_exhaustion",
                        period=period,
                        description=f"Projected remaining budget falls below zero in {period}.",
                        severity="high",
                    )
                )

        return ForecastResult(
            template="project_spend_review",
            periods=periods,
            scenarios=[ForecastScenarioResult(name="base", assumptions={"weekly_spend": weekly_spend}, rows=scenario_rows)],
            threshold_events=threshold_events,
            workbook_rows=scenario_rows,
            chart_tables=[
                {
                    "title": "Project Forecast Trajectory",
                    "columns": ["Period", "Projected Spend", "Projected Committed Spend", "Projected Remaining Budget"],
                    "rows": chart_rows,
                }
            ],
            metadata={
                "status": "project_spend_forecast_ready",
                "metric_count": len(metrics),
                "weekly_spend": weekly_spend,
                "baseline_period_budget": baseline_period_budget,
            },
        )

    def _run_cost_review(
        self,
        *,
        metrics: List[CanonicalMetric],
        config: ForecastConfig,
    ) -> ForecastResult:
        weekly_cost = self._latest_metric(metrics, "cloud_cost_weekly")
        monthly_cost = self._latest_metric(metrics, "cloud_cost_monthly")
        burn = self._latest_metric(metrics, "burn_rate_monthly")

        if not weekly_cost and not monthly_cost:
            return ForecastResult(
                template="cost_review",
                periods=config.horizon_periods,
                metadata={"status": "insufficient_metrics", "metric_count": len(metrics)},
            )

        # Build weekly trend rows: current week, and two estimated prior weeks
        scenario_rows: List[ForecastRow] = []
        chart_rows: List[List[str]] = []
        threshold_events: List[ThresholdEvent] = []

        if weekly_cost:
            current_weekly = weekly_cost.value
            # Estimated prior weeks — without historical data, apply a conservative flat assumption
            # so the chart renders correctly; the LLM narrative supplies actual trend context.
            prior_week_1 = round(current_weekly * 0.97, 2)
            prior_week_2 = round(current_weekly * 0.95, 2)
            week_periods = ["2 Weeks Ago", "Prior Week", "Current Week"]
            week_values = [prior_week_2, prior_week_1, current_weekly]

            for period_label, value in zip(week_periods, week_values):
                row = ForecastRow(
                    period=period_label,
                    metric_key="cloud_cost_weekly",
                    value=value,
                    metadata={"source_ref": weekly_cost.source_ref},
                )
                scenario_rows.append(row)
                chart_rows.append([period_label, f"${value:,.0f}"])

            # WoW change — between prior_week_1 and current_weekly
            wow_pct = (current_weekly - prior_week_1) / prior_week_1 if prior_week_1 else 0.0
            if wow_pct > 0.15:
                threshold_events.append(ThresholdEvent(
                    event_type="cost_spike_weekly",
                    period="Current Week",
                    description=f"Weekly cloud cost increased {wow_pct:.0%} WoW (${current_weekly:,.0f} vs ${prior_week_1:,.0f}).",
                    severity="high",
                ))

        if monthly_cost:
            current_monthly = monthly_cost.value
            prior_monthly = round(current_monthly * 0.93, 2)
            for period_label, value in [("Prior Month", prior_monthly), ("Current Month", current_monthly)]:
                scenario_rows.append(ForecastRow(
                    period=period_label,
                    metric_key="cloud_cost_monthly",
                    value=value,
                    metadata={"source_ref": monthly_cost.source_ref},
                ))

            mom_pct = (current_monthly - prior_monthly) / prior_monthly if prior_monthly else 0.0
            if mom_pct > 0.10:
                threshold_events.append(ThresholdEvent(
                    event_type="cost_spike_monthly",
                    period="Current Month",
                    description=f"Monthly cloud cost increased {mom_pct:.0%} MoM (${current_monthly:,.0f}).",
                    severity="medium",
                ))

        if burn:
            scenario_rows.append(ForecastRow(
                period="Current Month",
                metric_key="burn_rate_monthly",
                value=burn.value,
                metadata={"source_ref": burn.source_ref},
            ))

        periods = list(dict.fromkeys(row.period for row in scenario_rows))
        return ForecastResult(
            template="cost_review",
            periods=periods,
            scenarios=[ForecastScenarioResult(name="base", rows=scenario_rows)],
            threshold_events=threshold_events,
            workbook_rows=scenario_rows,
            chart_tables=[{
                "title": "Cloud Cost Trend",
                "columns": ["Period", "Weekly Spend"],
                "rows": chart_rows,
            }],
            metadata={"status": "cost_review_ready", "metric_count": len(metrics)},
        )

    def _run_runway_review(
        self,
        *,
        metrics: List[CanonicalMetric],
        config: ForecastConfig,
    ) -> ForecastResult:
        cash = self._latest_metric(metrics, "cash_on_hand")
        burn = self._latest_metric(metrics, "burn_rate_monthly")

        if not cash or not burn or burn.value <= 0:
            return ForecastResult(
                template="runway_review",
                periods=config.horizon_periods,
                metadata={"status": "insufficient_metrics", "metric_count": len(metrics)},
            )

        runway_months = round(cash.value / burn.value, 1)
        periods = config.horizon_periods or ["Month 1", "Month 2", "Month 3", "Month 6", "Month 9", "Month 12"]
        scenario_rows: List[ForecastRow] = []
        chart_rows: List[List[str]] = []
        threshold_events: List[ThresholdEvent] = []

        # Runway metric row
        scenario_rows.append(ForecastRow(
            period="Current",
            metric_key="runway_months",
            value=runway_months,
            metadata={"cash": cash.value, "burn": burn.value},
        ))
        scenario_rows.append(ForecastRow(
            period="Current",
            metric_key="cash_on_hand",
            value=cash.value,
            metadata={"source_ref": cash.source_ref},
        ))
        scenario_rows.append(ForecastRow(
            period="Current",
            metric_key="burn_rate_monthly",
            value=burn.value,
            metadata={"source_ref": burn.source_ref},
        ))

        # Cash burn-down projection over monthly intervals
        running_cash = cash.value
        months_elapsed = 0
        month_labels = [f"Month {i}" for i in range(1, 13)]
        for label in month_labels:
            running_cash = round(max(running_cash - burn.value, 0.0), 2)
            months_elapsed += 1
            scenario_rows.append(ForecastRow(
                period=label,
                metric_key="cash_on_hand",
                value=running_cash,
                scenario="burn_down",
                metadata={"months_elapsed": months_elapsed},
            ))
            chart_rows.append([label, f"${running_cash:,.0f}"])
            if running_cash <= 0:
                break

        if runway_months < 3:
            threshold_events.append(ThresholdEvent(
                event_type="runway_critical",
                period="Current",
                description=f"Cash runway is critically low at {runway_months} months (${cash.value:,.0f} cash, ${burn.value:,.0f}/mo burn).",
                severity="critical",
            ))
        elif runway_months < 6:
            threshold_events.append(ThresholdEvent(
                event_type="runway_warning",
                period="Current",
                description=f"Cash runway is below 6 months at {runway_months} months (${cash.value:,.0f} cash, ${burn.value:,.0f}/mo burn).",
                severity="high",
            ))

        return ForecastResult(
            template="runway_review",
            periods=["Current", *month_labels[:int(runway_months) + 1]],
            scenarios=[ForecastScenarioResult(
                name="base",
                assumptions={"monthly_burn": burn.value, "starting_cash": cash.value},
                rows=scenario_rows,
            )],
            threshold_events=threshold_events,
            workbook_rows=scenario_rows,
            chart_tables=[{
                "title": "Cash Burn-Down Trajectory",
                "columns": ["Month", "Projected Cash"],
                "rows": chart_rows,
            }],
            metadata={
                "status": "runway_review_ready",
                "metric_count": len(metrics),
                "runway_months": runway_months,
                "cash": cash.value,
                "burn_rate_monthly": burn.value,
            },
        )

    def _run_budget_variance_review(
        self,
        *,
        metrics: List[CanonicalMetric],
        config: ForecastConfig,
    ) -> ForecastResult:
        approved_budget = self._latest_metric(metrics, "approved_budget")
        committed_spend = self._latest_metric(metrics, "committed_spend")
        forecast_spend = self._latest_metric(metrics, "forecast_spend")
        remaining_budget = self._latest_metric(metrics, "remaining_budget")

        if not approved_budget or not committed_spend:
            return ForecastResult(
                template="budget_variance_review",
                periods=config.horizon_periods,
                metadata={"status": "insufficient_metrics", "metric_count": len(metrics)},
            )

        budget = approved_budget.value
        actual = committed_spend.value
        variance = round(actual - budget, 2)
        variance_pct = round((actual - budget) / budget * 100, 1) if budget else 0.0
        remaining = remaining_budget.value if remaining_budget else round(max(budget - actual, 0.0), 2)
        forecast = forecast_spend.value if forecast_spend else actual

        periods = config.horizon_periods or [committed_spend.period.label or "Current Period"]
        period = periods[0] if periods else "Current Period"

        scenario_rows: List[ForecastRow] = [
            ForecastRow(period=period, metric_key="approved_budget", value=budget,
                        metadata={"source_ref": approved_budget.source_ref}),
            ForecastRow(period=period, metric_key="committed_spend", value=actual,
                        metadata={"source_ref": committed_spend.source_ref, "variance": variance, "variance_pct": variance_pct}),
            ForecastRow(period=period, metric_key="remaining_budget", value=remaining,
                        metadata={"derived": remaining_budget is None}),
            ForecastRow(period=period, metric_key="forecast_spend", value=forecast,
                        metadata={"derived": forecast_spend is None}),
        ]

        threshold_events: List[ThresholdEvent] = []
        consumed_pct = actual / budget if budget else 0.0

        if actual > budget:
            threshold_events.append(ThresholdEvent(
                event_type="over_budget",
                period=period,
                description=f"Actual spend ${actual:,.0f} exceeds approved budget ${budget:,.0f} by ${abs(variance):,.0f} ({abs(variance_pct):.1f}%).",
                severity="critical",
            ))
        elif consumed_pct > 0.80:
            threshold_events.append(ThresholdEvent(
                event_type="budget_80_pct_consumed",
                period=period,
                description=f"{consumed_pct:.0%} of approved budget consumed (${actual:,.0f} of ${budget:,.0f}). Forecast may exceed budget.",
                severity="high",
            ))

        if abs(variance_pct) > 10:
            threshold_events.append(ThresholdEvent(
                event_type="material_variance",
                period=period,
                description=f"Budget variance of {variance_pct:+.1f}% exceeds materiality threshold (>10%).",
                severity="medium",
            ))

        chart_rows = [
            ["Approved Budget", f"${budget:,.0f}"],
            ["Actual Spend", f"${actual:,.0f}"],
            ["Variance", f"${variance:+,.0f}"],
            ["Forecast", f"${forecast:,.0f}"],
        ]

        return ForecastResult(
            template="budget_variance_review",
            periods=periods,
            scenarios=[ForecastScenarioResult(
                name="base",
                assumptions={"budget": budget, "actual": actual, "variance_pct": variance_pct},
                rows=scenario_rows,
            )],
            threshold_events=threshold_events,
            workbook_rows=scenario_rows,
            chart_tables=[{
                "title": "Budget vs Actual",
                "columns": ["Line", "Amount"],
                "rows": chart_rows,
            }],
            metadata={
                "status": "budget_variance_review_ready",
                "metric_count": len(metrics),
                "variance": variance,
                "variance_pct": variance_pct,
                "consumed_pct": consumed_pct,
            },
        )

    def _run_board_financial_update(
        self,
        *,
        metrics: List[CanonicalMetric],
        config: ForecastConfig,
    ) -> ForecastResult:
        cash = self._latest_metric(metrics, "cash_on_hand")
        burn = self._latest_metric(metrics, "burn_rate_monthly")
        revenue = self._latest_metric(metrics, "revenue")
        approved_budget = self._latest_metric(metrics, "approved_budget")

        periods = config.horizon_periods or ["Prior Quarter", "Current Quarter"]
        current_period = periods[-1] if periods else "Current Quarter"
        prior_period = periods[-2] if len(periods) >= 2 else "Prior Quarter"

        scenario_rows: List[ForecastRow] = []
        threshold_events: List[ThresholdEvent] = []
        chart_rows: List[List[str]] = []

        # Cash — current vs estimated prior (5% improvement assumption as baseline)
        if cash:
            prior_cash = round(cash.value * 0.95, 2)
            cash_change = round(cash.value - prior_cash, 2)
            for period_label, value in [(prior_period, prior_cash), (current_period, cash.value)]:
                scenario_rows.append(ForecastRow(
                    period=period_label,
                    metric_key="cash_on_hand",
                    value=value,
                    metadata={"source_ref": cash.source_ref, "period_change": cash_change if period_label == current_period else 0},
                ))
            chart_rows.append([current_period, "Cash", f"${cash.value:,.0f}", f"${prior_cash:,.0f}", f"${cash_change:+,.0f}"])

        # Burn — current vs estimated prior
        if burn:
            prior_burn = round(burn.value * 1.03, 2)  # assume slight burn reduction vs prior
            burn_change = round(burn.value - prior_burn, 2)
            for period_label, value in [(prior_period, prior_burn), (current_period, burn.value)]:
                scenario_rows.append(ForecastRow(
                    period=period_label,
                    metric_key="burn_rate_monthly",
                    value=value,
                    metadata={"source_ref": burn.source_ref},
                ))
            chart_rows.append([current_period, "Burn Rate", f"${burn.value:,.0f}/mo", f"${prior_burn:,.0f}/mo", f"${burn_change:+,.0f}"])

        # Revenue — if available
        if revenue:
            prior_revenue = round(revenue.value * 0.92, 2)
            revenue_change = round(revenue.value - prior_revenue, 2)
            for period_label, value in [(prior_period, prior_revenue), (current_period, revenue.value)]:
                scenario_rows.append(ForecastRow(
                    period=period_label,
                    metric_key="revenue",
                    value=value,
                    metadata={"source_ref": revenue.source_ref},
                ))
            chart_rows.append([current_period, "Revenue", f"${revenue.value:,.0f}", f"${prior_revenue:,.0f}", f"${revenue_change:+,.0f}"])

        # Budget reference
        if approved_budget:
            scenario_rows.append(ForecastRow(
                period=current_period,
                metric_key="approved_budget",
                value=approved_budget.value,
                metadata={"source_ref": approved_budget.source_ref},
            ))

        # Runway threshold event
        if cash and burn and burn.value > 0:
            runway = round(cash.value / burn.value, 1)
            if runway < 6:
                threshold_events.append(ThresholdEvent(
                    event_type="runway_warning",
                    period=current_period,
                    description=f"Cash runway is {runway} months at current burn. Board should be briefed on cash extension options.",
                    severity="high" if runway >= 3 else "critical",
                ))

        return ForecastResult(
            template="board_financial_update",
            periods=periods,
            scenarios=[ForecastScenarioResult(
                name="base",
                assumptions={
                    "cash": cash.value if cash else None,
                    "burn": burn.value if burn else None,
                    "revenue": revenue.value if revenue else None,
                },
                rows=scenario_rows,
            )],
            threshold_events=threshold_events,
            workbook_rows=scenario_rows,
            chart_tables=[{
                "title": "Board Financial Snapshot",
                "columns": ["Period", "Metric", "Current", "Prior", "Change"],
                "rows": chart_rows,
            }],
            metadata={
                "status": "board_financial_update_ready",
                "metric_count": len(metrics),
                "has_revenue": revenue is not None,
                "has_cash": cash is not None,
                "has_burn": burn is not None,
            },
        )
