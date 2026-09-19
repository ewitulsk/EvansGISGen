package com.evansgisgen.geoworld.geo;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import net.neoforged.fml.loading.FMLPaths;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Loads {@code config/geoworld.json}, which controls where the projected
 * geographic origin is inserted into the Minecraft world and at what scale:
 *
 * <pre>{@code
 * {
 *     "origin": { "minecraft_x": 0, "minecraft_z": 0 },
 *     "scale": { "horizontal_meters_per_block": 1.0, "vertical_meters_per_block": 1.0 },
 *     "vertical_datum": { "elevation_meters": 0.0, "minecraft_y": 64 }
 * }
 * }</pre>
 */
public final class GeoWorldConfig {
    private static final Logger LOGGER = LoggerFactory.getLogger(GeoWorldConfig.class);
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static final String FILE_NAME = "geoworld.json";

    private GeoWorldConfig() {}

    public static Path path() {
        return FMLPaths.CONFIGDIR.get().resolve(FILE_NAME);
    }

    public static GeoTransform load() {
        Path path = path();
        try {
            if (!Files.exists(path)) {
                Files.writeString(path, defaultJson());
                LOGGER.info("Wrote default GeoWorld config to {}", path);
                return GeoTransform.DEFAULT;
            }
            JsonObject root = GSON.fromJson(Files.readString(path), JsonObject.class);
            return parse(root);
        } catch (IOException | JsonParseException | NullPointerException e) {
            LOGGER.warn("Failed to load {}, using defaults: {}", path, e.toString());
            return GeoTransform.DEFAULT;
        }
    }

    private static GeoTransform parse(JsonObject root) {
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
