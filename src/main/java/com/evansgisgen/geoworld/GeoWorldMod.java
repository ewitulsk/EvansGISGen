package com.evansgisgen.geoworld;

import com.evansgisgen.geoworld.debug.GeoWorldCommands;
import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoTransform;
import com.evansgisgen.geoworld.geo.GeoWorldConfig;
import com.evansgisgen.geoworld.worldgen.GeoChunkGenerator;
import com.evansgisgen.geoworld.worldgen.GeoChunkGeneratorCodec;
import com.mojang.serialization.MapCodec;
import net.minecraft.core.registries.Registries;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Mod(GeoWorldMod.MODID)
public final class GeoWorldMod {
    public static final String MODID = "geoworld";
    private static final Logger LOGGER = LoggerFactory.getLogger(GeoWorldMod.class);

    private static final DeferredRegister<MapCodec<? extends ChunkGenerator>> CHUNK_GENERATORS =
            DeferredRegister.create(Registries.CHUNK_GENERATOR, MODID);

    public static final DeferredHolder<MapCodec<? extends ChunkGenerator>, MapCodec<GeoChunkGenerator>> GEO_CHUNK_GENERATOR =
            CHUNK_GENERATORS.register("geoworld", () -> GeoChunkGeneratorCodec.INSTANCE);

    private static GeoDataset dataset = GeoDataset.empty();

    public GeoWorldMod(IEventBus modEventBus) {
        dataset = new GeoDataset(GeoWorldConfig.load());
        GeoTransform t = dataset.transform();
        LOGGER.info("GeoWorld transform: origin=({}, {}) scale=({} horiz, {} vert m/block) datum=({} m -> y{})",
                t.originX(), t.originZ(), t.horizontalMetersPerBlock(), t.verticalMetersPerBlock(),
                t.datumElevationMeters(), t.datumY());
        CHUNK_GENERATORS.register(modEventBus);
        NeoForge.EVENT_BUS.addListener(GeoWorldCommands::register);
    }

    public static GeoDataset dataset() {
        return dataset;
    }

    public static GeoTransform transform() {
        return dataset.transform();
    }
}
