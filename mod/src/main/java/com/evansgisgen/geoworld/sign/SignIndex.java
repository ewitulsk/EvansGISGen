package com.evansgisgen.geoworld.sign;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.evansgisgen.geoworld.geo.GeoDataset;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Runtime view of a dataset's {@code signs.json} sidecar: street furniture
 * placements mined from the road ways (street-name blades at named-way
 * intersections, stop/yield posts at their OSM nodes), bucketed by chunk
 * for decoration-time placement.
 *
 * <p>Pure data index — no Minecraft types, so unit tests can load it.
 * World writes live in {@link SignPlacer}.
 */
public final class SignIndex {
    private static final Logger LOGGER = LoggerFactory.getLogger(SignIndex.class);
    private static final Gson GSON = new Gson();

    public static final SignIndex EMPTY = new SignIndex(Map.of());

    /** A compiled sign placement in block space. */
    public record Sign(int x, int z, String type, String[] lines, int rot) {}

    /** Chunk key for (chunkX, chunkZ), matching ChunkPos.asLong. */
    static long chunkKey(int chunkX, int chunkZ) {
        return (chunkX & 0xFFFFFFFFL) | ((chunkZ & 0xFFFFFFFFL) << 32);
    }

    private final Map<Long, List<Sign>> byChunk;

    private SignIndex(Map<Long, List<Sign>> byChunk) {
        this.byChunk = byChunk;
    }

    public boolean isEmpty() {
        return byChunk.isEmpty();
    }

    public int size() {
        int n = 0;
        for (List<Sign> l : byChunk.values()) {
            n += l.size();
        }
        return n;
    }

    /** Loads {@code <dataset>/signs.json}; empty when absent/unreadable. */
    public static SignIndex load(GeoDataset dataset) {
        Path root = dataset.root();
        if (root == null) {
            return EMPTY;
        }
        Path file = root.resolve("signs.json");
        if (!Files.isRegularFile(file)) {
            return EMPTY;
        }
        try {
            JsonObject doc = GSON.fromJson(Files.readString(file), JsonObject.class);
            Map<Long, List<Sign>> byChunk = new HashMap<>();
            JsonArray arr = doc.has("signs") ? doc.getAsJsonArray("signs") : new JsonArray();
            for (int i = 0; i < arr.size(); i++) {
                JsonObject s = arr.get(i).getAsJsonObject();
                int x = dataset.blockX(s.get("e").getAsDouble());
                int z = dataset.blockZ(s.get("n").getAsDouble());
                JsonArray lines = s.has("lines") ? s.getAsJsonArray("lines") : new JsonArray();
                String[] text = new String[Math.min(4, lines.size())];
                for (int j = 0; j < text.length; j++) {
                    text[j] = lines.get(j).getAsString();
                }
                Sign sign = new Sign(x, z,
                        s.has("type") ? s.get("type").getAsString() : "street_name",
                        text, s.has("rot") ? s.get("rot").getAsInt() : 0);
                byChunk.computeIfAbsent(chunkKey(x >> 4, z >> 4),
                        k -> new ArrayList<>()).add(sign);
            }
            LOGGER.info("Loaded {} sign placements from {}", arr.size(), file);
            return new SignIndex(byChunk);
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read signs {}: {}", file, e.toString());
            return EMPTY;
        }
    }

    /** Signs whose placement falls inside chunk ({@code cx}, {@code cz}). */
    public List<Sign> inChunk(int cx, int cz) {
        return byChunk.getOrDefault(chunkKey(cx, cz), List.of());
    }

    /** Nearest sign to block {@code (x, z)} within {@code radius} blocks. */
    public Optional<Sign> nearest(int x, int z, int radius) {
        int r = (radius >> 4) + 1;
        int cx = x >> 4;
        int cz = z >> 4;
        Sign best = null;
        long bestD2 = (long) radius * radius;
        for (int dx = -r; dx <= r; dx++) {
            for (int dz = -r; dz <= r; dz++) {
                for (Sign s : byChunk.getOrDefault(
                        chunkKey(cx + dx, cz + dz), List.of())) {
                    long d2 = (long) (s.x() - x) * (s.x() - x)
                            + (long) (s.z() - z) * (s.z() - z);
                    if (d2 <= bestD2) {
                        bestD2 = d2;
                        best = s;
                    }
                }
            }
        }
        return Optional.ofNullable(best);
    }
}
