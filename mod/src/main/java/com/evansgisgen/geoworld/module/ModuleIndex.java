package com.evansgisgen.geoworld.module;

import com.evansgisgen.geoworld.geo.GeoDataset;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.ToIntFunction;
import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderGetter;
import net.minecraft.core.Vec3i;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.NbtAccounter;
import net.minecraft.nbt.NbtIo;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.ServerLevelAccessor;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.levelgen.structure.BoundingBox;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructurePlaceSettings;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Runtime view of a dataset's {@code modules.json} sidecar (Phase 21):
 * small NBT fragments (shelf aisles, checkout lanes, cart corrals)
 * stamped inside business interiors at compiler-computed placements.
 *
 * <p>Placements carry a center {@code (e, n)} and a {@link Rotation};
 * each module's rotated bounding box is indexed by chunk and rendered
 * with the same chunk-clamped {@link StructurePlaceSettings} mechanism
 * landmarks use. Rotation pivots on the module's footprint center, so
 * {@code e}/{@code n} stays the center under any rotation. The module
 * origin lands on the building's floor slab — resolved per column by
 * the generator since floor height is a per-instance solve.
 */
public final class ModuleIndex {
    private static final Logger LOGGER = LoggerFactory.getLogger(ModuleIndex.class);
    private static final Gson GSON = new Gson();

    /**
     * A module placement. {@code pivot} is the template-local center the
     * rotation turns about; {@code x}/{@code z} the world center;
     * {@code box} the rotated footprint for chunk indexing.
     */
    public record Placed(String building, StructureTemplate template,
                         Rotation rotation, BlockPos pivot,
                         int x, int z, BoundingBox box) {}

    public static final ModuleIndex EMPTY = new ModuleIndex(Map.of());

    private final Map<Long, List<Placed>> byChunk;

    private ModuleIndex(Map<Long, List<Placed>> byChunk) {
        this.byChunk = byChunk;
    }

    public boolean isEmpty() {
        return byChunk.isEmpty();
    }

    /**
     * Loads {@code <dataset>/modules.json} and its {@code modules/*.nbt}
     * templates. Needs a block registry — called lazily at first
     * decoration. Placement Y is deferred to {@link #placeChunk} (the
     * floor slab height is the generator's per-instance solve).
     */
    public static ModuleIndex load(GeoDataset dataset, HolderGetter<Block> blocks) {
        Path root = dataset.root();
        if (root == null) {
            return EMPTY;
        }
        Path file = root.resolve("modules.json");
        if (!Files.isRegularFile(file)) {
            return EMPTY;
        }
        try {
            JsonObject doc = GSON.fromJson(Files.readString(file), JsonObject.class);
            Map<String, StructureTemplate> templates = new HashMap<>();
            JsonObject mods = doc.has("modules")
                    ? doc.getAsJsonObject("modules") : new JsonObject();
            for (String name : mods.keySet()) {
                Path nbt = root.resolve(mods.get(name).getAsString());
                try {
                    CompoundTag tag =
                            NbtIo.readCompressed(nbt, NbtAccounter.unlimitedHeap());
                    StructureTemplate t = new StructureTemplate();
                    t.load(blocks, tag);
                    templates.put(name, t);
                } catch (IOException | RuntimeException e) {
                    LOGGER.warn("Failed to load module '{}': {}", name, e.toString());
                }
            }
            Map<Long, List<Placed>> byChunk = new HashMap<>();
            int placed = 0;
            for (var el : doc.getAsJsonArray("placements")) {
                JsonObject p = el.getAsJsonObject();
                StructureTemplate t = templates.get(p.get("module").getAsString());
                if (t == null) {
                    continue;
                }
                int x = dataset.blockX(p.get("e").getAsDouble());
                int z = dataset.blockZ(p.get("n").getAsDouble());
                Rotation rot = Rotation.valueOf(
                        p.has("rot") ? p.get("rot").getAsString() : "NONE");
                Vec3i size = t.getSize();
                BlockPos pivot = new BlockPos(
                        size.getX() / 2, 0, size.getZ() / 2);
                StructurePlaceSettings settings = new StructurePlaceSettings()
                        .setRotation(rot).setRotationPivot(pivot);
                BoundingBox box = t.getBoundingBox(settings,
                        new BlockPos(x, 0, z).subtract(pivot));
                Placed rec = new Placed(
                        p.has("building") ? p.get("building").getAsString() : "",
                        t, rot, pivot, x, z, box);
                for (int cx = box.minX() >> 4; cx <= box.maxX() >> 4; cx++) {
                    for (int cz = box.minZ() >> 4; cz <= box.maxZ() >> 4; cz++) {
                        byChunk.computeIfAbsent(ChunkPos.asLong(cx, cz),
                                k -> new ArrayList<>()).add(rec);
                    }
                }
                placed++;
            }
            LOGGER.info("Loaded {} module templates, {} placements from {}",
                    templates.size(), placed, file);
            return new ModuleIndex(byChunk);
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read modules {}: {}", file, e.toString());
            return EMPTY;
        }
    }

    /**
     * Stamps the chunk-intersecting slice of every module placement in
     * this chunk. {@code floorAt} resolves a column's floor-slab Y (the
     * generator's per-instance roof solve); modules stand one block
     * above it on the walk surface.
     */
    public void placeChunk(ServerLevelAccessor level, ChunkAccess chunk,
            ToIntFunction<BlockPos> floorAt) {
        List<Placed> list = byChunk.get(chunk.getPos().toLong());
        if (list == null || list.isEmpty()) {
            return;
        }
        ChunkPos cp = chunk.getPos();
        BoundingBox chunkBox = new BoundingBox(
                cp.getMinBlockX(), level.getMinBuildHeight(), cp.getMinBlockZ(),
                cp.getMaxBlockX(), level.getMaxBuildHeight() - 1, cp.getMaxBlockZ());
        for (Placed p : list) {
            int floor = floorAt.applyAsInt(
                    new BlockPos(p.x(), level.getMinBuildHeight(), p.z()));
            if (floor <= level.getMinBuildHeight()) {
                continue;  // outside the dataset's building solve — skip
            }
            BlockPos origin =
                    new BlockPos(p.x(), floor + 1, p.z()).subtract(p.pivot());
            StructurePlaceSettings settings = new StructurePlaceSettings()
                    .setRotation(p.rotation())
                    .setRotationPivot(p.pivot())
                    .setBoundingBox(chunkBox);
            RandomSource random = RandomSource.create(
                    cp.toLong() * 31L + p.box().hashCode());
            p.template().placeInWorld(level, origin, p.pivot(),
                    settings, random, Block.UPDATE_ALL);
        }
    }
}
