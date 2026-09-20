package com.evansgisgen.geoworld.landmark;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Rotation;

/**
 * A curated structure placed at a real-world position — the plan's
 * {@code Landmark} record. {@code templatePath} resolves against the
 * dataset's {@code landmarks/} directory, {@code east}/{@code north} are
 * dataset geo meters, and {@code anchor} is the template-local block that
 * sits at that geo position (its Y is the declared ground plane).
 * {@code replacesBuilding} records which imported footprint this landmark
 * supersedes; suppression is spatial (the landmark's bounding box), so the
 * id is documentary for now.
 */
public record Landmark(
        String id,
        String templatePath,
        double east,
        double north,
        BlockPos anchor,
        Rotation rotation,
        TerrainPolicy terrainPolicy,
        Optional<String> replacesBuilding) {

    public static Landmark fromJson(JsonObject o) {
        JsonObject pos = o.getAsJsonObject("position");
        JsonArray anchor = o.has("anchor")
                ? o.getAsJsonArray("anchor") : new JsonArray();
        Rotation rotation = o.has("rotation")
                ? Rotation.valueOf(o.get("rotation").getAsString()
                        .toUpperCase(Locale.ROOT))
                : Rotation.NONE;
        return new Landmark(
                o.get("id").getAsString(),
                o.get("template").getAsString(),
                pos.get("east").getAsDouble(),
                pos.get("north").getAsDouble(),
                new BlockPos(anchor.size() > 0 ? anchor.get(0).getAsInt() : 0,
                        anchor.size() > 1 ? anchor.get(1).getAsInt() : 0,
                        anchor.size() > 2 ? anchor.get(2).getAsInt() : 0),
                rotation,
                TerrainPolicy.parse(o.has("terrain")
                        ? o.get("terrain").getAsString() : null),
                o.has("replaces_building")
                        ? Optional.of(o.get("replaces_building").getAsString())
                        : Optional.empty());
    }

    public static List<Landmark> listFromJson(JsonObject root) {
        List<Landmark> out = new ArrayList<>();
        for (JsonElement el : root.getAsJsonArray("landmarks")) {
            out.add(fromJson(el.getAsJsonObject()));
        }
        return out;
    }
}
