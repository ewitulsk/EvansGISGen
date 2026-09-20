package com.evansgisgen.geoworld.parcel;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import org.jetbrains.annotations.Nullable;

/**
 * One real-world property parcel: a stable county id, an optional situs
 * address, and polygon rings in dataset geo meters (first ring is the
 * exterior, the rest are holes). Parcels are lookup data, not a render
 * layer — the parcel studio resolves them to editable lots.
 */
public record Parcel(String id, @Nullable String address,
                     double[] bbox, List<double[][]> rings) {

    public static Parcel fromJson(JsonObject obj) {
        JsonArray bb = obj.getAsJsonArray("bbox");
        List<double[][]> rings = new ArrayList<>();
        for (var r : obj.getAsJsonArray("rings")) {
            JsonArray ring = r.getAsJsonArray();
            double[][] pts = new double[ring.size()][2];
            for (int i = 0; i < ring.size(); i++) {
                JsonArray p = ring.get(i).getAsJsonArray();
                pts[i][0] = p.get(0).getAsDouble();
                pts[i][1] = p.get(1).getAsDouble();
            }
            rings.add(pts);
        }
        return new Parcel(
                obj.get("id").getAsString(),
                obj.has("address") && !obj.get("address").isJsonNull()
                        ? obj.get("address").getAsString() : null,
                new double[]{bb.get(0).getAsDouble(), bb.get(1).getAsDouble(),
                        bb.get(2).getAsDouble(), bb.get(3).getAsDouble()},
                rings);
    }

    public double minEast() { return bbox[0]; }
    public double minNorth() { return bbox[1]; }
    public double maxEast() { return bbox[2]; }
    public double maxNorth() { return bbox[3]; }

    /** Point-in-polygon: even-odd on the exterior, subtracting holes. */
    public boolean contains(double east, double north) {
        if (east < bbox[0] || east > bbox[2] || north < bbox[1] || north > bbox[3]) {
            return false;
        }
        boolean inside = false;
        for (int i = 0; i < rings.size(); i++) {
            boolean hit = rayHit(rings.get(i), east, north);
            inside = i == 0 ? hit : inside && !hit;
        }
        return inside;
    }

    private static boolean rayHit(double[][] ring, double e, double n) {
        boolean inside = false;
        for (int i = 0, j = ring.length - 1; i < ring.length; j = i++) {
            double[] a = ring[i];
            double[] b = ring[j];
            if ((a[1] > n) != (b[1] > n)
                    && e < a[0] + (n - a[1]) * (b[0] - a[0]) / (b[1] - a[1])) {
                inside = !inside;
            }
        }
        return inside;
    }

    /** Stable landmark id for this parcel — overwriting reuses the key. */
    public String landmarkId() {
        return "parcel_" + id.replaceAll("[^A-Za-z0-9_-]", "_");
    }
}
