"""Financial statement workbook builders — financial-statements skill pattern.

Provides typed builder functions for standard financial statement sheets
(P&L, balance sheet, cash flow) that produce WorkbookSheetSpec objects
ready to drop into any WorkbookSpec.

All monetary values are in dollars. Periods are arbitrary strings (e.g. "Q2 2026").
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.workflows.workbook_models import (
    WorkbookChartSpec,
    WorkbookSheetSpec,
    WorkbookSpec,
    WorkbookTable,
)


# ---------------------------------------------------------------------------
# P&L (Income Statement)
# ---------------------------------------------------------------------------

def build_pnl_sheet(
    *,
    period: str,
    revenue: float,
    cogs: float,
    gross_profit: Optional[float] = None,
    opex: float = 0.0,
    ebitda: Optional[float] = None,
    net_income: Optional[float] = None,
    prior_period: Optional[str] = None,
    prior_revenue: Optional[float] = None,
    prior_opex: Optional[float] = None,
    prior_net_income: Optional[float] = None,
    budget_revenue: Optional[float] = None,
    budget_opex: Optional[float] = None,
    notes: Optional[str] = None,
) -> WorkbookSheetSpec:
    """
    Build a standard P&L (Income Statement) sheet.

    Returns a WorkbookSheetSpec with:
    - A summary metrics block (revenue, gross profit, EBITDA, net income)
    - A line-item detail table with optional prior-period and budget columns
    - A bar chart comparing revenue vs OpEx
    """
    gp = gross_profit if gross_profit is not None else revenue - cogs
    ebitda_val = ebitda if ebitda is not None else gp - opex
    net = net_income if net_income is not None else ebitda_val

    def _fmt(v: float) -> str:
        return f"${v:,.0f}"

    def _pct(actual: float, base: float) -> str:
        if base == 0:
            return "N/A"
        return f"{((actual - base) / abs(base)) * 100:.1f}%"

    # Summary metrics
    metrics_rows = [
        ["Revenue", _fmt(revenue)],
        ["COGS", _fmt(cogs)],
        ["Gross Profit", _fmt(gp)],
        ["Gross Margin", f"{(gp / revenue * 100):.1f}%" if revenue else "N/A"],
        ["Total OpEx", _fmt(opex)],
        ["EBITDA", _fmt(ebitda_val)],
        ["EBITDA Margin", f"{(ebitda_val / revenue * 100):.1f}%" if revenue else "N/A"],
        ["Net Income", _fmt(net)],
    ]

    # Line-item table
    has_prior = prior_period is not None
    has_budget = budget_revenue is not None

    columns = ["Line Item", period]
    if has_prior:
        columns.append(prior_period)
        columns.append(f"vs Prior ({prior_period})")
    if has_budget:
        columns.append("Budget")
        columns.append("vs Budget")

    def _row(label: str, actual: float, prior: Optional[float] = None, budget: Optional[float] = None) -> List[str]:
        row = [label, _fmt(actual)]
        if has_prior:
            row.append(_fmt(prior) if prior is not None else "—")
            row.append(_pct(actual, prior) if prior is not None else "—")
        if has_budget:
            row.append(_fmt(budget) if budget is not None else "—")
            row.append(_pct(actual, budget) if budget is not None else "—")
        return row

    detail_rows = [
        _row("Revenue", revenue, prior_revenue, budget_revenue),
        _row("COGS", cogs),
        _row("Gross Profit", gp),
        _row("Total OpEx", opex, prior_opex, budget_opex),
        _row("EBITDA", ebitda_val),
        _row("Net Income", net, prior_net_income),
    ]

    tables = [
        WorkbookTable(title="P&L Summary", columns=["Metric", "Value"], rows=metrics_rows),
        WorkbookTable(title="Income Statement Detail", columns=columns, rows=detail_rows),
    ]
    if notes:
        tables.append(WorkbookTable(
            title="Notes",
            columns=["Note"],
            rows=[[notes]],
        ))

    chart_specs = [
        WorkbookChartSpec(
            title=f"Revenue vs OpEx — {period}",
            chart_type="bar",
            x_axis="Line Item",
            y_axis=period,
            series_label=period,
            source_sheet="P&L",
            source_table="Income Statement Detail",
        )
    ]

    return WorkbookSheetSpec(
        name="P&L",
        kind="model",
        tables=tables,
        chart_specs=chart_specs,
        metadata={"statement_type": "income_statement", "period": period},
    )


# ---------------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------------

def build_balance_sheet(
    *,
    period: str,
    cash: float,
    accounts_receivable: float = 0.0,
    other_current_assets: float = 0.0,
    fixed_assets: float = 0.0,
    other_assets: float = 0.0,
    accounts_payable: float = 0.0,
    accrued_liabilities: float = 0.0,
    long_term_debt: float = 0.0,
    other_liabilities: float = 0.0,
    equity: Optional[float] = None,
    notes: Optional[str] = None,
) -> WorkbookSheetSpec:
    """
    Build a standard balance sheet.

    Returns a WorkbookSheetSpec with:
    - Assets section (current + long-term)
    - Liabilities section (current + long-term)
    - Equity section derived or provided
    - Balance check (Assets = Liabilities + Equity)
    """
    def _fmt(v: float) -> str:
        return f"${v:,.0f}"

    current_assets = cash + accounts_receivable + other_current_assets
    total_assets = current_assets + fixed_assets + other_assets
    current_liabilities = accounts_payable + accrued_liabilities
    total_liabilities = current_liabilities + long_term_debt + other_liabilities
    equity_val = equity if equity is not None else total_assets - total_liabilities
    balanced = abs(total_assets - (total_liabilities + equity_val)) < 1.0

    assets_rows = [
        ["Cash & Equivalents", _fmt(cash)],
        ["Accounts Receivable", _fmt(accounts_receivable)],
        ["Other Current Assets", _fmt(other_current_assets)],
        ["Total Current Assets", _fmt(current_assets)],
        ["Fixed Assets (Net)", _fmt(fixed_assets)],
        ["Other Long-Term Assets", _fmt(other_assets)],
        ["TOTAL ASSETS", _fmt(total_assets)],
    ]

    liabilities_rows = [
        ["Accounts Payable", _fmt(accounts_payable)],
        ["Accrued Liabilities", _fmt(accrued_liabilities)],
        ["Total Current Liabilities", _fmt(current_liabilities)],
        ["Long-Term Debt", _fmt(long_term_debt)],
        ["Other Long-Term Liabilities", _fmt(other_liabilities)],
        ["TOTAL LIABILITIES", _fmt(total_liabilities)],
    ]

    equity_rows = [
        ["Total Equity", _fmt(equity_val)],
        ["TOTAL LIABILITIES + EQUITY", _fmt(total_liabilities + equity_val)],
        ["Balance Check", "✓ Balanced" if balanced else "⚠ Imbalanced — check inputs"],
    ]

    tables = [
        WorkbookTable(title="Assets", columns=["Line Item", period], rows=assets_rows),
        WorkbookTable(title="Liabilities", columns=["Line Item", period], rows=liabilities_rows),
        WorkbookTable(title="Equity", columns=["Line Item", period], rows=equity_rows),
    ]
    if notes:
        tables.append(WorkbookTable(title="Notes", columns=["Note"], rows=[[notes]]))

    return WorkbookSheetSpec(
        name="Balance Sheet",
        kind="model",
        tables=tables,
        chart_specs=[],
        metadata={
            "statement_type": "balance_sheet",
            "period": period,
            "balanced": balanced,
        },
    )


# ---------------------------------------------------------------------------
# Cash Flow Statement
# ---------------------------------------------------------------------------

def build_cash_flow_sheet(
    *,
    period: str,
    operating_cash_flow: float,
    investing_cash_flow: float = 0.0,
    financing_cash_flow: float = 0.0,
    beginning_cash: Optional[float] = None,
    operating_items: Optional[List[tuple[str, float]]] = None,
    investing_items: Optional[List[tuple[str, float]]] = None,
    financing_items: Optional[List[tuple[str, float]]] = None,
    notes: Optional[str] = None,
) -> WorkbookSheetSpec:
    """
    Build a standard cash flow statement sheet.

    Returns a WorkbookSheetSpec with:
    - Operating, investing, and financing sections
    - Net change in cash and ending balance (if beginning_cash provided)
    - Summary chart of the three activity totals
    """
    def _fmt(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}${v:,.0f}"

    def _fmt_abs(v: float) -> str:
        return f"${v:,.0f}"

    net_change = operating_cash_flow + investing_cash_flow + financing_cash_flow
    ending_cash = (beginning_cash + net_change) if beginning_cash is not None else None

    def _item_rows(items: Optional[List[tuple[str, float]]], total: float, total_label: str) -> List[List[str]]:
        rows: List[List[str]] = []
        for label, amount in (items or []):
            rows.append([label, _fmt(amount)])
        rows.append([total_label, _fmt(total)])
        return rows

    operating_rows = _item_rows(operating_items, operating_cash_flow, "Net Operating Cash Flow")
    investing_rows = _item_rows(investing_items, investing_cash_flow, "Net Investing Cash Flow")
    financing_rows = _item_rows(financing_items, financing_cash_flow, "Net Financing Cash Flow")

    summary_rows = [
        ["Operating Activities", _fmt(operating_cash_flow)],
        ["Investing Activities", _fmt(investing_cash_flow)],
        ["Financing Activities", _fmt(financing_cash_flow)],
        ["Net Change in Cash", _fmt(net_change)],
    ]
    if beginning_cash is not None:
        summary_rows.append(["Beginning Cash", _fmt_abs(beginning_cash)])
    if ending_cash is not None:
        summary_rows.append(["Ending Cash", _fmt_abs(ending_cash)])

    tables = [
        WorkbookTable(title="Cash Flow Summary", columns=["Activity", period], rows=summary_rows),
        WorkbookTable(title="Operating Activities", columns=["Item", "Amount"], rows=operating_rows),
        WorkbookTable(title="Investing Activities", columns=["Item", "Amount"], rows=investing_rows),
        WorkbookTable(title="Financing Activities", columns=["Item", "Amount"], rows=financing_rows),
    ]
    if notes:
        tables.append(WorkbookTable(title="Notes", columns=["Note"], rows=[[notes]]))

    chart_specs = [
        WorkbookChartSpec(
            title=f"Cash Flow by Activity — {period}",
            chart_type="bar",
            x_axis="Activity",
            y_axis=period,
            series_label=period,
            source_sheet="Cash Flow",
            source_table="Cash Flow Summary",
        )
    ]

    return WorkbookSheetSpec(
        name="Cash Flow",
        kind="model",
        tables=tables,
        chart_specs=chart_specs,
        metadata={
            "statement_type": "cash_flow",
            "period": period,
            "net_change": net_change,
            "ending_cash": ending_cash,
        },
    )


# ---------------------------------------------------------------------------
# Combined statement workbook
# ---------------------------------------------------------------------------

def build_financial_statement_workbook(
    *,
    company_name: str,
    period: str,
    pnl_kwargs: Dict[str, Any],
    balance_sheet_kwargs: Optional[Dict[str, Any]] = None,
    cash_flow_kwargs: Optional[Dict[str, Any]] = None,
    template_id: str = "finance_workbook_v1",
    theme_id: str = "board_formal",
) -> WorkbookSpec:
    """
    Combine P&L, balance sheet, and cash flow sheets into a single WorkbookSpec.

    Args:
        company_name: used for the workbook title
        period: reporting period label (e.g. "Q2 2026")
        pnl_kwargs: kwargs passed to build_pnl_sheet (required)
        balance_sheet_kwargs: kwargs passed to build_balance_sheet (optional)
        cash_flow_kwargs: kwargs passed to build_cash_flow_sheet (optional)
        template_id: workbook template to apply
        theme_id: brand theme to apply
    """
    sheets: List[WorkbookSheetSpec] = []

    pnl_sheet = build_pnl_sheet(period=period, **pnl_kwargs)
    sheets.append(pnl_sheet)

    if balance_sheet_kwargs is not None:
        bs_sheet = build_balance_sheet(period=period, **balance_sheet_kwargs)
        sheets.append(bs_sheet)

    if cash_flow_kwargs is not None:
        cf_sheet = build_cash_flow_sheet(period=period, **cash_flow_kwargs)
        sheets.append(cf_sheet)

    return WorkbookSpec(
        workbook_title=f"{company_name} — Financial Statements {period}",
        sheets=sheets,
        metadata={"template_id": template_id, "theme_id": theme_id, "period": period},
    )
