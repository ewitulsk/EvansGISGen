package com.evansgisgen.geoworld.studio;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderLookup;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.util.datafix.DataFixTypes;
import net.minecraft.world.level.saveddata.SavedData;
import org.jetbrains.annotations.Nullable;

/**
 * Persistent studio state (Phase 12): which parcel owns which studio lot
 * slot, and which player is editing which lot. Stored on the overworld's
 * {@code DimensionDataStorage} so sessions survive restarts and lot slots
 * never collide.
 */
public final class StudioData extends SavedData {
    private static final String NAME = "geoworld_studio";

    /** Lot slots are spaced on a fixed grid — no overlap, ever. */
    static final int SLOT_STRIDE = 512;
    private static final int SLOTS_PER_ROW = 64;

    /** One player's active studio session. */
    public record Session(String parcelId, BlockPos lotOrigin,
                          ResourceLocation returnDim, BlockPos returnPos,
                          float returnYaw, float returnPitch) {}

    private final Map<String, BlockPos> lotSlots = new HashMap<>();
    private final Map<UUID, Session> sessions = new HashMap<>();
    private int nextSlot = 0;

    public static StudioData get(ServerLevel overworld) {
        return overworld.getDataStorage().computeIfAbsent(
                new SavedData.Factory<>(StudioData::new,
                        (tag, registries) -> load(tag), DataFixTypes.LEVEL),
                NAME);
    }

    /** The studio min corner assigned to a parcel, allocating if new. */
    public BlockPos lotSlot(String parcelId) {
        BlockPos slot = lotSlots.get(parcelId);
        if (slot == null) {
            int i = nextSlot++;
            slot = new BlockPos((i % SLOTS_PER_ROW) * SLOT_STRIDE, 0,
                    (i / SLOTS_PER_ROW) * SLOT_STRIDE);
            lotSlots.put(parcelId, slot);
            setDirty();
        }
        return slot;
    }

    @Nullable
    public BlockPos existingLot(String parcelId) {
        return lotSlots.get(parcelId);
    }

    public void beginSession(UUID player, Session session) {
        sessions.put(player, session);
        setDirty();
    }

    @Nullable
    public Session session(UUID player) {
        return sessions.get(player);
    }

    @Nullable
    public Session endSession(UUID player) {
        Session s = sessions.remove(player);
        if (s != null) {
            setDirty();
        }
        return s;
    }

    /** The parcel id a lot slot is staged for, or null. */
    @Nullable
    public String parcelAt(BlockPos lotOrigin) {
        for (var e : lotSlots.entrySet()) {
            if (e.getValue().equals(lotOrigin)) {
                return e.getKey();
            }
        }
        return null;
    }

    private static StudioData load(CompoundTag tag) {
        StudioData data = new StudioData();
        data.nextSlot = tag.getInt("next_slot");
        CompoundTag lots = tag.getCompound("lots");
        for (String key : lots.getAllKeys()) {
            int[] p = lots.getIntArray(key);
            if (p.length == 3) {
                data.lotSlots.put(key, new BlockPos(p[0], p[1], p[2]));
            }
        }
        CompoundTag sess = tag.getCompound("sessions");
        for (String key : sess.getAllKeys()) {
            CompoundTag s = sess.getCompound(key);
            int[] lot = s.getIntArray("lot");
            int[] ret = s.getIntArray("return_pos");
            if (lot.length == 3 && ret.length == 3) {
                data.sessions.put(UUID.fromString(key), new Session(
                        s.getString("parcel"),
                        new BlockPos(lot[0], lot[1], lot[2]),
                        ResourceLocation.parse(s.getString("return_dim")),
                        new BlockPos(ret[0], ret[1], ret[2]),
                        s.getFloat("return_yaw"), s.getFloat("return_pitch")));
            }
        }
        return data;
    }

    @Override
    public CompoundTag save(CompoundTag tag, HolderLookup.Provider registries) {
        tag.putInt("next_slot", nextSlot);
        CompoundTag lots = new CompoundTag();
        for (var e : lotSlots.entrySet()) {
            lots.putIntArray(e.getKey(), new int[]{
                    e.getValue().getX(), e.getValue().getY(), e.getValue().getZ()});
        }
        tag.put("lots", lots);
        CompoundTag sess = new CompoundTag();
        for (var e : sessions.entrySet()) {
            Session s = e.getValue();
            CompoundTag t = new CompoundTag();
            t.putString("parcel", s.parcelId());
            t.putIntArray("lot", new int[]{
                    s.lotOrigin().getX(), s.lotOrigin().getY(), s.lotOrigin().getZ()});
            t.putString("return_dim", s.returnDim().toString());
            t.putIntArray("return_pos", new int[]{
                    s.returnPos().getX(), s.returnPos().getY(), s.returnPos().getZ()});
            t.putFloat("return_yaw", s.returnYaw());
            t.putFloat("return_pitch", s.returnPitch());
            sess.put(e.getKey().toString(), t);
        }
        tag.put("sessions", sess);
        return tag;
    }
}
