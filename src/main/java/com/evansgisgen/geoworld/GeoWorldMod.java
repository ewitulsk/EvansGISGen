package com.evansgisgen.geoworld;

import com.evansgisgen.geoworld.worldgen.GeoChunkGenerator;
import com.evansgisgen.geoworld.worldgen.GeoChunkGeneratorCodec;
import com.mojang.serialization.MapCodec;
import net.minecraft.core.registries.Registries;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;

@Mod(GeoWorldMod.MODID)
public final class GeoWorldMod {
    public static final String MODID = "geoworld";

    private static final DeferredRegister<MapCodec<? extends ChunkGenerator>> CHUNK_GENERATORS =
            DeferredRegister.create(Registries.CHUNK_GENERATOR, MODID);

    public static final DeferredHolder<MapCodec<? extends ChunkGenerator>, MapCodec<GeoChunkGenerator>> GEO_CHUNK_GENERATOR =
            CHUNK_GENERATORS.register("geoworld", () -> GeoChunkGeneratorCodec.INSTANCE);

    public GeoWorldMod(IEventBus modEventBus) {
        CHUNK_GENERATORS.register(modEventBus);
    }
}
