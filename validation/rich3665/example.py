from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Column

progress = Progress(
    TextColumn("{task.description}"),
    BarColumn(),
    MofNCompleteColumn(),
    TaskProgressColumn(),
    TextColumn(
        "{task.fields[detail]}",
        table_column=Column(ratio=1, no_wrap=True, overflow="ellipsis"),
    ),
    expand=True,
)
