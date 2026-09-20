package com.evansgisgen.geoworld.geo;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import net.neoforged.fml.loading.FMLPaths;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Loads {@code config/geoworld.json}:
 *
 * <pre>{@code
 * {
 *     "dataset_path": "geoworld/beatrice.geoworld",
 *     "origin": { "minecraft_x": 0, "minecraft_z": 0 },
 *     "scale": { "horizontal_meters_per_block": 1.0, "vertical_meters_per_block": 1.0 },
 *     "vertical_datum": { "elevation_meters": 0.0, "minecraft_y": 64 }
 * }
 * }</pre>
 *
 * <p>{@code dataset_path} points at a compiled {@code .geoworld} directory,
 * resolved relative to the game directory when not absolute. The dataset's own
 * manifest supplies the transform when present; the config's origin/scale
 * fields are the fallback (and drive the Phase 0-style fallback deformation
 * when no dataset is loaded).
 */
public final class GeoWorldConfig {
    private static final Logger LOGGER = LoggerFactory.getLogger(GeoWorldConfig.class);
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static final String FILE_NAME = "geoworld.json";

    public record LoadedConfig(GeoTransform transform, @Nullable Path datasetPath,
                               @Nullable com.evansgisgen.geoworld.studio.Geocoder.Config geocoder) {}

    private GeoWorldConfig() {}

    public static Path path() {
        return FMLPaths.CONFIGDIR.get().resolve(FILE_NAME);
    }

    public static LoadedConfig load() {
        Path path = path();
        JsonObject root;
        try {
            if (!Files.exists(path)) {
                Files.writeString(path, defaultJson());
                LOGGER.info("Wrote default GeoWorld config to {}", path);
                root = GSON.fromJson(defaultJson(), JsonObject.class);
            } else {
                root = GSON.fromJson(Files.readString(path), JsonObject.class);
            }
        } catch (IOException | JsonParseException | NullPointerException e) {
            LOGGER.warn("Failed to load {}, using defaults: {}", path, e.toString());
            root = GSON.fromJson(defaultJson(), JsonObject.class);
        }
        return new LoadedConfig(parseTransform(root), parseDatasetPath(root),
                parseGeocoder(root));
    }

    /**
     * Optional runtime geocoder for studio address lookup. Absent (or
     * {@code "provider": "none"}) disables the HTTP path entirely — parcel
     * gazetteer and coordinates still work offline.
     */
    @Nullable
    private static com.evansgisgen.geoworld.studio.Geocoder.Config parseGeocoder(
            JsonObject root) {
        JsonObject g = section(root, "geocoder");
        String provider = g.has("provider") ? g.get("provider").getAsString() : null;
        if (provider == null || provider.isBlank() || provider.equals("none")) {
            return null;
        }
        return new com.evansgisgen.geoworld.studio.Geocoder.Config(
                provider,
                g.has("endpoint") ? g.get("endpoint").getAsString() : null,
                g.has("timeout_ms") ? g.get("timeout_ms").getAsInt() : 400,
                !g.has("autocomplete") || g.get("autocomplete").getAsBoolean());
    }

    public static GeoTransform parseTransform(JsonObject root) {
        JsonObject origin = section(root, "origin");
        JsonObject scale = section(root, "scale");
        JsonObject datum = section(root, "vertical_datum");
        return new GeoTransform(
                getInt(origin, "minecraft_x", GeoTransform.DEFAULT.originX()),
                getInt(origin, "minecraft_z", GeoTransform.DEFAULT.originZ()),
                getDouble(scale, "horizontal_meters_per_block", GeoTransform.DEFAULT.horizontalMetersPerBlock()),
                getDouble(scale, "vertical_meters_per_block", GeoTransform.DEFAULT.verticalMetersPerBlock()),
                getDouble(datum, "elevation_meters", GeoTransform.DEFAULT.datumElevationMeters()),
                getInt(datum, "minecraft_y", GeoTransform.DEFAULT.datumY()));
    }

    @Nullable
    private static Path parseDatasetPath(JsonObject root) {
        if (root == null || !root.has("dataset_path")) {
            return null;
        }
        String raw = root.get("dataset_path").getAsString();
        if (raw == null || raw.isBlank()) {
            return null;
        }
        Path p = Path.of(raw);
        return p.isAbsolute() ? p : FMLPaths.GAMEDIR.get().resolve(p);
    }

    private static JsonObject section(JsonObject root, String name) {
        return root != null && root.has(name) && root.get(name).isJsonObject()
                ? root.getAsJsonObject(name)
                : new JsonObject();
    }

    private static int getInt(JsonObject obj, String key, int fallback) {
        return obj.has(key) ? obj.get(key).getAsInt() : fallback;
    }

    private static double getDouble(JsonObject obj, String key, double fallback) {
        return obj.has(key) ? obj.get(key).getAsDouble() : fallback;
    }

    private static String defaultJson() {
        JsonObject root = new JsonObject();
        root.addProperty("dataset_path", "geoworld/beatrice.geoworld");
        JsonObject origin = new JsonObject();
        origin.addProperty("minecraft_x", GeoTransform.DEFAULT.originX());
        origin.addProperty("minecraft_z", GeoTransform.DEFAULT.originZ());
        JsonObject scale = new JsonObject();
        scale.addProperty("horizontal_meters_per_block", GeoTransform.DEFAULT.horizontalMetersPerBlock());
        scale.addProperty("vertical_meters_per_block", GeoTransform.DEFAULT.verticalMetersPerBlock());
        JsonObject datum = new JsonObject();
        datum.addProperty("elevation_meters", GeoTransform.DEFAULT.datumElevationMeters());
        datum.addProperty("minecraft_y", GeoTransform.DEFAULT.datumY());
        root.add("origin", origin);
        root.add("scale", scale);
        root.add("vertical_datum", datum);
        return GSON.toJson(root);
    }
}
