from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest
from matplotlib.figure import Figure

from exceptions import InvalidOperationError
from models import Currency
from reporting import BarChart, Chart, ChartRenderer, LineChart, PieChart, Report, ReportKind

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MORNING, NOON, EVENING = datetime(2026, 9, 24, 9), datetime(2026, 9, 24, 12), datetime(2026, 9, 24, 18)


@pytest.fixture(scope="module")
def renderer() -> ChartRenderer:
    return ChartRenderer()


def pie(**values: object) -> PieChart:
    return PieChart("balance", "Balance", "RUB", labels=list(values), values=list(values.values()))


def test_category_chart_needs_a_value_per_label():
    with pytest.raises(InvalidOperationError, match="2 labels and 1 values"):
        BarChart("errors", "Errors", "attempts", labels=["a", "b"], values=[1])


def test_chart_name_goes_into_file_names():
    with pytest.raises(InvalidOperationError, match="chart name must be lowercase"):
        BarChart("Top clients", "Top", "RUB", labels=[], values=[])


def test_pie_shows_positive_parts_largest_first_and_lists_the_negative_ones():
    chart = pie(RUB=Decimal("100.00"), USD=Decimal("-50.00"), EUR=Decimal("300.00"), KZT=Decimal("0.00"))
    assert chart.slices == (("EUR", Decimal("300.00")), ("RUB", Decimal("100.00")))
    assert chart.left_out == (("USD", Decimal("-50.00")),)
    assert not chart.is_empty


def test_pie_folds_the_smallest_parts_into_other():
    chart = pie(**{f"part{number}": number for number in range(1, 11)})
    assert len(chart.slices) == PieChart.MAX_SLICES
    assert chart.slices[0] == ("part10", 10)
    assert chart.slices[-1] == ("Other", 1 + 2 + 3)


def test_pie_without_a_positive_part_is_empty():
    assert pie(USD=Decimal("-1.00"), RUB=0).is_empty


def test_line_points_must_be_in_time_order():
    with pytest.raises(InvalidOperationError, match="must be in time order"):
        LineChart("balance", "Balance", "RUB", {"RUB": [(NOON, Decimal(1)), (MORNING, Decimal(2))]})


def test_line_chart_is_read_only_and_empty_without_points():
    chart = LineChart("balance", "Balance", "RUB", {"RUB": [], "USD": []})
    assert chart.is_empty
    with pytest.raises(TypeError):
        chart.series["EUR"] = ()


def test_report_chart_names_are_unique():
    chart = BarChart("errors", "Errors", "attempts", labels=["a"], values=[1])
    with pytest.raises(InvalidOperationError, match="Chart names of a report must be unique"):
        Report(ReportKind.RISK, "Risk report", MORNING, Currency.RUB, (), (chart, chart))


@pytest.mark.parametrize(
    "chart",
    [
        pytest.param(pie(RUB=Decimal("100.00"), USD=Decimal("-50.00")), id="pie"),
        pytest.param(BarChart("errors", "Errors", "attempts", labels=["a", "b"], values=[3, 1]), id="bar"),
        pytest.param(
            LineChart(
                "balance",
                "Balance",
                "RUB",
                {
                    "RUB": [(MORNING, Decimal("100.00")), (NOON, Decimal("-20.00")), (EVENING, Decimal("-20.00"))],
                    "USD": [(NOON, Decimal("50.00"))],
                },
            ),
            id="line",
        ),
    ],
)
def test_every_chart_form_is_drawn_as_a_png(renderer, chart):
    figure = renderer.render(chart)
    assert isinstance(figure, Figure)
    assert figure.axes[0].get_title(loc="left") == chart.title
    image = renderer.to_png(chart)
    assert image.startswith(PNG_SIGNATURE)
    assert renderer.to_png(chart) == image  # the same chart, the same bytes


def test_pie_names_the_negative_values_it_leaves_out(renderer):
    figure = renderer.render(pie(RUB=Decimal("100.00"), USD=Decimal("-135990.00")))
    assert [text.get_text() for text in figure.texts] == ["Negative, not shown: USD -135,990.00 RUB"]
    (legend,) = figure.legends
    assert [text.get_text() for text in legend.get_texts()] == ["RUB  100.00 RUB (100.0%)"]


def test_a_single_line_needs_no_legend(renderer):
    figure = renderer.render(LineChart("total", "Total", "RUB", {"Total": [(MORNING, Decimal(1))]}))
    assert figure.legends == []


def test_empty_chart_is_not_drawn(renderer):
    with pytest.raises(InvalidOperationError, match="has nothing to draw"):
        renderer.render(BarChart("errors", "Errors", "attempts", labels=[], values=[]))


def test_line_chart_has_at_most_one_colour_per_series(renderer):
    series = {f"account {number}": [(MORNING, Decimal(number))] for number in range(len(ChartRenderer.COLORS) + 1)}
    with pytest.raises(InvalidOperationError, match="9 series, at most 8"):
        renderer.render(LineChart("balance", "Balance", "RUB", series))


def test_unknown_chart_form_is_refused(renderer):
    @dataclass(frozen=True)
    class Gauge(Chart):
        @property
        def is_empty(self) -> bool:
            return False

    with pytest.raises(InvalidOperationError, match="No drawing for Gauge"):
        renderer.render(Gauge("gauge", "Gauge", "RUB"))
