from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from . import box

from .console import (
    Console,
    ConsoleOptions,
    ConsoleRenderable,
    RenderableType,
    RenderResult,
    RenderWidth,
)
from . import errors
from .segment import Segment
from .style import Style
from .text import Text
from ._tools import iter_first, iter_last, ratio_divide


@dataclass
class Column:
    """Defines a column in a table."""

    title: RenderableType = ""
    width: Optional[int] = None
    ratio: Optional[int] = None
    _cells: List[ConsoleRenderable] = field(default_factory=list)

    @property
    def cells(self) -> Iterable[RenderableType]:
        """Get all cells in the column, including header."""
        yield Text(self.title) if isinstance(self.title, str) else self.title
        yield from self._cells

    @property
    def flexible(self) -> bool:
        """Check if this column is flexible."""
        return self.ratio is not None

    def measure(self, max_width: int) -> RenderWidth:
        """Get the minimum and maximum width of the column."""
        if self.width is not None:
            # Fixed width column
            return RenderWidth(self.width, self.width)
        # Flexible column, we need to measure contents
        min_widths: List[int] = []
        max_widths: List[int] = []
        append_min = min_widths.append
        append_max = max_widths.append
        get_render_width = RenderWidth.get
        for renderable in self.cells:
            _min, _max = get_render_width(renderable, max_width)
            append_min(_min)
            append_max(_max)
        return RenderWidth(max(min_widths), max(max_widths))


class Table:
    columns: List[Column]

    def __init__(
        self,
        *headers: Union[Column, str],
        width: int = None,
        box: Optional[box.Box] = box.HEAVY_EDGE,
        expand: bool = False,
        style: Union[str, Style] = "none",
        header_style: Union[str, Style] = "bold",
        border_style: Union[str, Style] = "dim",
    ) -> None:
        self.width = width
        self.box = box
        self.columns = [
            (Column(header) if isinstance(header, str) else header)
            for header in headers
        ]
        self.expand = expand
        self.style = style
        self.header_style = header_style
        self.border_style = border_style
        self._row_count = 0

    def add_row(self, *renderables: Optional[Union[str, ConsoleRenderable]]) -> None:

        for index, renderable in enumerate(renderables):
            if index == len(self.columns):
                column = Column()
                for _ in range(self._row_count):
                    column._cells.append(Text(""))
                self.columns.append(column)
            else:
                column = self.columns[index]
            if isinstance(renderable, ConsoleRenderable):
                column._cells.append(renderable)
            elif renderable is None:
                column._cells.append(Text(""))
            elif isinstance(renderable, str):
                column._cells.append(Text(renderable))
            else:
                raise errors.NotRenderableError(
                    f"unable to render {renderable!r}; str or object with a __console__ method is required"
                )
        self._row_count += 1

    def __console__(self, console: Console, options: ConsoleOptions) -> RenderResult:

        max_width = options.max_width
        if self.width is not None:
            max_width = min(self.width, max_width)
        if self.box:
            max_width -= len(self.columns) + 2
        widths = self._calculate_column_widths(max_width)

        yield from self._render(console, options, widths)

    def _calculate_column_widths(self, max_width: int) -> List[int]:
        """Calculate the widths of each column."""
        columns = self.columns
        width_ranges = [column.measure(max_width) for column in columns]
        widths = [_range.maximum for _range in width_ranges]

        if self.expand:
            ratios = [col.ratio or 0 for col in columns if col.flexible]
            if any(ratios):
                fixed_widths = [_range.minimum for _range in width_ranges]
                flex_minimum = [
                    column.width or 1 for column in columns if column.flexible
                ]
                flexible_width = max_width - sum(fixed_widths)
                flex_widths = ratio_divide(flexible_width, ratios, flex_minimum)
                iter_flex_widths = iter(flex_widths)
                for index, column in enumerate(columns):
                    if column.flexible:
                        widths[index] = next(iter_flex_widths)

        table_width = sum(widths)

        if table_width > max_width:
            flex_widths = [_range.span for _range in width_ranges]
            if not any(flex_widths):
                flex_widths = [1] * len(flex_widths)
            excess_width = table_width - max_width
            shrink_widths = ratio_divide(excess_width, flex_widths)
            widths = [_width - shrink for _width, shrink in zip(widths, shrink_widths)]
        elif table_width < max_width and self.expand:
            pad_widths = ratio_divide(max_width - table_width, widths)
            widths = [_width + pad for _width, pad in zip(widths, pad_widths)]
        return widths

    def _render(
        self, console: Console, options: ConsoleOptions, widths: List[int]
    ) -> RenderResult:

        style = console.get_style(self.style)
        header_style = console.get_style(self.header_style)
        border_style = console.get_style(self.border_style)
        rows = list(zip(*(column.cells for column in self.columns)))
        box = self.box

        if box:
            yield Segment(box.get_top(*widths), border_style)
        for first, row in iter_first(rows):
            max_height = 1
            cells: List[List[List[Segment]]] = []
            for width, cell in zip(widths, row):
                lines = console.render_lines(
                    cell, options.with_width(width), header_style if first else style
                )
                max_height = max(max_height, len(lines))
                cells.append(lines)

            cells[:] = [
                Segment.set_shape(cell, width, max_height, style=style)
                for width, cell in zip(widths, cells)
            ]

            if box is not None:
                if first:
                    left = Segment(box.head_left, border_style)
                    right = Segment(box.head_right, border_style)
                    divider = Segment(box.head_vertical, border_style)
                else:
                    left = Segment(box.mid_left, border_style)
                    right = Segment(box.mid_right, border_style)
                    divider = Segment(box.mid_vertical, border_style)
            for line_no in range(max_height):
                if box:
                    yield left
                for last, cell in iter_last(cells):
                    yield from cell[line_no]
                    if box and not last:
                        yield divider
                if box:
                    yield right
                yield Segment.line()
                if box and first:
                    yield Segment(box.get_row(*widths), border_style)
        if box:
            yield Segment(box.get_bottom(*widths), border_style)


if __name__ == "__main__":

    c = Console()
    table = Table("Foo", "Bar", expand=True, border_style="", box=box.HEAVY_EDGE)
    table.columns[0].width = 12

    table.add_row("Hello, World! " * 8, "cake" * 10)
    from .markdown import Markdown

    table.add_row(Markdown("# This is *Markdown*!"), "More text", "Hello WOrld")

    c.print(table)
