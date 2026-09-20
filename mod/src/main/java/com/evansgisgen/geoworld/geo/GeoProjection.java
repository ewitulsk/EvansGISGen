package com.evansgisgen.geoworld.geo;

import java.util.Locale;
import java.util.Optional;

/**
 * Forward projection from WGS84 lat/lon to dataset geo meters (east/north
 * relative to the manifest's projected anchor). Supports UTM CRS codes
 * ({@code EPSG:326xx} northern hemisphere, {@code EPSG:327xx} southern) —
 * the CRS family the compiler emits — via the standard transverse Mercator
 * series. Only used by the optional studio geocoder path; world generation
 * never projects at runtime.
 */
public record GeoProjection(String crs, double anchorEast, double anchorNorth) {

    /** Parses a manifest {@code projection} section; null when unsupported. */
    public static GeoProjection parse(String crs, double anchorEast, double anchorNorth) {
        if (crs == null || zoneOf(crs) == 0) {
            return null;
        }
        return new GeoProjection(crs, anchorEast, anchorNorth);
    }

    /** lat/lon -> {eastMeters, northMeters} relative to the anchor. */
    public Optional<double[]> toGeo(double lat, double lon) {
        int zone = zoneOf(crs);
        if (zone == 0 || lat < -80 || lat > 84) {
            return Optional.empty();
        }
        boolean south = crs.toUpperCase(Locale.ROOT).startsWith("EPSG:327");

        double a = 6378137.0;
        double f = 1.0 / 298.257223563;
        double k0 = 0.9996;
        double e2 = f * (2.0 - f);
        double ep2 = e2 / (1.0 - e2);

        double phi = Math.toRadians(lat);
        double lambda = Math.toRadians(lon);
        double lambda0 = Math.toRadians(zone * 6.0 - 183.0);

        double sin = Math.sin(phi);
        double cos = Math.cos(phi);
        double tan = Math.tan(phi);
        double n = a / Math.sqrt(1.0 - e2 * sin * sin);
        double t = tan * tan;
        double c = ep2 * cos * cos;
        double bigA = cos * (lambda - lambda0);

        double m = a * ((1.0 - e2 / 4.0 - 3.0 * e2 * e2 / 64.0 - 5.0 * e2 * e2 * e2 / 256.0) * phi
                - (3.0 * e2 / 8.0 + 3.0 * e2 * e2 / 32.0 + 45.0 * e2 * e2 * e2 / 1024.0)
                        * Math.sin(2.0 * phi)
                + (15.0 * e2 * e2 / 256.0 + 45.0 * e2 * e2 * e2 / 1024.0) * Math.sin(4.0 * phi)
                - (35.0 * e2 * e2 * e2 / 3072.0) * Math.sin(6.0 * phi));

        double east = k0 * n * (bigA + (1.0 - t + c) * Math.pow(bigA, 3) / 6.0
                + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * ep2) * Math.pow(bigA, 5) / 120.0)
                + 500000.0;
        double north = k0 * (m + n * tan * (bigA * bigA / 2.0
                + (5.0 - t + 9.0 * c + 4.0 * c * c) * Math.pow(bigA, 4) / 24.0
                + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * ep2)
                        * Math.pow(bigA, 6) / 720.0));
        if (south) {
            north += 10000000.0;
        }
        return Optional.of(new double[]{east - anchorEast, north - anchorNorth});
    }

    private static int zoneOf(String crs) {
        String u = crs.toUpperCase(Locale.ROOT).trim();
        if ((!u.startsWith("EPSG:326") && !u.startsWith("EPSG:327"))
                || u.length() < 9) {
            return 0;
        }
        try {
            int zone = Integer.parseInt(u.substring(8));
            return zone >= 1 && zone <= 60 ? zone : 0;
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
