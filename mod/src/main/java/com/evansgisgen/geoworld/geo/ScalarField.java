package com.evansgisgen.geoworld.geo;

/**
 * A sampled scalar field over block columns (Phase 4).
 *
 * <p>Influence is the canonical instance: {@code 0.0 = completely vanilla},
 * {@code 1.0 = completely geographic}. Cities, corridors, and regions are
 * never special to terrain generation — the compiler combines them into the
 * dataset's influence layer, and this interface is how the runtime samples
 * the result. Other scalar layers (water depth, road priority, ...) can use
 * the same shape.
 */
@FunctionalInterface
public interface ScalarField {
    /** Field value at block column ({@code x}, {@code z}); range is layer-defined. */
    float sample(int x, int z);

    /** A field that is zero everywhere (no dataset / outside coverage). */
    ScalarField ZERO = (x, z) -> 0.0f;
}
