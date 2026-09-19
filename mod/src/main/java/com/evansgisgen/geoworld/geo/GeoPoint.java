package com.evansgisgen.geoworld.geo;

/**
 * A position in projected geographic coordinates: meters east and meters north
 * of the dataset's projection origin. This is the offline compiler's output
 * space — the runtime never sees lat/lon.
 */
public record GeoPoint(double eastMeters, double northMeters) {}
