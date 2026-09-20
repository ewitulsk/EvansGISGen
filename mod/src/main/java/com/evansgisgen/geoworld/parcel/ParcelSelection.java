package com.evansgisgen.geoworld.parcel;

import java.util.List;

/**
 * A studio lot: one or more parcels edited as a unit (Phase 12). Single
 * parcels keep their {@code parcel_<id>} landmark key; a multi-parcel
 * selection (a city block, a streetscape) becomes one {@code lot_<hash>}
 * landmark whose id is stable for the same sorted parcel set — re-selecting
 * the same block overwrites the same lot.
 */
public record ParcelSelection(List<Parcel> parcels, double[] bbox) {

    public static ParcelSelection of(List<Parcel> parcels) {
        double e0 = Double.MAX_VALUE, n0 = Double.MAX_VALUE;
        double e1 = -Double.MAX_VALUE, n1 = -Double.MAX_VALUE;
        for (Parcel p : parcels) {
            e0 = Math.min(e0, p.minEast());
            n0 = Math.min(n0, p.minNorth());
            e1 = Math.max(e1, p.maxEast());
            n1 = Math.max(n1, p.maxNorth());
        }
        return new ParcelSelection(List.copyOf(parcels),
                new double[]{e0, n0, e1, n1});
    }

    public static ParcelSelection single(Parcel parcel) {
        return new ParcelSelection(List.of(parcel), parcel.bbox().clone());
    }

    public double minEast() { return bbox[0]; }
    public double minNorth() { return bbox[1]; }
    public double maxEast() { return bbox[2]; }
    public double maxNorth() { return bbox[3]; }

    /** Stable landmark key: parcel id for singles, set-hash for groups. */
    public String landmarkId() {
        if (parcels.size() == 1) {
            return parcels.get(0).landmarkId();
        }
        List<String> ids = parcels.stream().map(Parcel::id).sorted().toList();
        return "lot_" + Integer.toHexString(String.join("+", ids).hashCode());
    }

    /** True when (e, n) is inside any selected parcel. */
    public boolean contains(double east, double north) {
        for (Parcel p : parcels) {
            if (p.contains(east, north)) {
                return true;
            }
        }
        return false;
    }

    /** Parcel ids as a comma list — persisted in StudioData sessions. */
    public String idList() {
        return String.join(",", parcels.stream().map(Parcel::id).toList());
    }
}
