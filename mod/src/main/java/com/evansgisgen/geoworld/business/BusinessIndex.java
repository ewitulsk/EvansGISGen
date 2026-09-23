package com.evansgisgen.geoworld.business;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.evansgisgen.geoworld.geo.GeoDataset;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import net.minecraft.core.HolderGetter;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Runtime view of a dataset's {@code businesses.json} sidecar (Phase 19):
 * known-business facade palettes and layout keys, keyed by the {@code
 * business} tile-layer id. The generator consults it per column before
 * the class palette — a Walmart is a Walmart even when the OSM footprint
 * classified as generic retail.
 */
public final class BusinessIndex {
    private static final Logger LOGGER = LoggerFactory.getLogger(BusinessIndex.class);
    private static final Gson GSON = new Gson();

    public static final BusinessIndex EMPTY = new BusinessIndex(Map.of(), Map.of());

    /**
     * Facade spec for a business. Null components inherit the class
     * palette; {@code accentY} names the accent-band placement
     * ({@code "parapet"} = the top two wall courses).
     */
    public record Facade(@Nullable BlockState wall, @Nullable BlockState accent,
                         @Nullable String accentY, @Nullable BlockState floor,
                         @Nullable BlockState roof, @Nullable BlockState window) {}

    private final Map<Integer, Facade> facades;
    private final Map<Integer, String> layouts;

    private BusinessIndex(Map<Integer, Facade> facades, Map<Integer, String> layouts) {
        this.facades = facades;
        this.layouts = layouts;
    }

    public boolean isEmpty() {
        return facades.isEmpty();
    }

    /** Facade for a business layer id, or null when unknown. */
    @Nullable
    public Facade facade(int businessId) {
        return facades.get(businessId);
    }

    /** Interior layout key for a business layer id, or null. */
    @Nullable
    public String layout(int businessId) {
        return layouts.get(businessId);
    }

    /** Loads {@code <dataset>/businesses.json}; empty when absent/unreadable. */
    public static BusinessIndex load(GeoDataset dataset, HolderGetter<Block> blocks) {
        Path root = dataset.root();
        if (root == null) {
            return EMPTY;
        }
        Path file = root.resolve("businesses.json");
        if (!Files.isRegularFile(file)) {
            return EMPTY;
        }
        try {
            JsonObject doc = GSON.fromJson(Files.readString(file), JsonObject.class);
            Map<Integer, Facade> facades = new HashMap<>();
            Map<Integer, String> layouts = new HashMap<>();
            JsonObject reg = doc.has("businesses")
                    ? doc.getAsJsonObject("businesses") : new JsonObject();
            for (String key : reg.keySet()) {
                JsonObject b = reg.getAsJsonObject(key);
                int id = b.has("id") ? b.get("id").getAsInt() : 0;
                if (id <= 0) {
                    continue;
                }
                JsonObject pal = b.has("palette")
                        ? b.getAsJsonObject("palette") : new JsonObject();
                facades.put(id, new Facade(
                        block(blocks, pal, "wall"),
                        block(blocks, pal, "accent"),
                        pal.has("accent_y") ? pal.get("accent_y").getAsString() : null,
                        block(blocks, pal, "floor"),
                        block(blocks, pal, "roof"),
                        block(blocks, pal, "window")));
                if (b.has("layout") && b.get("layout").isJsonPrimitive()) {
                    layouts.put(id, b.get("layout").getAsString());
                }
            }
            LOGGER.info("Loaded {} business facade entries from {}",
                    facades.size(), file);
            return new BusinessIndex(facades, layouts);
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read businesses {}: {}", file, e.toString());
            return EMPTY;
        }
    }

    @Nullable
    private static BlockState block(HolderGetter<Block> blocks, JsonObject pal,
                                    String key) {
        if (!pal.has(key) || !pal.get(key).isJsonPrimitive()) {
            return null;
        }
        String name = pal.get(key).getAsString();
        ResourceLocation rl = ResourceLocation.tryParse(
                name.contains(":") ? name : "minecraft:" + name);
        if (rl == null) {
            return null;
        }
        return blocks.get(ResourceKey.create(Registries.BLOCK, rl))
                .map(h -> h.value().defaultBlockState())
                .filter(s -> !s.isAir())
                .orElse(null);
    }
}
