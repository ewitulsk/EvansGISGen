package com.evansgisgen.geoworld.sign;

import java.util.List;
import java.util.function.ToIntFunction;
import net.minecraft.core.BlockPos;
import net.minecraft.core.RegistryAccess;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.StringTag;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.WorldGenLevel;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.chunk.ChunkAccess;

/**
 * Plants compiled signs on posts during decoration. Signs land on the
 * first solid ground under the placement point. Their text is written as
 * pending block-entity NBT on the chunk rather than through
 * {@link SignBlockEntity#setText}: a sign block entity has no level
 * during world generation, so mutating it directly crashes in
 * {@code markUpdated}. The pending tag materializes when the chunk is
 * promoted.
 */
public final class SignPlacer {
    private SignPlacer() {
    }

    public static void place(SignIndex index, WorldGenLevel level,
            ChunkAccess chunk, ToIntFunction<BlockPos> groundAt) {
        List<SignIndex.Sign> signs =
                index.inChunk(chunk.getPos().x, chunk.getPos().z);
        if (signs.isEmpty()) {
            return;
        }
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (SignIndex.Sign sign : signs) {
            int ground = groundAt.applyAsInt(pos.set(sign.x(), 0, sign.z()));
            if (ground <= level.getMinBuildHeight()) {
                continue;
            }
            pos.set(sign.x(), ground + 1, sign.z());
            // Post + standing blade. The fence grounds the sign visually.
            level.setBlock(pos, Blocks.OAK_FENCE.defaultBlockState(), 0);
            pos.setY(ground + 2);
            level.setBlock(pos, Blocks.OAK_SIGN.defaultBlockState()
                    .setValue(BlockStateProperties.ROTATION_16,
                            sign.rot() & 15), 0);
            chunk.setBlockEntityNbt(signTag(pos, sign, level.registryAccess()));
        }
    }

    private static CompoundTag signTag(BlockPos pos, SignIndex.Sign sign,
            RegistryAccess registries) {
        ListTag messages = new ListTag();
        for (int i = 0; i < 4; i++) {
            String line = i < sign.lines().length ? sign.lines()[i] : "";
            messages.add(StringTag.valueOf(Component.Serializer.toJson(
                    Component.literal(line), registries)));
        }
        CompoundTag text = new CompoundTag();
        text.put("messages", messages);
        text.putString("color", "black");
        text.putBoolean("has_glowing_text", false);
        CompoundTag tag = new CompoundTag();
        tag.putString("id", "minecraft:sign");
        tag.putInt("x", pos.getX());
        tag.putInt("y", pos.getY());
        tag.putInt("z", pos.getZ());
        tag.put("front_text", text);
        tag.put("back_text", text.copy());
        return tag;
    }
}
