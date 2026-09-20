package com.evansgisgen.geoworld.parcel;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Runtime view of a dataset's {@code parcels.json} sidecar: parcels bucketed
 * into a spatial grid for point-in-polygon lookup, plus a normalized-address
 * gazetteer for address search and command autocomplete.
 *
 * <p>Lookup order for a query: raw coordinates, then gazetteer match, then
 * point-in-polygon at the gazetteer point. The optional HTTP geocoder sits
 * upstream of this index and feeds it coordinates — it is never required.
 */
public final class ParcelIndex {
    private static final Logger LOGGER = LoggerFactory.getLogger(ParcelIndex.class);
    private static final Gson GSON = new Gson();

    /** Spatial grid cell size (geo meters) for parcel candidate bucketing. */
    private static final int CELL = 512;

    public static final ParcelIndex EMPTY = new ParcelIndex(
            Map.of(), Map.of(), List.of());

    private record Address(String text, double east, double north,
                           @Nullable String parcel) {}

    private final Map<String, Parcel> byId;
    private final Map<Long, List<Parcel>> grid;
    private final List<Address> addresses;

    private ParcelIndex(Map<String, Parcel> byId, Map<Long, List<Parcel>> grid,
            List<Address> addresses) {
        this.byId = byId;
        this.grid = grid;
        this.addresses = addresses;
    }

    public boolean isEmpty() {
        return byId.isEmpty();
    }

    public int size() {
        return byId.size();
    }

    /** Loads {@code <dataset>/parcels.json}; EMPTY when absent/unreadable. */
    public static ParcelIndex load(@Nullable Path datasetRoot) {
        if (datasetRoot == null) {
            return EMPTY;
        }
        Path file = datasetRoot.resolve("parcels.json");
        if (!Files.isRegularFile(file)) {
            return EMPTY;
        }
        try {
            JsonObject root = GSON.fromJson(Files.readString(file), JsonObject.class);
            Map<String, Parcel> byId = new LinkedHashMap<>();
            Map<Long, List<Parcel>> grid = new HashMap<>();
            for (var el : root.getAsJsonArray("parcels")) {
                Parcel p = Parcel.fromJson(el.getAsJsonObject());
                byId.put(p.id(), p);
                int c0x = cellOf(p.minEast()), c1x = cellOf(p.maxEast());
                int c0z = cellOf(p.minNorth()), c1z = cellOf(p.maxNorth());
                for (int cx = c0x; cx <= c1x; cx++) {
                    for (int cz = c0z; cz <= c1z; cz++) {
                        grid.computeIfAbsent(cellKey(cx, cz), k -> new ArrayList<>())
                                .add(p);
                    }
                }
            }
            List<Address> addresses = new ArrayList<>();
            if (root.has("addresses")) {
                for (var el : root.getAsJsonArray("addresses")) {
                    JsonObject a = el.getAsJsonObject();
                    addresses.add(new Address(
                            a.get("text").getAsString(),
                            a.get("east").getAsDouble(),
                            a.get("north").getAsDouble(),
                            a.has("parcel") && !a.get("parcel").isJsonNull()
                                    ? a.get("parcel").getAsString() : null));
                }
            }
            addresses.sort(Comparator.comparing(Address::text));
            LOGGER.info("Loaded {} parcels, {} addresses from {}",
                    byId.size(), addresses.size(), file);
            return new ParcelIndex(byId, grid, addresses);
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read parcels {}: {}", file, e.toString());
            return EMPTY;
        }
    }

    /** The parcel containing geo point (east, north), if any. */
    public Optional<Parcel> at(double east, double north) {
        for (Parcel p : grid.getOrDefault(
                cellKey(cellOf(east), cellOf(north)), List.of())) {
            if (p.contains(east, north)) {
                return Optional.of(p);
            }
        }
        return Optional.empty();
    }

    @Nullable
    public Parcel byId(String id) {
        return byId.get(id);
    }

    /** Parcels whose bbox intersects the geo rect — studio context rings. */
    public List<Parcel> within(double e0, double n0, double e1, double n1) {
        List<Parcel> out = new ArrayList<>();
        for (int cx = cellOf(e0); cx <= cellOf(e1); cx++) {
            for (int cz = cellOf(n0); cz <= cellOf(n1); cz++) {
                for (Parcel p : grid.getOrDefault(cellKey(cx, cz), List.of())) {
                    if (p.minEast() <= e1 && p.maxEast() >= e0
                            && p.minNorth() <= n1 && p.maxNorth() >= n0
                            && !out.contains(p)) {
                        out.add(p);
                    }
                }
            }
        }
        return out;
    }

    /**
     * Resolves a query to a parcel: "e,n" geo meters, an exact gazetteer
     * address, or the parcel containing a matched gazetteer point.
     */
    public Optional<Parcel> resolve(String query) {
        Double[] coords = parseCoords(query);
        if (coords != null) {
            return at(coords[0], coords[1]);
        }
        String want = normalize(query);
        if (want == null) {
            return Optional.empty();
        }
        Address best = findAddress(want);
        if (best == null) {
            return Optional.empty();
        }
        if (best.parcel() != null && byId.containsKey(best.parcel())) {
            return Optional.of(byId.get(best.parcel()));
        }
        return at(best.east(), best.north());
    }

    @Nullable
    private Address findAddress(String want) {
        // Sorted list — binary-search the prefix window, then rank.
        Address best = null;
        for (Address a : addresses) {
            if (a.text().equals(want)) {
                return a;
            }
            if (a.text().startsWith(want)) {
                best = best == null ? a : best;
            }
        }
        return best;
    }

    /** Gazetteer completions for a partial query (command suggestions). */
    public List<String> suggest(String prefix, int limit) {
        String want = normalize(prefix);
        List<String> out = new ArrayList<>();
        if (want == null) {
            return out;
        }
        for (Address a : addresses) {
            if (a.text().startsWith(want)) {
                out.add(a.text());
                if (out.size() >= limit) {
                    break;
                }
            }
        }
        return out;
    }

    // --- helpers shared with the compiler's normalization -------------------

    private static final Map<String, String> SUFFIX = Map.ofEntries(
            Map.entry("STREET", "ST"), Map.entry("AVENUE", "AVE"),
            Map.entry("BOULEVARD", "BLVD"), Map.entry("DRIVE", "DR"),
            Map.entry("ROAD", "RD"), Map.entry("LANE", "LN"),
            Map.entry("COURT", "CT"), Map.entry("PLACE", "PL"),
            Map.entry("TERRACE", "TER"), Map.entry("CIRCLE", "CIR"),
            Map.entry("HIGHWAY", "HWY"), Map.entry("PARKWAY", "PKWY"),
            Map.entry("NORTH", "N"), Map.entry("SOUTH", "S"),
            Map.entry("EAST", "E"), Map.entry("WEST", "W"));

    /** Mirrors the compiler's normalize_address (street part only). */
    @Nullable
    public static String normalize(String text) {
        if (text == null) {
            return null;
        }
        String s = text.split(",")[0].toUpperCase(Locale.ROOT)
                .replaceAll("[^A-Z0-9 ]", " ").trim();
        if (s.isEmpty()) {
            return null;
        }
        StringBuilder out = new StringBuilder();
        for (String t : s.split("\\s+")) {
            if (out.length() > 0) {
                out.append(' ');
            }
            out.append(SUFFIX.getOrDefault(t, t));
        }
        return out.toString();
    }

    /** Parses "e,n" geo-meter coordinates; null for anything else. */
    @Nullable
    public static Double[] parseCoords(String query) {
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("\\s*(-?[\\d.]+)\\s*,\\s*(-?[\\d.]+)\\s*")
                .matcher(query);
        return m.matches()
                ? new Double[]{Double.parseDouble(m.group(1)),
                        Double.parseDouble(m.group(2))}
                : null;
    }

    private static int cellOf(double geo) {
        return (int) Math.floor(geo / CELL);
    }

    private static long cellKey(int cx, int cz) {
        return ((long) cx << 32) | (cz & 0xFFFFFFFFL);
    }
}
