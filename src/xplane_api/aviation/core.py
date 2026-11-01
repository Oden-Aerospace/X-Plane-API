"""Registered data providers and atomically replaceable spatial snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Callable, Iterable

from geographiclib.geodesic import Geodesic


LAYERS = ("airports", "navaids", "fixes", "airspace")


def layer_metadata(source: str, coverage: str = "unknown", **extra) -> dict:
    return {
        "available": True, "source": source, "coverage": coverage,
        "cycle": None, "effective_date": None, "license": "Review source terms",
        "currency": "unknown; not operational navigation data", "warnings": [],
        **extra,
    }


def _coordinates(geometry: dict) -> list[tuple[float, float]]:
    points = []

    def visit(value):
        if isinstance(value, (list, tuple)) and value:
            if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
                longitude, latitude = float(value[0]), float(value[1])
                if not math.isfinite(longitude) or not math.isfinite(latitude):
                    raise ValueError("non-finite geographic coordinate")
                if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                    raise ValueError("geographic coordinate out of bounds")
                points.append((longitude, latitude))
            else:
                for child in value:
                    visit(child)

    if geometry.get("type") not in ("Point", "Polygon", "MultiPolygon", "LineString"):
        raise ValueError("unsupported geographic geometry")
    visit(geometry.get("coordinates", []))
    if not points:
        raise ValueError("geometry has no coordinates")
    return points


def _windows(bounds: Iterable[float]) -> list[tuple[float, float, float, float]]:
    west, south, east, north = tuple(bounds)
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("bounds must be finite")
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("invalid map bounds")
    return [(west, south, east, north)] if west <= east else [
        (west, south, 180, north), (-180, south, east, north)
    ]


def range_geometry(latitude: float, longitude: float, range_nm: float) -> dict:
    _windows((longitude, latitude, longitude, latitude))
    if range_nm not in (2, 5, 10, 20, 50, 100):
        raise ValueError("unsupported map range")
    north = Geodesic.WGS84.Direct(latitude, longitude, 0, range_nm * 1852)
    east = Geodesic.WGS84.Direct(latitude, longitude, 90, range_nm * 1852 / 4)
    return {"north": [north["lat2"], north["lon2"]], "range_nm": range_nm,
            "scale_east": [east["lat2"], east["lon2"]], "scale_nm": range_nm / 4,
            "projection_limited": abs(latitude) > 85.05112878 or abs(north["lat2"]) > 85.05112878}


class AviationDataset:
    def __init__(self, provider: str, features: Iterable[dict], layers: dict) -> None:
        self.provider = provider
        self.layers = json.loads(json.dumps(layers, allow_nan=False))
        for info in self.layers.values():
            if info.get("valid_until"):
                expiry = datetime.fromisoformat(info["valid_until"]).date()
                if expiry < datetime.now(timezone.utc).date():
                    info["currency"] = "expired according to source-provided valid_until"
        self.imported_at = datetime.now(timezone.utc).isoformat()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.execute("CREATE TABLE features (id TEXT UNIQUE, layer TEXT, body TEXT)")
        self._db.execute("CREATE VIRTUAL TABLE spatial USING rtree(id, west, east, south, north)")
        counts = {layer: 0 for layer in LAYERS}
        for feature in features:
            layer = feature.get("properties", {}).get("layer")
            if layer not in LAYERS or not isinstance(feature.get("id"), str):
                raise ValueError("feature requires a string id and a supported layer")
            points = _coordinates(feature["geometry"])
            for runway in feature["properties"].get("runways", []):
                if runway.get("coordinates"):
                    _coordinates({"type": "LineString", "coordinates": runway["coordinates"]})
            if layer != "airspace" and feature["geometry"]["type"] != "Point":
                raise ValueError("airport, navaid and fix features must be points")
            if layer == "airspace" and feature["geometry"]["type"] not in ("Polygon", "MultiPolygon"):
                raise ValueError("airspace must be a polygon")
            body = json.dumps(feature, allow_nan=False, separators=(",", ":"))
            try:
                cursor = self._db.execute("INSERT INTO features VALUES (?, ?, ?)", (feature["id"], layer, body))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"duplicate feature identifier: {feature['id']}") from exc
            longitudes, latitudes = zip(*points)
            west, east = min(longitudes), max(longitudes)
            if east - west > 180:
                west, east = -180, 180
            self._db.execute("INSERT INTO spatial VALUES (?, ?, ?, ?, ?)", (
                cursor.lastrowid, west, east, min(latitudes), max(latitudes)
            ))
            counts[layer] += 1
        self._db.commit()
        for layer in LAYERS:
            self.layers.setdefault(layer, {"available": False, "reason": "not supplied by this provider"})
            self.layers[layer]["count"] = counts[layer]

    def metadata(self) -> dict:
        return json.loads(json.dumps({
            "provider": self.provider, "imported_at": self.imported_at,
            "layers": self.layers, "simulation_only": True,
        }))

    def query(self, bounds: Iterable[float], layers: Iterable[str] = LAYERS, limit: int = 1200) -> dict:
        selected = tuple(layers)
        if not selected or any(layer not in LAYERS for layer in selected):
            raise ValueError("unknown or empty layer selection")
        if not 1 <= limit <= 2000:
            raise ValueError("feature limit must be between 1 and 2000")
        windows = _windows(bounds)
        found = {}
        with self._lock:
            for west, south, east, north in windows:
                placeholders = ",".join("?" for _ in selected)
                rows = self._db.execute(
                    f"SELECT f.id, f.body FROM features f JOIN spatial s ON f.rowid=s.id "
                    f"WHERE f.layer IN ({placeholders}) AND s.west<=? AND s.east>=? "
                    "AND s.south<=? AND s.north>=? ORDER BY f.id LIMIT ?",
                    (*selected, east, west, north, south, limit + 1),
                )
                for identifier, body in rows:
                    found[identifier] = body
        return {
            "type": "FeatureCollection", "provider": self.provider,
            "imported_at": self.imported_at, "available": True,
            "features": [json.loads(body) for body in list(found.values())[:limit]],
            "truncated": len(found) > limit,
        }

    def detail(self, identifier: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM features WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def nearest(self, latitude: float, longitude: float) -> list[dict]:
        _windows((longitude, latitude, longitude, latitude))
        radius_m = 100 * 1852
        delta = radius_m / 110000
        south, north = max(-90, latitude - delta), min(90, latitude + delta)
        cosine = math.cos(math.radians(max(abs(south), abs(north))))
        longitude_delta = min(180, delta / max(0.000001, cosine))
        if longitude_delta == 180:
            west, east = -180, 180
        else:
            west = (longitude - longitude_delta + 180) % 360 - 180
            east = (longitude + longitude_delta + 180) % 360 - 180
        candidates = self.query((west, south, east, north), ("airports",), 2000)
        if candidates["truncated"]:
            raise ValueError("too many nearby airports; dataset exceeds query limit")
        airports = []
        for feature in candidates["features"]:
            target_lon, target_lat = feature["geometry"]["coordinates"][:2]
            inverse = Geodesic.WGS84.Inverse(latitude, longitude, target_lat, target_lon)
            if inverse["s12"] <= radius_m:
                airports.append({
                    "id": feature["id"], **feature["properties"],
                    "distance_nm": inverse["s12"] / 1852,
                    "bearing_true_deg": inverse["azi1"] % 360,
                })
        return sorted(airports, key=lambda airport: (airport["distance_nm"], airport["id"]))[:10]


class AviationManager:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path).resolve() if config_path else None
        self._providers: dict[str, Callable[[dict], AviationDataset]] = {}
        self._lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._active: AviationDataset | None = None
        self._requested = "xplane"
        self._error: str | None = "No aviation dataset configured"

    def register(self, identifier: str, importer: Callable[[dict], AviationDataset]) -> None:
        if not identifier or identifier in self._providers or not callable(importer):
            raise ValueError("provider must have a unique identifier and an importer")
        self._providers[identifier] = importer

    def active(self) -> AviationDataset | None:
        with self._lock:
            return self._active

    def status(self) -> dict:
        with self._lock:
            return {
                "requested_provider": self._requested, "error": self._error,
                "active": self._active.metadata() if self._active else None,
                "providers": sorted(self._providers),
            }

    def reload(self) -> dict:
        with self._reload_lock:
            requested = "xplane"
            try:
                config = json.loads(self.config_path.read_text()) if self.config_path else {}
                if not isinstance(config, dict):
                    raise ValueError("aviation configuration must be a JSON object")
                requested = config.get("provider", "xplane")
                if not isinstance(requested, str) or requested not in self._providers:
                    raise ValueError(f"unknown aviation provider: {requested!r}")
                config = dict(config)
                config.pop("provider", None)
                config["base_path"] = self.config_path.parent if self.config_path else Path.cwd()
                replacement = self._providers[requested](config)
                if replacement.provider != requested:
                    raise ValueError("importer returned a different provider identifier")
                with self._lock:
                    self._active = replacement
                    self._requested = requested
                    self._error = None
            except Exception as exc:
                with self._lock:
                    self._requested = str(requested)
                    self._error = str(exc)
        return self.status()