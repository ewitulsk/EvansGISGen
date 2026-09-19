import math
import unittest

from geoworld_compiler.transform import GeoTransform


class GeoTransformTest(unittest.TestCase):
    # Mirrors GeoTransformTest.java — semantics must stay identical.
    T = GeoTransform(origin_x=10000, origin_z=-5000)

    def test_origin_maps_to_minecraft_origin(self):
        self.assertEqual(10000, self.T.block_x(0.0))
        self.assertEqual(-5000, self.T.block_z(0.0))

    def test_north_is_negative_z(self):
        self.assertEqual(-5100, self.T.block_z(100.0))
        self.assertEqual(-4900, self.T.block_z(-100.0))

    def test_known_beatrice_offsets(self):
        # UTM 14N offsets relative to 6th & Court (matches the Java test).
        cases = [
            (0.00, 0.00, 10000, -5000),
            (31.72, -243.57, 10032, -4756),
            (-308.01, 736.42, 9692, -5736),
            (1355.84, -1109.55, 11356, -3890),
            (1040.58, -762.15, 11041, -4238),
            (-696.93, 3659.10, 9303, -8659),
            (1642.61, 2308.05, 11643, -7308),
            (-506.70, -3845.15, 9493, -1155),
            (-6590.54, 1579.18, 3409, -6579),
            (1966.76, -1038.34, 11967, -3962),
        ]
        for east, north, x, z in cases:
            self.assertEqual(x, self.T.block_x(east), f"blockX({east})")
            self.assertEqual(z, self.T.block_z(north), f"blockZ({north})")

    def test_round_trip(self):
        self.assertEqual(1356.0, self.T.east_meters(11356))
        self.assertEqual(-1110.0, self.T.north_meters(-3890))

    def test_scale(self):
        t = GeoTransform(origin_x=2000, origin_z=3000, horizontal_meters_per_block=2.0)
        self.assertEqual(2500, t.block_x(1000.0))
        self.assertEqual(2500, t.block_z(1000.0))

    def test_vertical_datum(self):
        t = GeoTransform(datum_elevation_meters=381.0, datum_y=64)
        self.assertEqual(64, t.block_y(381.0))
        self.assertEqual(74, t.block_y(391.0))
        stretched = GeoTransform(vertical_meters_per_block=0.5,
                                 datum_elevation_meters=381.0, datum_y=64)
        self.assertEqual(84, stretched.block_y(391.0))

    def test_rounding_matches_java(self):
        # Java Math.round is half-up; Python round() is banker's — verify parity.
        self.assertEqual(10001, self.T.block_x(0.5))
        self.assertEqual(10002, self.T.block_x(1.5))


if __name__ == "__main__":
    unittest.main()
