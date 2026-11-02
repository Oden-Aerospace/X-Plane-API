#include <algorithm>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iomanip>
#include <locale>
#include <mutex>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "XPLMDataAccess.h"
#include "XPLMDefs.h"
#include "XPLMNavigation.h"
#include "XPLMProcessing.h"
#include "XPLMUtilities.h"

namespace {

constexpr int kBridgeVersion = 3;
constexpr XPLMNavFlightPlan kFlightPlan = xplm_Fpl_Pilot_Primary;
constexpr int kMaximumFlightPlanEntries = 100;
constexpr std::size_t kMaximumRequestLength = 64;
constexpr char kInitialSnapshot[] =
    R"json({"schema_version":1,"bridge_version":3,"source":"pilot_primary","generated_at_unix_ms":0,"available":false,"status":"initializing","active_index":null,"active_index_status":"unavailable","entries":[]})json";
constexpr char kInitialDirectToResult[] =
    R"json({"request_id":"","status":"idle","target_identifier":null,"error":null})json";

XPLMDataRef g_bridge_version_ref = nullptr;
XPLMDataRef g_snapshot_ref = nullptr;
XPLMDataRef g_direct_to_request_ref = nullptr;
XPLMDataRef g_direct_to_result_ref = nullptr;
XPLMCommandRef g_probe_command = nullptr;
XPLMCommandRef g_direct_to_command = nullptr;
bool g_snapshot_callback_registered = false;
std::mutex g_snapshot_mutex;
std::string g_snapshot = kInitialSnapshot;
std::mutex g_direct_to_mutex;
std::string g_direct_to_request;
std::string g_direct_to_result = kInitialDirectToResult;
bool g_direct_to_pending = false;

int read_bridge_version(void *) { return kBridgeVersion; }

void append_json_string(std::ostringstream &output, const std::string &value) {
  output << '"';
  for (const unsigned char character : value) {
    switch (character) {
    case '"':
      output << "\\\"";
      break;
    case '\\':
      output << "\\\\";
      break;
    case '\b':
      output << "\\b";
      break;
    case '\f':
      output << "\\f";
      break;
    case '\n':
      output << "\\n";
      break;
    case '\r':
      output << "\\r";
      break;
    case '\t':
      output << "\\t";
      break;
    default:
      if (character < 0x20) {
        output << "\\u00" << std::hex << std::setw(2) << std::setfill('0')
               << static_cast<int>(character) << std::dec;
      } else {
        output << static_cast<char>(character);
      }
      break;
    }
  }
  output << '"';
}

const char *nav_type_name(XPLMNavType type) {
  switch (type) {
  case xplm_Nav_Airport:
    return "airport";
  case xplm_Nav_NDB:
    return "ndb";
  case xplm_Nav_VOR:
    return "vor";
  case xplm_Nav_ILS:
    return "ils";
  case xplm_Nav_Localizer:
    return "localizer";
  case xplm_Nav_GlideSlope:
    return "glideslope";
  case xplm_Nav_OuterMarker:
    return "outer_marker";
  case xplm_Nav_MiddleMarker:
    return "middle_marker";
  case xplm_Nav_InnerMarker:
    return "inner_marker";
  case xplm_Nav_Fix:
    return "fix";
  case xplm_Nav_DME:
    return "dme";
  case xplm_Nav_LatLon:
    return "lat_lon";
  case xplm_Nav_TACAN:
    return "tacan";
  default:
    return "unknown";
  }
}

std::int64_t current_unix_time_ms() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

std::string make_unavailable_snapshot(const char *status) {
  std::ostringstream output;
  output.imbue(std::locale::classic());
  output << "{\"schema_version\":1,\"bridge_version\":" << kBridgeVersion
         << ",\"source\":\"pilot_primary\",\"generated_at_unix_ms\":"
         << current_unix_time_ms()
         << ",\"available\":false,\"status\":";
  append_json_string(output, status);
  output << ",\"active_index\":null,\"active_index_status\":\"unavailable\","
            "\"entries\":[]}";
  return output.str();
}

bool supported_fms_type(XPLMNavType type) {
  return type == xplm_Nav_Airport || type == xplm_Nav_Fix ||
         type == xplm_Nav_NDB || type == xplm_Nav_VOR ||
         type == xplm_Nav_TACAN;
}

std::string normalize_identifier(const std::string &identifier) {
  if (identifier.empty() || identifier.size() > 16) {
    return {};
  }
  std::string normalized;
  normalized.reserve(identifier.size());
  for (const unsigned char character : identifier) {
    if (!std::isalnum(character) && character != '-' && character != '_') {
      return {};
    }
    normalized.push_back(
        static_cast<char>(std::toupper(static_cast<unsigned char>(character))));
  }
  return normalized;
}

bool valid_request_id(const std::string &request_id) {
  return request_id.size() == 32 &&
         std::all_of(request_id.begin(), request_id.end(),
                     [](unsigned char character) {
                       return std::isxdigit(character) != 0;
                     });
}

std::string make_direct_to_result(const std::string &request_id,
                                  const char *status,
                                  const std::string &target_identifier,
                                  const char *error) {
  std::ostringstream output;
  output.imbue(std::locale::classic());
  output << "{\"request_id\":";
  append_json_string(output, request_id);
  output << ",\"status\":";
  append_json_string(output, status);
  output << ",\"target_identifier\":";
  if (target_identifier.empty()) {
    output << "null";
  } else {
    append_json_string(output, target_identifier);
  }
  output << ",\"error\":";
  if (error == nullptr) {
    output << "null";
  } else {
    append_json_string(output, error);
  }
  output << '}';
  return output.str();
}

void set_direct_to_result(const std::string &request_id, const char *status,
                          const std::string &target_identifier,
                          const char *error = nullptr) {
  std::lock_guard<std::mutex> lock(g_direct_to_mutex);
  g_direct_to_result =
      make_direct_to_result(request_id, status, target_identifier, error);
}

int read_data_buffer(std::string &value, std::mutex &mutex, void *out_value,
                     int offset, int max_length) {
  std::lock_guard<std::mutex> lock(mutex);
  const int value_length = static_cast<int>(value.size());
  if (out_value == nullptr) {
    return value_length;
  }
  if (offset < 0 || max_length <= 0 || offset >= value_length) {
    return 0;
  }
  const int bytes_to_copy = std::min(max_length, value_length - offset);
  std::memcpy(out_value, value.data() + offset,
              static_cast<std::size_t>(bytes_to_copy));
  return bytes_to_copy;
}

int read_direct_to_result(void *, void *out_value, int offset, int max_length) {
  return read_data_buffer(g_direct_to_result, g_direct_to_mutex, out_value,
                          offset, max_length);
}

void write_direct_to_request(void *, void *in_value, int offset,
                             int length) {
  if (in_value == nullptr || offset != 0 || length <= 0 ||
      static_cast<std::size_t>(length) > kMaximumRequestLength) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_direct_to_mutex);
  g_direct_to_request.assign(static_cast<const char *>(in_value),
                             static_cast<std::size_t>(length));
}

struct DirectToRequest {
  std::string request_id;
  std::string target_identifier;
};

bool parse_direct_to_request(const std::string &raw,
                             DirectToRequest &request) {
  const std::size_t separator = raw.find('|');
  if (separator == std::string::npos) {
    return false;
  }
  request.request_id = raw.substr(0, separator);
  if (!valid_request_id(request.request_id)) {
    return false;
  }
  request.target_identifier = normalize_identifier(raw.substr(separator + 1));
  return !request.target_identifier.empty();
}

bool find_unique_target(const std::string &target_identifier,
                        XPLMNavRef &target_ref, const char *&error_status,
                        const char *&error_message) {
  int exact_match_count = 0;
  XPLMNavRef matching_ref = XPLM_NAV_NOT_FOUND;
  XPLMNavType matching_type = xplm_Nav_Unknown;
  for (XPLMNavRef ref = XPLMGetFirstNavAid(); ref != XPLM_NAV_NOT_FOUND;
       ref = XPLMGetNextNavAid(ref)) {
    XPLMNavType type = xplm_Nav_Unknown;
    char identifier[256] = {};
    XPLMGetNavAidInfo(ref, &type, nullptr, nullptr, nullptr, nullptr, nullptr,
                      identifier, nullptr, nullptr);
    identifier[sizeof(identifier) - 1] = '\0';
    if (normalize_identifier(identifier) != target_identifier) {
      continue;
    }
    ++exact_match_count;
    matching_ref = ref;
    matching_type = type;
  }
  if (exact_match_count == 0) {
    error_status = "unknown_target";
    error_message = "target was not found in the navigation database";
    return false;
  }
  if (exact_match_count > 1) {
    error_status = "ambiguous_target";
    error_message = "target identifier matches multiple navigation entries";
    return false;
  }
  if (!supported_fms_type(matching_type)) {
    error_status = "unsupported_target";
    error_message = "target type is not supported by the FMS";
    return false;
  }
  target_ref = matching_ref;
  return true;
}

struct FmsEntry {
  XPLMNavType type = xplm_Nav_Unknown;
  XPLMNavRef nav_ref = XPLM_NAV_NOT_FOUND;
  std::string identifier;
  int altitude_ft = 0;
  float latitude = 0.0f;
  float longitude = 0.0f;
};

bool read_fms_entry(int index, FmsEntry &entry) {
  char identifier[256] = {};
  XPLMGetFMSFlightPlanEntryInfo(kFlightPlan, index, &entry.type, identifier,
                                &entry.nav_ref, &entry.altitude_ft,
                                &entry.latitude, &entry.longitude);
  identifier[sizeof(identifier) - 1] = '\0';
  entry.identifier = identifier;
  if (entry.type == xplm_Nav_LatLon) {
    return std::isfinite(entry.latitude) && std::isfinite(entry.longitude) &&
           entry.latitude >= -90.0f && entry.latitude <= 90.0f &&
           entry.longitude >= -180.0f && entry.longitude <= 180.0f;
  }
  return supported_fms_type(entry.type) &&
         entry.nav_ref != XPLM_NAV_NOT_FOUND;
}

void write_fms_entry(int index, const FmsEntry &entry) {
  if (entry.type == xplm_Nav_LatLon) {
    if (entry.identifier.empty()) {
      XPLMSetFMSFlightPlanEntryLatLon(kFlightPlan, index, entry.latitude,
                                      entry.longitude, entry.altitude_ft);
    } else {
      XPLMSetFMSFlightPlanEntryLatLonWithId(
          kFlightPlan, index, entry.latitude, entry.longitude,
          entry.altitude_ft, entry.identifier.c_str(),
          static_cast<unsigned int>(entry.identifier.size()));
    }
    return;
  }
  XPLMSetFMSFlightPlanEntryInfo(kFlightPlan, index, entry.nav_ref,
                                entry.altitude_ft);
}

void process_direct_to_request() {
  std::string raw_request;
  {
    std::lock_guard<std::mutex> lock(g_direct_to_mutex);
    if (!g_direct_to_pending) {
      return;
    }
    g_direct_to_pending = false;
    raw_request = std::move(g_direct_to_request);
    g_direct_to_request.clear();
  }

  DirectToRequest request;
  if (!parse_direct_to_request(raw_request, request)) {
    set_direct_to_result(valid_request_id(request.request_id)
                             ? request.request_id
                             : std::string{},
                         "invalid_request", request.target_identifier,
                         "request must contain a UUID and a target identifier");
    return;
  }
  set_direct_to_result(request.request_id, "processing",
                       request.target_identifier);

  XPLMNavRef target_ref = XPLM_NAV_NOT_FOUND;
  const char *error_status = nullptr;
  const char *error_message = nullptr;
  if (!find_unique_target(request.target_identifier, target_ref, error_status,
                          error_message)) {
    set_direct_to_result(request.request_id, error_status,
                         request.target_identifier, error_message);
    return;
  }

  const int entry_count = XPLMCountFMSFlightPlanEntries(kFlightPlan);
  if (entry_count < 0 || entry_count > kMaximumFlightPlanEntries) {
    set_direct_to_result(request.request_id, "invalid_entry_count",
                         request.target_identifier,
                         "the active flight plan has an invalid entry count");
    return;
  }

  int matching_index = -1;
  int matching_entry_count = 0;
  for (int index = 0; index < entry_count; ++index) {
    FmsEntry entry;
    XPLMGetFMSFlightPlanEntryInfo(
        kFlightPlan, index, &entry.type, nullptr, &entry.nav_ref,
        &entry.altitude_ft, nullptr, nullptr);
    char identifier[256] = {};
    XPLMGetFMSFlightPlanEntryInfo(kFlightPlan, index, nullptr, identifier,
                                  nullptr, nullptr, nullptr, nullptr);
    identifier[sizeof(identifier) - 1] = '\0';
    if (normalize_identifier(identifier) == request.target_identifier) {
      matching_index = index;
      ++matching_entry_count;
    }
  }
  if (matching_entry_count > 1) {
    set_direct_to_result(request.request_id, "ambiguous_route_entry",
                         request.target_identifier,
                         "target occurs multiple times in the active route");
    return;
  }

  int direct_to_index = matching_index;
  if (matching_index < 0) {
    if (entry_count == 0) {
      XPLMSetFMSFlightPlanEntryInfo(kFlightPlan, 0, target_ref, 0);
      if (XPLMCountFMSFlightPlanEntries(kFlightPlan) != 1) {
        set_direct_to_result(request.request_id, "route_insert_failed",
                             request.target_identifier,
                             "the simulator did not insert the target into the empty plan");
        return;
      }
      direct_to_index = 0;
    } else {
      const int active_index =
          XPLMGetDestinationFMSFlightPlanEntry(kFlightPlan);
      if (active_index < 0 || active_index >= entry_count) {
        set_direct_to_result(request.request_id, "no_active_plan",
                             request.target_identifier,
                             "a current active waypoint is required for continuation");
        return;
      }
      if (entry_count >= kMaximumFlightPlanEntries) {
        set_direct_to_result(
            request.request_id, "flight_plan_full", request.target_identifier,
            "the active flight plan has no room for another entry");
        return;
      }

      std::vector<FmsEntry> continuation;
      continuation.reserve(
          static_cast<std::size_t>(entry_count - active_index));
      for (int index = active_index; index < entry_count; ++index) {
        FmsEntry entry;
        if (!read_fms_entry(index, entry)) {
          set_direct_to_result(
              request.request_id, "unsupported_route_entry",
              request.target_identifier,
              "the active route contains an entry that cannot be preserved");
          return;
        }
        continuation.push_back(std::move(entry));
      }

      XPLMSetFMSFlightPlanEntryInfo(kFlightPlan, entry_count, target_ref, 0);
      if (XPLMCountFMSFlightPlanEntries(kFlightPlan) != entry_count + 1) {
        set_direct_to_result(
            request.request_id, "route_insert_failed", request.target_identifier,
            "the simulator did not append the direct-to target");
        return;
      }
      for (int offset = static_cast<int>(continuation.size()) - 1; offset >= 0;
           --offset) {
        write_fms_entry(active_index + offset + 1,
                        continuation[static_cast<std::size_t>(offset)]);
      }
      XPLMSetFMSFlightPlanEntryInfo(kFlightPlan, active_index, target_ref, 0);
      direct_to_index = active_index;
    }
  }

  XPLMSetDirectToFMSFlightPlanEntry(kFlightPlan, direct_to_index);
  set_direct_to_result(request.request_id, "applied",
                       request.target_identifier);
}

std::string build_snapshot() {
  const int entry_count = XPLMCountFMSFlightPlanEntries(kFlightPlan);
  if (entry_count < 0 || entry_count > kMaximumFlightPlanEntries) {
    return make_unavailable_snapshot("invalid_entry_count");
  }
  if (entry_count == 0) {
    return make_unavailable_snapshot("no_active_plan");
  }

  const int active_index = XPLMGetDestinationFMSFlightPlanEntry(kFlightPlan);
  const bool active_index_available =
      active_index >= 0 && active_index < entry_count;

  std::ostringstream output;
  output.imbue(std::locale::classic());
  output << "{\"schema_version\":1,\"bridge_version\":" << kBridgeVersion
         << ",\"source\":\"pilot_primary\",\"generated_at_unix_ms\":"
         << current_unix_time_ms()
         << ",\"available\":true,\"status\":\"available\",\"active_index\":";
  if (active_index_available) {
    output << active_index;
  } else {
    output << "null";
  }
  output << ",\"active_index_status\":";
  append_json_string(output,
                     active_index_available ? "available" : "unavailable");
  output << ",\"entries\":[";

  for (int index = 0; index < entry_count; ++index) {
    if (index != 0) {
      output << ',';
    }

    XPLMNavType type = 0;
    XPLMNavRef nav_ref = XPLM_NAV_NOT_FOUND;
    int altitude_ft = 0;
    float latitude = 0.0f;
    float longitude = 0.0f;
    char identifier[256] = {};
    XPLMGetFMSFlightPlanEntryInfo(kFlightPlan, index, &type, identifier,
                                  &nav_ref, &altitude_ft, &latitude,
                                  &longitude);
    identifier[sizeof(identifier) - 1] = '\0';

    const bool identifier_available = identifier[0] != '\0';
    const char *type_name = nav_type_name(type);
    const bool type_available = std::strcmp(type_name, "unknown") != 0;
    const bool position_available =
        std::isfinite(latitude) && std::isfinite(longitude) &&
        latitude >= -90.0f && latitude <= 90.0f && longitude >= -180.0f &&
        longitude <= 180.0f;

    output << "{\"index\":" << index << ",\"identifier\":";
    if (identifier_available) {
      append_json_string(output, identifier);
    } else {
      output << "null";
    }
    output << ",\"type\":";
    append_json_string(output, type_name);
    output << ",\"latitude_deg\":";
    if (position_available) {
      output << std::setprecision(9) << latitude;
    } else {
      output << "null";
    }
    output << ",\"longitude_deg\":";
    if (position_available) {
      output << std::setprecision(9) << longitude;
    } else {
      output << "null";
    }
    output << ",\"altitude_ft\":" << altitude_ft
           << ",\"field_status\":{\"identifier\":";
    append_json_string(output,
                       identifier_available ? "available" : "unavailable");
    output << ",\"type\":";
    append_json_string(output, type_available ? "available" : "unavailable");
    output << ",\"position\":";
    append_json_string(output,
                       position_available ? "available" : "unavailable");
    output << ",\"altitude\":\"reported\"}}";
  }
  output << "]}";
  return output.str();
}

float refresh_snapshot(float, float, int, void *) {
  process_direct_to_request();
  std::string next_snapshot = build_snapshot();
  {
    std::lock_guard<std::mutex> lock(g_snapshot_mutex);
    g_snapshot = std::move(next_snapshot);
  }
  return 1.0f;
}

int read_snapshot(void *, void *out_value, int offset, int max_length) {
  return read_data_buffer(g_snapshot, g_snapshot_mutex, out_value, offset,
                          max_length);
}

int handle_probe_command(XPLMCommandRef, XPLMCommandPhase, void *) { return 0; }

int handle_direct_to_command(XPLMCommandRef, XPLMCommandPhase phase, void *) {
  if (phase != xplm_CommandBegin) {
    return 0;
  }
  std::lock_guard<std::mutex> lock(g_direct_to_mutex);
  if (!g_direct_to_request.empty()) {
    g_direct_to_pending = true;
  }
  return 0;
}

void unregister_bridge() {
  if (g_snapshot_callback_registered) {
    XPLMUnregisterFlightLoopCallback(refresh_snapshot, nullptr);
    g_snapshot_callback_registered = false;
  }
  if (g_probe_command != nullptr) {
    XPLMUnregisterCommandHandler(g_probe_command, handle_probe_command, 1,
                                 nullptr);
    g_probe_command = nullptr;
  }
  if (g_direct_to_command != nullptr) {
    XPLMUnregisterCommandHandler(g_direct_to_command, handle_direct_to_command,
                                 1, nullptr);
    g_direct_to_command = nullptr;
  }
  if (g_direct_to_result_ref != nullptr) {
    XPLMUnregisterDataAccessor(g_direct_to_result_ref);
    g_direct_to_result_ref = nullptr;
  }
  if (g_direct_to_request_ref != nullptr) {
    XPLMUnregisterDataAccessor(g_direct_to_request_ref);
    g_direct_to_request_ref = nullptr;
  }
  if (g_snapshot_ref != nullptr) {
    XPLMUnregisterDataAccessor(g_snapshot_ref);
    g_snapshot_ref = nullptr;
  }
  if (g_bridge_version_ref != nullptr) {
    XPLMUnregisterDataAccessor(g_bridge_version_ref);
    g_bridge_version_ref = nullptr;
  }
}

}  // namespace

PLUGIN_API int XPluginStart(char *out_name, char *out_signature,
                            char *out_description) {
  std::snprintf(out_name, 256, "%s", "X-Plane API Flight Plan Bridge");
  std::snprintf(out_signature, 256, "%s",
                "com.oden-aerospace.xplane-api.flight-plan-bridge");
  std::snprintf(out_description, 256, "%s",
                "Bridge for the pilot's primary flight plan.");

  g_bridge_version_ref = XPLMRegisterDataAccessor(
      "xplane_api/flight_plan/bridge_version", xplmType_Int, 0,
      read_bridge_version, nullptr,  // Integer accessors.
      nullptr, nullptr,              // Float accessors.
      nullptr, nullptr,              // Double accessors.
      nullptr, nullptr,              // Integer-array accessors.
      nullptr, nullptr,              // Float-array accessors.
      nullptr, nullptr,              // Data accessors.
      nullptr, nullptr);             // Refcons.
  g_snapshot_ref = XPLMRegisterDataAccessor(
      "xplane_api/flight_plan/bridge_snapshot", xplmType_Data, 0, nullptr,
      nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
      nullptr, read_snapshot, nullptr, nullptr, nullptr);
  g_direct_to_request_ref = XPLMRegisterDataAccessor(
      "xplane_api/flight_plan/direct_to_request", xplmType_Data, 1, nullptr,
      nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
      nullptr, nullptr, write_direct_to_request, nullptr, nullptr);
  g_direct_to_result_ref = XPLMRegisterDataAccessor(
      "xplane_api/flight_plan/direct_to_result", xplmType_Data, 0, nullptr,
      nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
      nullptr, read_direct_to_result, nullptr, nullptr, nullptr);
  g_probe_command = XPLMCreateCommand(
      "xplane_api/flight_plan/probe", "Read-only flight-plan bridge probe");
  g_direct_to_command = XPLMCreateCommand(
      "xplane_api/flight_plan/direct_to",
      "Set direct-to target on the pilot's primary flight plan");

  if (g_bridge_version_ref == nullptr || g_snapshot_ref == nullptr ||
      g_direct_to_request_ref == nullptr || g_direct_to_result_ref == nullptr ||
      g_probe_command == nullptr || g_direct_to_command == nullptr) {
    unregister_bridge();
    return 0;
  }
  XPLMRegisterCommandHandler(g_probe_command, handle_probe_command, 1, nullptr);
  XPLMRegisterCommandHandler(g_direct_to_command, handle_direct_to_command, 1,
                             nullptr);
  XPLMRegisterFlightLoopCallback(refresh_snapshot, -1.0f, nullptr);
  g_snapshot_callback_registered = true;
  return 1;
}

PLUGIN_API void XPluginStop() { unregister_bridge(); }

PLUGIN_API int XPluginEnable() { return 1; }

PLUGIN_API void XPluginDisable() {}

PLUGIN_API void XPluginReceiveMessage(XPLMPluginID, int, void *) {}
