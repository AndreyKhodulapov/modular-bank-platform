"""ChartRenderer: draws the charts of a report as PNG images with matplotlib.

matplotlib is used through its object API: a ``Figure`` is created and
saved directly, without ``pyplot``. There is then no global state, no
figure to close afterwards and no window backend, so drawing works the
same in a terminal, in the tests and on a server.

Money stays ``Decimal`` everywhere else; it becomes ``float`` only here,
for the position of a mark on the picture. Every label shows the exact
value.
"""

import io
from collections.abc import Callable
from decimal import Decimal

from matplotlib.axes import Axes
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from exceptions import InvalidOperationError
from reporting.report import BarChart, Chart, LineChart, PieChart


def _amount(value: Decimal | int) -> str:
    """A value with thousands separated: ``1,464,010.00``, a count as ``12``."""
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


class ChartRenderer:
    """Turns a chart description into a PNG image.

    One look for every chart: a light surface, a recessive hairline grid,
    text in ink colours and the series in a fixed order of categorical
    colours (a colour-blind safe palette), 2px lines, a legend only when
    there are two series or more.
    """

    COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
    SURFACE = "#fcfcfb"
    INK = "#0b0b0b"
    INK_SECONDARY = "#52514e"
    MUTED = "#898781"
    GRID = "#e1e0d9"
    AXIS = "#c3c2b7"

    def __init__(self, *, width: float = 8.0, dpi: int = 120) -> None:
        self._width = width
        self._dpi = dpi
        self._drawers: dict[type[Chart], Callable[[Chart], Figure]] = {
            PieChart: self._draw_pie,
            BarChart: self._draw_bar,
            LineChart: self._draw_line,
        }

    def render(self, chart: Chart) -> Figure:
        drawer = self._drawers.get(type(chart))
        if drawer is None:
            raise InvalidOperationError(f"No drawing for {type(chart).__name__}.")
        if chart.is_empty:
            raise InvalidOperationError(f"Chart {chart.name!r} has nothing to draw.")
        return drawer(chart)

    def to_png(self, chart: Chart) -> bytes:
        buffer = io.BytesIO()
        # no Software entry: the file does not carry the matplotlib version
        self.render(chart).savefig(buffer, format="png", facecolor=self.SURFACE, metadata={"Software": None})
        return buffer.getvalue()

    # --- common parts

    def _axes(self, chart: Chart, height: float) -> tuple[Figure, Axes]:
        figure = Figure(figsize=(self._width, height), dpi=self._dpi, facecolor=self.SURFACE, layout="constrained")
        axes = figure.add_subplot()
        axes.set_facecolor(self.SURFACE)
        axes.set_title(chart.title, loc="left", color=self.INK, fontsize=12, pad=12)
        axes.tick_params(colors=self.MUTED, labelcolor=self.INK_SECONDARY, labelsize=9)
        for side in ("top", "right"):
            axes.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            axes.spines[side].set_color(self.AXIS)
        return figure, axes

    def _grid(self, axes: Axes, axis: str) -> None:
        axes.grid(axis=axis, color=self.GRID, linewidth=1)
        axes.set_axisbelow(True)

    def _legend(self, figure: Figure, handles: list, labels: list[str]) -> None:
        # a figure legend "outside" the axes is part of the layout, so it is never cut off
        figure.legend(
            handles, labels, loc="outside right upper", frameon=False, fontsize=9, labelcolor=self.INK_SECONDARY
        )

    # --- chart forms

    def _draw_pie(self, chart: PieChart) -> Figure:
        figure, axes = self._axes(chart, height=4.5)
        values = [value for _, value in chart.slices]
        total = sum(values)
        wedges, _ = axes.pie(
            [float(value) for value in values],
            colors=self.COLORS[: len(values)],
            startangle=90,
            counterclock=False,
            # a surface-coloured gap between the slices instead of a border
            wedgeprops={"edgecolor": self.SURFACE, "linewidth": 2},
        )
        axes.set_aspect("equal")
        # the values and shares go to the legend: text stays in ink, never on a coloured slice
        self._legend(
            figure,
            wedges,
            [f"{label}  {_amount(value)} {chart.unit} ({100 * value / total:.1f}%)" for label, value in chart.slices],
        )
        if chart.left_out:
            left_out = ", ".join(f"{label} {_amount(value)} {chart.unit}" for label, value in chart.left_out)
            figure.text(0.01, 0.01, f"Negative, not shown: {left_out}", color=self.MUTED, fontsize=8)
        return figure

    def _draw_bar(self, chart: BarChart) -> Figure:
        # horizontal bars: long category names stay readable; a row per bar keeps the bars thin
        figure, axes = self._axes(chart, height=1.2 + 0.35 * len(chart.labels))
        positions = range(len(chart.labels))
        bars = axes.barh(positions, [float(value) for value in chart.values], height=0.55, color=self.COLORS[0])
        axes.set_yticks(positions, chart.labels)
        axes.invert_yaxis()  # the first category on top
        axes.bar_label(bars, labels=[_amount(value) for value in chart.values], padding=4, color=self.INK, fontsize=9)
        axes.margins(x=0.15)  # room for the value at the tip of the longest bar
        if all(isinstance(value, int) for value in chart.values):
            axes.xaxis.set_major_locator(MaxNLocator(integer=True))  # a count has no ticks between whole numbers
        axes.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        axes.set_xlabel(chart.unit, color=self.MUTED, fontsize=9)
        axes.spines["left"].set_visible(False)
        axes.tick_params(axis="y", length=0)
        self._grid(axes, "x")
        return figure

    def _draw_line(self, chart: LineChart) -> Figure:
        figure, axes = self._axes(chart, height=4.5)
        drawn = {label: points for label, points in chart.series.items() if points}
        if len(drawn) > len(self.COLORS):
            raise InvalidOperationError(f"Chart {chart.name!r} has {len(drawn)} series, at most {len(self.COLORS)}.")
        lowest = min(value for points in drawn.values() for _, value in points)
        if lowest < 0:
            axes.axhline(0, color=self.AXIS, linewidth=1)  # an overdraft goes below this line
        for color, (label, points) in zip(self.COLORS, drawn.items(), strict=False):
            moments = [moment for moment, _ in points]
            values = [float(value) for _, value in points]
            axes.step(moments, values, where="post", color=color, linewidth=2, label=label, solid_joinstyle="round")
            # the end dot, ringed with the surface colour where it crosses another line
            axes.plot(
                moments[-1:],
                values[-1:],
                "o",
                color=color,
                markersize=8,
                markeredgecolor=self.SURFACE,
                markeredgewidth=2,
            )
        locator = AutoDateLocator()
        axes.xaxis.set_major_locator(locator)
        axes.xaxis.set_major_formatter(ConciseDateFormatter(locator))
        axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        axes.set_ylabel(chart.unit, color=self.MUTED, fontsize=9)
        self._grid(axes, "y")
        if len(drawn) > 1:
            self._legend(figure, *axes.get_legend_handles_labels())
        return figure
