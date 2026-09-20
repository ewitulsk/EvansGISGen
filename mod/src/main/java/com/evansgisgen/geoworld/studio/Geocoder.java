package com.evansgisgen.geoworld.studio;

import com.evansgisgen.geoworld.geo.GeoDataset;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Optional address lookup fallback for the parcel studio (Phase 12). The
 * local gazetteer in {@code parcels.json} is always tried first; this runs
 * only when configured via {@code geocoder} in {@code geoworld.json}:
 *
 * <pre>{@code
 * "geocoder": {
 *     "provider": "census" | "nominatim" | "photon",
 *     "endpoint": "https://...",        // defaults per provider
 *     "timeout_ms": 400,
 *     "autocomplete": true              // provider supports suggest
 * }
 * }</pre>
 *
 * <p>Results are lat/lon, projected to geo meters through the dataset's
 * manifest projection, then resolved to a parcel by point-in-polygon. All
 * calls are async and time-bounded; nothing here touches world generation.
 */
public final class Geocoder {
    private static final Logger LOGGER = LoggerFactory.getLogger(Geocoder.class);
    private static final Gson GSON = new Gson();

    public record Config(String provider, @Nullable String endpoint,
                         int timeoutMs, boolean autocomplete) {}

    private final Config config;
    private final GeoDataset dataset;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5)).build();

    public Geocoder(@Nullable Config config, GeoDataset dataset) {
        this.config = config;
        this.dataset = dataset;
    }

    public boolean enabled() {
        return config != null && config.provider() != null
                && !config.provider().equals("none");
    }

    public boolean canAutocomplete() {
        return enabled() && config.autocomplete()
                && !config.provider().equals("census");
    }

    /** address text -> {eastMeters, northMeters}; empty on any failure. */
    public CompletableFuture<Optional<double[]>> lookup(String query) {
        if (!enabled()) {
            return CompletableFuture.completedFuture(Optional.empty());
        }
        URI uri = URI.create(switch (config.provider()) {
            case "census" -> endpoint(
                    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress")
                    + "?address=" + enc(query)
                    + "&benchmark=Public_AR_Current&format=json";
            case "photon" -> endpoint("https://photon.komoot.io/api")
                    + "?q=" + enc(query) + "&limit=1";
            default -> endpoint("https://nominatim.openstreetmap.org/search")
                    + "?q=" + enc(query) + "&format=jsonv2&limit=1";
        });
        return get(uri).thenApply(body -> {
                    double[] ll = parseLatLon(body);
                    return ll == null ? Optional.<double[]>empty()
                            : dataset.projection()
                                    .flatMap(p -> p.toGeo(ll[0], ll[1]));
                }).exceptionally(e -> {
                    LOGGER.debug("geocoder lookup failed: {}", e.toString());
                    return Optional.empty();
                });
    }

    /** Remote autocomplete labels; empty when unsupported/disabled. */
    public CompletableFuture<List<String>> suggest(String query, int limit) {
        if (!canAutocomplete() || query == null || query.length() < 3) {
            return CompletableFuture.completedFuture(List.of());
        }
        URI uri = URI.create(switch (config.provider()) {
            case "photon" -> endpoint("https://photon.komoot.io/api")
                    + "?q=" + enc(query) + "&limit=" + limit;
            default -> endpoint("https://nominatim.openstreetmap.org/search")
                    + "?q=" + enc(query) + "&format=jsonv2&limit=" + limit;
        });
        return get(uri).thenApply(this::parseLabels)
                .exceptionally(e -> List.of());
    }

    private String endpoint(String fallback) {
        return config.endpoint() != null && !config.endpoint().isBlank()
                ? config.endpoint() : fallback;
    }

    private CompletableFuture<String> get(URI uri) {
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofMillis(Math.max(200, config.timeoutMs())))
                .header("User-Agent", "geoworld-mod/0.1 (minecraft parcel studio)")
                .GET().build();
        return http.sendAsync(req, HttpResponse.BodyHandlers.ofString())
                .thenApply(HttpResponse::body);
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    /** {lat, lon} from the provider's response shape; null when no hit. */
    @Nullable
    private double[] parseLatLon(String body) {
        try {
            return switch (config.provider()) {
                case "census" -> {
                    JsonArray matches = GSON.fromJson(body, JsonObject.class)
                            .getAsJsonObject("result")
                            .getAsJsonArray("addressMatches");
                    if (matches.isEmpty()) {
                        yield null;
                    }
                    JsonObject c = matches.get(0).getAsJsonObject()
                            .getAsJsonObject("coordinates");
                    yield new double[]{c.get("y").getAsDouble(),
                            c.get("x").getAsDouble()};
                }
                case "photon" -> {
                    JsonArray feats = GSON.fromJson(body, JsonObject.class)
                            .getAsJsonArray("features");
                    if (feats.isEmpty()) {
                        yield null;
                    }
                    JsonArray xy = feats.get(0).getAsJsonObject()
                            .getAsJsonObject("geometry")
                            .getAsJsonArray("coordinates");
                    yield new double[]{xy.get(1).getAsDouble(),
                            xy.get(0).getAsDouble()};
                }
                default -> {
                    JsonArray hits = GSON.fromJson(body, JsonArray.class);
                    if (hits.isEmpty()) {
                        yield null;
                    }
                    JsonObject hit = hits.get(0).getAsJsonObject();
                    yield new double[]{hit.get("lat").getAsDouble(),
                            hit.get("lon").getAsDouble()};
                }
            };
        } catch (RuntimeException e) {
            return null;
        }
    }

    private List<String> parseLabels(String body) {
        List<String> out = new ArrayList<>();
        try {
            if (config.provider().equals("photon")) {
                for (var f : GSON.fromJson(body, JsonObject.class)
                        .getAsJsonArray("features")) {
                    JsonObject p = f.getAsJsonObject().getAsJsonObject("properties");
                    String label = p.has("name") ? p.get("name").getAsString() : "";
                    String street = p.has("street") ? p.get("street").getAsString() : "";
                    String city = p.has("city") ? p.get("city").getAsString() : "";
                    String text = String.join(", ",
                            java.util.stream.Stream.of(label, street, city)
                                    .filter(s -> !s.isBlank()).toList());
                    if (!text.isBlank()) {
                        out.add(text);
                    }
                }
            } else {
                for (var el : GSON.fromJson(body, JsonArray.class)) {
                    out.add(el.getAsJsonObject().get("display_name").getAsString());
                }
            }
        } catch (RuntimeException ignored) {
        }
        return out;
    }
}
