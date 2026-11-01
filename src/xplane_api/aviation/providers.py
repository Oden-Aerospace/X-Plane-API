"""Read-only X-Plane and public-dataset adapters. No network access."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re

from geographiclib.geodesic import Geodesic

from .core import AviationDataset, AviationManager, layer_metadata


def _options(config: dict, allowed: set[str]) -> Path:
    unknown = config.keys() - allowed - {"base_path", "acknowledge_data_terms"}
    if unknown:
        raise ValueError(f"unknown provider options: {sorted(unknown)}")
    if config.get("acknowledge_data_terms") is not True:
        raise ValueError("Set acknowledge_data_terms=true only after confirming permitted dataset use")
    return Path(config.get("base_path", Path.cwd()))


def _path(base: Path, value) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("dataset locations must be nonempty path strings")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _number(value):
    if value is None or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite dataset value")
    return result


def _point(layer: str, identifier: str, latitude, longitude, **properties) -> dict:
    return {"type": "Feature", "id": f"{layer}:{identifier}", "geometry": {
        "type": "Point", "coordinates": [float(longitude), float(latitude)]},
        "properties": {"layer": layer, "ident": identifier, **properties}}


def _records(path: Path, versions: tuple[str, ...]):
    with path.open(encoding="utf-8-sig", errors="strict") as stream:
        stream.readline()
        header = stream.readline().strip()
        if not header.startswith(versions):
            raise ValueError(f"unsupported X-Plane format in {path}: {header}")
        for line in stream:
            tokens = line.strip().split()
            if tokens and tokens[0] != "99":
                yield tokens


def _header_metadata(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as stream:
        stream.readline()
        header = stream.readline().strip()
    cycle = re.search(r"data cycle (\d+)", header)
    return layer_metadata(str(path), "installed dataset; coverage unverified",
                          cycle=cycle.group(1) if cycle else None, attribution=header)


def _airports(path: Path) -> dict[str, dict]:
    airports = {}
    current = None

    def finish():
        if current is None:
            return
        latitude, longitude = current.pop("latitude", None), current.pop("longitude", None)
        position_source = "airport datum"
        if latitude is None or longitude is None:
            runway = next((item for item in current["runways"] if item.get("coordinates")), None)
            if runway is None:
                return
            start, end = runway["coordinates"]
            inverse = Geodesic.WGS84.Inverse(start[1], start[0], end[1], end[0])
            midpoint = Geodesic.WGS84.Direct(start[1], start[0], inverse["azi1"], inverse["s12"] / 2)
            latitude, longitude = midpoint["lat2"], midpoint["lon2"]
            position_source = "runway midpoint; airport datum unavailable"
        identifier = current["ident"]
        properties = dict(current)
        properties.pop("ident")
        airports[identifier] = _point("airports", identifier, latitude, longitude,
                                      position_source=position_source, **properties)

    for tokens in _records(path, ("1200 ", "1130 ", "1100 ")):
        code = tokens[0]
        if code in ("1", "16", "17"):
            finish()
            current = {"ident": tokens[4], "name": " ".join(tokens[5:]),
                       "elevation_ft": _number(tokens[1]), "runways": [], "frequencies": []}
        elif current is not None:
            if code == "1302" and len(tokens) >= 3:
                if tokens[1] in ("datum_lat", "datum_lon"):
                    current["latitude" if tokens[1] == "datum_lat" else "longitude"] = float(tokens[2])
                elif tokens[1] in ("icao_code", "iata_code", "local_code", "region_code"):
                    current[tokens[1]] = tokens[2]
            elif code in ("100", "101", "102"):
                if code == "100":
                    start, end = [float(tokens[10]), float(tokens[9])], [float(tokens[19]), float(tokens[18])]
                    identifiers, width, surface = [tokens[8], tokens[17]], float(tokens[1]), tokens[2]
                elif code == "101":
                    start, end = [float(tokens[5]), float(tokens[4])], [float(tokens[8]), float(tokens[7])]
                    identifiers, width, surface = [tokens[3], tokens[6]], float(tokens[1]), "water"
                else:
                    start = end = [float(tokens[3]), float(tokens[2])]
                    identifiers, width, surface = [tokens[1]], float(tokens[6]), tokens[7]
                length = float(tokens[5]) if code == "102" else Geodesic.WGS84.Inverse(start[1], start[0], end[1], end[0])["s12"]
                surfaces = {"1": "asphalt", "2": "concrete", "3": "grass", "4": "dirt",
                            "5": "gravel", "12": "dry lakebed", "13": "water", "14": "snow/ice"}
                current["runways"].append({"identifiers": identifiers, "width_m": width,
                    "length_m": length, "surface": surfaces.get(surface, surface), "coordinates": [start, end]})
            elif code.isdigit() and (50 <= int(code) <= 56 or 1050 <= int(code) <= 1056):
                current["frequencies"].append({"description": " ".join(tokens[2:]),
                    "frequency_mhz": float(tokens[1]) / (1000 if int(code) >= 1000 else 100)})
    finish()
    return airports


def _navigation(path: Path, layer: str):
    for tokens in _records(path, ("1200 ", "1150 ", "1101 ", "1100 ")):
        if layer == "fixes":
            identifier, terminal, region = tokens[2:5]
            yield _point(layer, f"{identifier}:{terminal}:{region}", tokens[0], tokens[1],
                         ident=identifier, name=identifier, terminal=terminal, region=region)
        elif tokens[0] in ("2", "3", "12", "13"):
            identifier, terminal, region = tokens[7:10]
            key = f"{identifier}:{terminal}:{region}:{tokens[0]}:{tokens[1]}:{tokens[2]}"
            yield _point(layer, key, tokens[1], tokens[2], ident=identifier,
                         name=" ".join(tokens[10:]), kind={"2": "NDB", "3": "VOR", "12": "DME", "13": "DME"}[tokens[0]],
                         frequency=float(tokens[4]) / (1 if tokens[0] == "2" else 100),
                         frequency_unit="kHz" if tokens[0] == "2" else "MHz", region=region, terminal=terminal)


def _dms_pairs(text: str) -> list[list[float]]:
    matches = re.findall(r"(\d{1,3}):([\d.]+)(?::([\d.]+))?\s*([NSEW])", text.upper())
    if not matches or len(matches) % 2:
        raise ValueError(f"unsupported OpenAIR coordinate: {text}")
    coordinates = []
    for index in range(0, len(matches), 2):
        values = []
        for degrees, minutes, seconds, hemisphere in matches[index:index + 2]:
            if not 0 <= float(minutes) < 60 or not 0 <= float(seconds or 0) < 60:
                raise ValueError("invalid OpenAIR minutes or seconds")
            value = float(degrees) + float(minutes) / 60 + float(seconds or 0) / 3600
            values.append(value * (-1 if hemisphere in "SW" else 1))
        if matches[index][3] not in "NS" or matches[index + 1][3] not in "EW":
            raise ValueError("OpenAIR coordinates must be latitude then longitude")
        coordinates.append([values[1], values[0]])
    return coordinates


def _arc(center: list[float], radius_nm: float, start: float, sweep: float) -> list[list[float]]:
    steps = max(1, math.ceil(abs(sweep) / 5))
    if not 0 < radius_nm <= 2000:
        raise ValueError("invalid OpenAIR radius")
    points = []
    for index in range(steps + 1):
        result = Geodesic.WGS84.Direct(center[1], center[0], start + sweep * index / steps, radius_nm * 1852)
        points.append([result["lon2"], result["lat2"]])
    return points


def _airspace(path: Path):
    current = None
    coordinates = []
    center, direction = None, 1
    counter = 0

    def finish():
        if current is None:
            return None
        if len(coordinates) < 3:
            raise ValueError(f"airspace has insufficient boundary points: {current.get('name')}")
        ring = list(coordinates)
        if ring[-1] != ring[0]:
            ring.append(ring[0])
        return {"type": "Feature", "id": f"airspace:{counter}", "geometry": {
            "type": "Polygon", "coordinates": [ring]}, "properties": {"layer": "airspace", **current}}

    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            text = line.split("*", 1)[0].strip()
            if not text:
                continue
            code, _, value = text.partition(" ")
            value = value.strip()
            if code == "AC":
                completed = finish()
                if completed:
                    yield completed
                counter += 1
                current, coordinates, center, direction = {"class": value, "name": None, "floor": None, "ceiling": None}, [], None, 1
            elif current is not None:
                if code in ("AN", "AL", "AH"):
                    current[{"AN": "name", "AL": "floor", "AH": "ceiling"}[code]] = value
                elif code == "DP":
                    coordinates.extend(_dms_pairs(value))
                elif code == "V":
                    variable, _, setting = value.partition("=")
                    if variable.strip() == "X":
                        center = _dms_pairs(setting)[0]
                    elif variable.strip() == "D":
                        direction = -1 if setting.strip() == "-" else 1
                elif code in ("DC", "DA", "DB"):
                    if center is None:
                        raise ValueError("OpenAIR arc/circle requires V X center")
                    if code == "DC":
                        coordinates.extend(_arc(center, float(value), 0, 360))
                    elif code == "DA":
                        radius, start, end = map(float, value.split(","))
                        sweep = ((end - start) * direction % 360) * direction
                        coordinates.extend(_arc(center, radius, start, sweep))
                    else:
                        start_point, end_point = _dms_pairs(value)
                        start = Geodesic.WGS84.Inverse(center[1], center[0], start_point[1], start_point[0])
                        end = Geodesic.WGS84.Inverse(center[1], center[0], end_point[1], end_point[0])
                        if abs(start["s12"] - end["s12"]) > max(100, start["s12"] * .02):
                            raise ValueError("inconsistent OpenAIR arc radii")
                        sweep = ((end["azi1"] - start["azi1"]) * direction % 360) * direction
                        coordinates.extend(_arc(center, start["s12"] / 1852, start["azi1"], sweep))
                    current["geometry_note"] = "geodesic arcs sampled at at most 5-degree intervals"
                elif code.startswith("D"):
                    raise ValueError(f"unsupported OpenAIR boundary record: {code}")
    completed = finish()
    if completed:
        yield completed


def load_xplane(config: dict) -> AviationDataset:
    if not config.get("xplane_path"):
        raise ValueError("xplane_path is required; no simulator installation was scanned")
    base = _options(config, {"xplane_path"})
    root = _path(base, config["xplane_path"])
    custom = root / "Custom Data"
    for name in ("earth_424.dat", "FAACIFP18", "user_nav.dat", "user_fix.dat"):
        if (custom / name).exists():
            raise ValueError(f"unsupported navigation override: Custom Data/{name}")
    global_airports = root / "Global Scenery/Global Airports/Earth nav data/apt.dat"
    paths = []
    scenery_order = root / "Custom Scenery/scenery_packs.ini"
    warnings = []
    if (custom / "CIFP").exists():
        warnings.append("Custom CIFP procedures are not depicted by this map")
    if scenery_order.exists():
        for line in scenery_order.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("SCENERY_PACK "):
                value = line[len("SCENERY_PACK "):].strip()
                if value == "*GLOBAL_AIRPORTS*":
                    paths.append(global_airports)
                else:
                    candidate = _path(root, value) / "Earth nav data/apt.dat"
                    if candidate.exists():
                        paths.append(candidate)
    else:
        warnings.append("No scenery_packs.ini; only Global Airports is represented")
    if global_airports not in paths:
        paths.append(global_airports)
    airports = {}
    for path in paths:
        for identifier, feature in _airports(path).items():
            airports.setdefault(identifier, feature)
    layers = {"airports": layer_metadata("X-Plane apt.dat", "installed scenery", warnings=warnings,
                                         source_files=[str(path) for path in paths])}
    features = list(airports.values())
    has_custom_navigation = any((custom / name).exists() for name in ("earth_nav.dat", "earth_fix.dat"))
    data = custom if has_custom_navigation else root / "Resources/default data"
    cycles = set()
    for layer, name in (("navaids", "earth_nav.dat"), ("fixes", "earth_fix.dat")):
        path = data / name
        if not path.exists():
            if has_custom_navigation:
                raise ValueError("Custom Data must provide both earth_nav.dat and earth_fix.dat")
            continue
        layers[layer] = _header_metadata(path)
        if layers[layer]["cycle"]:
            cycles.add(layers[layer]["cycle"])
        features.extend(_navigation(path, layer))
    if len(cycles) > 1:
        raise ValueError("incompatible navigation/fix cycles")
    airspace = custom / "airspaces/airspace.txt"
    if not airspace.exists():
        airspace = root / "Resources/default data/airspaces/airspace.txt"
    if airspace.exists():
        layers["airspace"] = layer_metadata(str(airspace), "installed dataset; coverage unverified")
        features.extend(_airspace(airspace))
    return AviationDataset("xplane", features, layers)


def _csv(path: Path, required: set[str]):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"missing CSV columns in {path.name}: {sorted(required)}")
        yield from reader


def _geojson(path: Path, layer: str):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("FAA data must be an exported GeoJSON FeatureCollection")
    for index, feature in enumerate(data["features"]):
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise ValueError("GeoJSON features require a geometry object")
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError("GeoJSON properties must be an object")
        identifier = properties.get("IDENT") or properties.get("FIX_ID") or properties.get("NAME") or str(index)
        yield {"type": "Feature", "id": f"{layer}:{index}:{identifier}", "geometry": feature["geometry"],
               "properties": {"layer": layer, "ident": str(identifier), "name": properties.get("NAME"),
                "class": properties.get("CLASS") or properties.get("CLASS_CODE"),
                "floor": properties.get("LOWER_DESC") or properties.get("LOWER_VAL"),
                "ceiling": properties.get("UPPER_DESC") or properties.get("UPPER_VAL"),
                "floor_unit": properties.get("LOWER_UOM"), "ceiling_unit": properties.get("UPPER_UOM"),
                "floor_reference": properties.get("LOWER_CODE"), "ceiling_reference": properties.get("UPPER_CODE"),
                "source_properties": properties}}


def load_public(config: dict) -> AviationDataset:
    base = _options(config, {"files", "metadata"})
    files = config.get("files", {})
    if not isinstance(files, dict) or files.keys() - {"airports", "runways", "frequencies", "navaids", "fixes", "airspace"}:
        raise ValueError("unknown public dataset file options")
    if not files.get("airports"):
        raise ValueError("public files.airports is required")
    paths = {name: _path(base, value) for name, value in files.items()}
    overrides = config.get("metadata", {})
    if not isinstance(overrides, dict) or overrides.keys() - {"airports", "navaids", "fixes", "airspace"}:
        raise ValueError("unknown layer metadata options")
    layers = {}
    for layer in ("airports", "navaids", "fixes", "airspace"):
        if layer in paths:
            extra = overrides.get(layer, {})
            if not isinstance(extra, dict) or extra.keys() - {"cycle", "effective_date", "valid_until", "coverage", "license", "attribution"}:
                raise ValueError("unsupported layer metadata fields")
            layers[layer] = layer_metadata(
                "OurAirports" if layer in ("airports", "navaids") else "FAA GeoJSON export",
                **{"coverage": "worldwide; completeness unverified" if layer in ("airports", "navaids") else "United States; source coverage applies",
                   "license": "Public domain; no accuracy guarantee" if layer in ("airports", "navaids") else "Review FAA export product terms",
                   **extra}, source_file=str(paths[layer]))
    airports = {}
    for row in _csv(paths["airports"], {"ident", "name", "latitude_deg", "longitude_deg"}):
        identifier = row["ident"]
        if identifier in airports:
            raise ValueError(f"duplicate airport identifier: {identifier}")
        airports[identifier] = _point("airports", identifier, row["latitude_deg"], row["longitude_deg"],
            name=row["name"], elevation_ft=_number(row.get("elevation_ft")),
            airport_type=row.get("type"), runways=[], frequencies=[])
    if "runways" in paths:
        for row in _csv(paths["runways"], {"airport_ident", "le_ident", "he_ident"}):
            airport = airports.get(row["airport_ident"])
            if airport is None:
                continue
            coordinates = None
            if all(row.get(field) for field in ("le_longitude_deg", "le_latitude_deg", "he_longitude_deg", "he_latitude_deg")):
                coordinates = [[float(row["le_longitude_deg"]), float(row["le_latitude_deg"])],
                               [float(row["he_longitude_deg"]), float(row["he_latitude_deg"])]]
            length, width = _number(row.get("length_ft")), _number(row.get("width_ft"))
            airport["properties"]["runways"].append({"identifiers": [row["le_ident"], row["he_ident"]],
                "length_m": length * .3048 if length is not None else None,
                "width_m": width * .3048 if width is not None else None,
                "surface": row.get("surface"), "coordinates": coordinates, "closed": row.get("closed") == "1"})
    if "frequencies" in paths:
        for row in _csv(paths["frequencies"], {"airport_ident", "frequency_mhz"}):
            if row["airport_ident"] in airports:
                airports[row["airport_ident"]]["properties"]["frequencies"].append({
                    "description": row.get("description") or row.get("type"), "frequency_mhz": _number(row["frequency_mhz"])})
    features = list(airports.values())
    if "navaids" in paths:
        for row in _csv(paths["navaids"], {"id", "ident", "latitude_deg", "longitude_deg"}):
            features.append(_point("navaids", row["id"], row["latitude_deg"], row["longitude_deg"],
                ident=row["ident"], name=row.get("name"), kind=row.get("type"),
                frequency=_number(row.get("frequency_khz")), frequency_unit="kHz"))
    for layer in ("fixes", "airspace"):
        if layer in paths:
            features.extend(_geojson(paths[layer], layer))
    return AviationDataset("public", features, layers)


def create_manager(config_path=None) -> AviationManager:
    manager = AviationManager(config_path)
    manager.register("xplane", load_xplane)
    manager.register("public", load_public)
    manager.reload()
    return manager