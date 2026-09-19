import struct
import tempfile
import unittest
from pathlib import Path

from geoworld_compiler.fixture import write_fixture
from geoworld_compiler.tileio import (
    TILE_SIZE, pack_bitset, pack_elevation, read_tile, unpack_bitset, write_tile,
)


class TileIoTest(unittest.TestCase):
    def test_roundtrip_all_layers(self):
        n = TILE_SIZE * TILE_SIZE
        layers = {
            "elevation": pack_elevation([(i * 7) % 500 - 250 for i in range(n)]),
            "influence": bytes(i % 256 for i in range(n)),
            "surface": bytes(i % 17 for i in range(n)),
            "road": bytes(i % 9 for i in range(n)),
            "water": pack_bitset([i % 3 == 0 for i in range(n)]),
        }
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "+0000_-0001.gwt"
            write_tile(p, 0, -1, layers)
            tx, tz, out = read_tile(p)
        self.assertEqual((0, -1), (tx, tz))
        self.assertEqual(set(layers), set(out))
        for name in layers:
            self.assertEqual(layers[name], out[name], name)

    def test_roundtrip_uncompressed(self):
        n = TILE_SIZE * TILE_SIZE
        layers = {"elevation": pack_elevation(list(range(n))[:0] + [64] * n)}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.gwt"
            write_tile(p, 3, 4, layers, compress=False)
            self.assertEqual(layers["elevation"], read_tile(p)[2]["elevation"])

    def test_fixture_pattern(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_fixture(Path(td) / "fixture.gwt")
            tx, tz, layers = read_tile(p)
        self.assertEqual((0, 0), (tx, tz))
        elev = struct.unpack(f">{TILE_SIZE * TILE_SIZE}h", layers["elevation"])
        self.assertEqual(-250, elev[0])
        self.assertEqual(-243, elev[1])
        self.assertEqual(0, layers["influence"][0])
        self.assertEqual(255, layers["influence"][255])
        self.assertEqual(0, layers["influence"][256])
        self.assertEqual(2, layers["surface"][2])
        water = unpack_bitset(layers["water"])
        self.assertEqual(1, water[0])
        self.assertEqual(0, water[1])
        self.assertEqual(0, water[2])
        self.assertEqual(1, water[3])

    def test_bitset_roundtrip(self):
        n = TILE_SIZE * TILE_SIZE
        bits = [(i * i) % 7 < 3 for i in range(n)]
        self.assertEqual(bytes(bits), unpack_bitset(pack_bitset(bits)))


if __name__ == "__main__":
    unittest.main()
