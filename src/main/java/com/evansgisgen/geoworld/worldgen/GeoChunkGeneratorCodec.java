package com.evansgisgen.geoworld.worldgen;

import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;
import net.minecraft.world.level.chunk.ChunkGenerator;

/**
 * Serialized form of {@link GeoChunkGenerator}: wraps an arbitrary vanilla
 * {@link ChunkGenerator} under the "delegate" key, e.g.
 *
 * <pre>{@code
 * {
 *     "type": "geoworld:geoworld",
 *     "delegate": {
 *         "type": "minecraft:noise",
 *         "biome_source": { "type": "minecraft:multi_noise", "preset": "minecraft:overworld" },
 *         "settings": "minecraft:overworld"
 *     }
 * }
 * }</pre>
 */
public final class GeoChunkGeneratorCodec {
    private GeoChunkGeneratorCodec() {}

    public static final MapCodec<GeoChunkGenerator> INSTANCE = RecordCodecBuilder.mapCodec(instance ->
            instance.group(
                    ChunkGenerator.CODEC.fieldOf("delegate").forGetter(GeoChunkGenerator::delegate)
            ).apply(instance, GeoChunkGenerator::new));
}
