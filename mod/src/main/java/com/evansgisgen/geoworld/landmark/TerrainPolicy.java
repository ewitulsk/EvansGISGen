package com.evansgisgen.geoworld.landmark;

import java.util.Locale;

/**
 * How a landmark reconciles its template with generated terrain.
 *
 * <ul>
 *   <li>{@code NONE} — place the template, leave terrain alone.
 *   <li>{@code LEVEL_FOUNDATION} — flatten the landmark's footprint to the
 *       anchor's ground plane: fill air pockets below it, cut terrain above
 *       it. For buildings with a declared ground floor.
 *   <li>{@code CUT_AND_FILL} — fill below the ground plane to the terrain
 *       (foundation) and cut any terrain towering above the template top
 *       inside the footprint.
 *   <li>{@code FOLLOW_TERRAIN} — don't touch terrain; extend each column's
 *       lowest template block straight down until it meets ground.
 * </ul>
 */
public enum TerrainPolicy {
    NONE,
    LEVEL_FOUNDATION,
    CUT_AND_FILL,
    FOLLOW_TERRAIN;

    public static TerrainPolicy parse(String value) {
        if (value == null) {
            return NONE;
        }
        try {
            return valueOf(value.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return NONE;
        }
    }
}
