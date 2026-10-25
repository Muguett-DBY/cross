"""Regressions for the puzzle bugs in Textual #5762 and #6294."""

from __future__ import annotations

import pytest

from textual import containers
from textual.app import App, ComposeResult
from textual.demo.game import Game, Tile
from textual.geometry import Offset, Size

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("start", "moved"),
    [
        (Offset(1, 0), Offset(0, 0)),
        (Offset(0, 1), Offset(0, 0)),
        (Offset(1, 1), Offset(2, 0)),
    ],
)
async def test_moved_tile_mount_keeps_original_content(
    start: Offset, moved: Offset
) -> None:
    """A pre-mount shuffle must not change the tile's portion of the picture."""
    tile_size = Size(8, 3)
    picture = "\n".join("012345678901234567890123" for _ in range(9))

    class TileApp(App):
        """Keep one tile mounted in a fixed-size picture grid."""

        def compose(self) -> ComposeResult:
            """Mount a tile that moved before its mount handler ran."""
            with containers.HorizontalGroup() as grid:
                grid.styles.width = tile_size.width * 3
                grid.styles.height = tile_size.height * 3
                tile = Tile(picture, 1, tile_size, start)
                tile.set_reactive(Tile.position, moved)
                yield tile

    async with TileApp().run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        tile = pilot.app.query_one(Tile)
        assert tile.position == moved
        assert tile.scroll_offset == start * tile_size
        assert tile.offset == moved * tile_size


@pytest.mark.parametrize("side", [3, 4, 5])
async def test_recompose_after_move_resets_the_puzzle(side: int) -> None:
    """Starting a new game must restore tile identities and the blank corner."""

    class GameApp(App):
        """Host the puzzle without the surrounding demo screen."""

        def compose(self) -> ComposeResult:
            """Mount the game without starting its random shuffle worker."""
            game = Game(
                "\n".join("x" * 120 for _ in range(100)),
                "",
                dimensions=(side, side),
                tile_size=(6, 3),
            )
            game.visible = True
            yield game

    async with GameApp().run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        game = pilot.app.query_one(Game)
        original = {
            tile.tile: (tile.start_position, tile.scroll_offset)
            for tile in game.query(Tile)
        }
        corner = Offset(side - 1, side - 1)
        assert game.get_tile(None).position == corner

        neighbour = game.get_tile_at(Offset(side - 2, side - 1))
        assert neighbour.tile is not None
        game.move_tile(neighbour.tile)
        assert game.get_tile(None).position != corner

        await game.recompose()
        await pilot.pause()

        assert game.get_tile(None).position == corner
        assert {
            tile.tile: (tile.start_position, tile.scroll_offset)
            for tile in game.query(Tile)
        } == original
        assert game.check_win()
