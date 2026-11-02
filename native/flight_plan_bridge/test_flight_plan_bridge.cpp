#include "flight_plan_bridge.cpp"

#include <cassert>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

struct TestFmsEntry {
  XPLMNavType type;
  std::string identifier;
  int altitude_ft;
  float latitude;
  float longitude;
  XPLMNavRef nav_ref = XPLM_NAV_NOT_FOUND;
};

struct NavAid {
  XPLMNavType type;
  std::string identifier;
  float latitude;
  float longitude;
};

struct DataAccessor {
  XPLMDataRef handle;
  XPLMDataTypeID type;
  XPLMGetDatai_f read_int;
  XPLMGetDatab_f read_data;
  XPLMSetDatab_f write_data;
  void *read_refcon;
};

struct CommandAccessor {
  XPLMCommandRef handle;
  XPLMCommandCallback_f handler;
  bool registered;
};

std::map<std::string, DataAccessor> g_datarefs;
std::map<std::string, CommandAccessor> g_commands;
std::vector<TestFmsEntry> g_fms_entries;
std::vector<NavAid> g_nav_aids;
int g_active_fms_entry = -1;
std::uintptr_t g_next_handle = 1;
XPLMFlightLoop_f g_flight_loop_callback = nullptr;
void *g_flight_loop_refcon = nullptr;

}  // namespace

extern "C" XPLMDataRef XPLMRegisterDataAccessor(
    const char *name, XPLMDataTypeID type, int, XPLMGetDatai_f read_int,
    XPLMSetDatai_f, XPLMGetDataf_f, XPLMSetDataf_f, XPLMGetDatad_f,
    XPLMSetDatad_f, XPLMGetDatavi_f, XPLMSetDatavi_f, XPLMGetDatavf_f,
    XPLMSetDatavf_f, XPLMGetDatab_f read_data,
    XPLMSetDatab_f write_data, void *read_refcon, void *) {
  auto handle = reinterpret_cast<XPLMDataRef>(g_next_handle++);
  g_datarefs[name] = {handle, type, read_int, read_data, write_data,
                      read_refcon};
  return handle;
}

extern "C" void XPLMUnregisterDataAccessor(XPLMDataRef handle) {
  for (auto item = g_datarefs.begin(); item != g_datarefs.end(); ++item) {
    if (item->second.handle == handle) {
      g_datarefs.erase(item);
      return;
    }
  }
}

extern "C" int XPLMCountFMSFlightPlanEntries(XPLMNavFlightPlan plan) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  return static_cast<int>(g_fms_entries.size());
}

extern "C" int
XPLMGetDestinationFMSFlightPlanEntry(XPLMNavFlightPlan plan) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  return g_active_fms_entry;
}

extern "C" void XPLMGetFMSFlightPlanEntryInfo(
    XPLMNavFlightPlan plan, int index, XPLMNavType *out_type, char *out_id,
    XPLMNavRef *out_ref, int *out_altitude, float *out_latitude,
    float *out_longitude) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  assert(index >= 0 && index < static_cast<int>(g_fms_entries.size()));
  const auto &entry = g_fms_entries[static_cast<std::size_t>(index)];
  if (out_type != nullptr) {
    *out_type = entry.type;
  }
  if (out_id != nullptr) {
    std::strncpy(out_id, entry.identifier.c_str(), 255);
    out_id[255] = '\0';
  }
  if (out_ref != nullptr) {
    *out_ref = entry.nav_ref;
  }
  if (out_altitude != nullptr) {
    *out_altitude = entry.altitude_ft;
  }
  if (out_latitude != nullptr) {
    *out_latitude = entry.latitude;
  }
  if (out_longitude != nullptr) {
    *out_longitude = entry.longitude;
  }
}

extern "C" XPLMNavRef XPLMGetFirstNavAid() {
  return g_nav_aids.empty() ? XPLM_NAV_NOT_FOUND : 0;
}

extern "C" XPLMNavRef XPLMGetNextNavAid(XPLMNavRef ref) {
  if (ref < 0 || static_cast<std::size_t>(ref + 1) >= g_nav_aids.size()) {
    return XPLM_NAV_NOT_FOUND;
  }
  return ref + 1;
}

extern "C" void XPLMGetNavAidInfo(
    XPLMNavRef ref, XPLMNavType *out_type, float *out_latitude,
    float *out_longitude, float *, int *, float *, char *out_id, char *,
    char *) {
  assert(ref >= 0 && static_cast<std::size_t>(ref) < g_nav_aids.size());
  const auto &aid = g_nav_aids[static_cast<std::size_t>(ref)];
  if (out_type != nullptr) {
    *out_type = aid.type;
  }
  if (out_latitude != nullptr) {
    *out_latitude = aid.latitude;
  }
  if (out_longitude != nullptr) {
    *out_longitude = aid.longitude;
  }
  if (out_id != nullptr) {
    std::strncpy(out_id, aid.identifier.c_str(), 255);
    out_id[255] = '\0';
  }
}

extern "C" void XPLMSetFMSFlightPlanEntryInfo(
    XPLMNavFlightPlan plan, int index, XPLMNavRef nav_ref, int altitude_ft) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  assert(index >= 0 && index <= static_cast<int>(g_fms_entries.size()));
  assert(nav_ref >= 0 && static_cast<std::size_t>(nav_ref) < g_nav_aids.size());
  const auto &aid = g_nav_aids[static_cast<std::size_t>(nav_ref)];
  TestFmsEntry entry{aid.type, aid.identifier, altitude_ft, aid.latitude,
                     aid.longitude, nav_ref};
  if (index == static_cast<int>(g_fms_entries.size())) {
    g_fms_entries.push_back(entry);
  } else {
    g_fms_entries[static_cast<std::size_t>(index)] = entry;
  }
}

extern "C" void XPLMSetFMSFlightPlanEntryLatLon(
    XPLMNavFlightPlan plan, int index, float latitude, float longitude,
    int altitude_ft) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  assert(index >= 0 && index <= static_cast<int>(g_fms_entries.size()));
  TestFmsEntry entry{xplm_Nav_LatLon, "", altitude_ft, latitude, longitude};
  if (index == static_cast<int>(g_fms_entries.size())) {
    g_fms_entries.push_back(entry);
  } else {
    g_fms_entries[static_cast<std::size_t>(index)] = entry;
  }
}

extern "C" void XPLMSetFMSFlightPlanEntryLatLonWithId(
    XPLMNavFlightPlan plan, int index, float latitude, float longitude,
    int altitude_ft, const char *identifier, unsigned int id_length) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  assert(index >= 0 && index <= static_cast<int>(g_fms_entries.size()));
  TestFmsEntry entry{xplm_Nav_LatLon, std::string(identifier, id_length),
                     altitude_ft, latitude, longitude};
  if (index == static_cast<int>(g_fms_entries.size())) {
    g_fms_entries.push_back(entry);
  } else {
    g_fms_entries[static_cast<std::size_t>(index)] = entry;
  }
}

extern "C" void XPLMSetDirectToFMSFlightPlanEntry(
    XPLMNavFlightPlan plan, int index) {
  assert(plan == xplm_Fpl_Pilot_Primary);
  assert(index >= 0 && index < static_cast<int>(g_fms_entries.size()));
  g_active_fms_entry = index;
}

extern "C" void XPLMRegisterFlightLoopCallback(XPLMFlightLoop_f callback,
                                                float interval, void *refcon) {
  assert(interval == -1.0f);
  g_flight_loop_callback = callback;
  g_flight_loop_refcon = refcon;
}

extern "C" void XPLMUnregisterFlightLoopCallback(XPLMFlightLoop_f callback,
                                                  void *refcon) {
  assert(callback == g_flight_loop_callback);
  assert(refcon == g_flight_loop_refcon);
  g_flight_loop_callback = nullptr;
  g_flight_loop_refcon = nullptr;
}

extern "C" XPLMCommandRef XPLMCreateCommand(const char *name, const char *) {
  auto handle = reinterpret_cast<XPLMCommandRef>(g_next_handle++);
  g_commands[name] = {handle, nullptr, false};
  return handle;
}

extern "C" void XPLMRegisterCommandHandler(
    XPLMCommandRef command, XPLMCommandCallback_f handler, int before, void *) {
  assert(before == 1);
  for (auto &item : g_commands) {
    if (item.second.handle == command) {
      item.second.handler = handler;
      item.second.registered = true;
      return;
    }
  }
  assert(false);
}

extern "C" void XPLMUnregisterCommandHandler(
    XPLMCommandRef command, XPLMCommandCallback_f handler, int before, void *) {
  assert(before == 1);
  for (auto &item : g_commands) {
    if (item.second.handle == command) {
      assert(handler == item.second.handler);
      item.second.registered = false;
      return;
    }
  }
  assert(false);
}

void trigger_command(const std::string &name) {
  auto &command = g_commands.at(name);
  assert(command.registered);
  assert(command.handler != nullptr);
  command.handler(command.handle, xplm_CommandBegin, nullptr);
  command.handler(command.handle, xplm_CommandEnd, nullptr);
}

std::string read_snapshot_payload(const DataAccessor &snapshot) {
  const int length = snapshot.read_data(snapshot.read_refcon, nullptr, 0, 0);
  assert(length > 0);
  std::vector<char> bytes(static_cast<std::size_t>(length));
  assert(snapshot.read_data(snapshot.read_refcon, bytes.data(), 0, length) ==
         length);
  return {bytes.begin(), bytes.end()};
}

int main() {
  char name[256] = {};
  char signature[256] = {};
  char description[256] = {};
  assert(XPluginStart(name, signature, description) == 1);
  assert(g_datarefs.size() == 4);
  assert(g_commands.size() == 2);
  assert(g_commands.at("xplane_api/flight_plan/probe").registered);
  assert(g_commands.at("xplane_api/flight_plan/direct_to").registered);
  assert(g_flight_loop_callback != nullptr);

  const auto &version = g_datarefs.at("xplane_api/flight_plan/bridge_version");
  assert(version.type == xplmType_Int);
  assert(version.read_int(version.read_refcon) == 3);

  const auto &snapshot = g_datarefs.at("xplane_api/flight_plan/bridge_snapshot");
  assert(snapshot.type == xplmType_Data);
  char empty_query = 0;
  assert(snapshot.read_data(snapshot.read_refcon, &empty_query, 0, 0) == 0);

  assert(g_flight_loop_callback(0.0f, 0.0f, 0, g_flight_loop_refcon) == 1.0f);
  std::string payload = read_snapshot_payload(snapshot);
  assert(payload.find("\"available\":false") != std::string::npos);
  assert(payload.find("\"status\":\"no_active_plan\"") != std::string::npos);
  assert(payload.find("\"active_index\":null") != std::string::npos);

  g_fms_entries = {
      {xplm_Nav_Airport, "KSEA", 0, 47.4489f, -122.3094f},
      {xplm_Nav_Fix, "SEA", 5000, 47.4500f, -122.3000f},
      {xplm_Nav_LatLon, "", 3000, 45.0f, -120.0f},
  };
  g_active_fms_entry = 1;
  assert(g_flight_loop_callback(1.0f, 1.0f, 1, g_flight_loop_refcon) ==
         1.0f);
  payload = read_snapshot_payload(snapshot);
  assert(payload.find("\"available\":true") != std::string::npos);
  assert(payload.find("\"status\":\"available\"") != std::string::npos);
  assert(payload.find("\"active_index\":1") != std::string::npos);
  assert(payload.find("\"identifier\":\"KSEA\"") <
         payload.find("\"identifier\":\"SEA\""));
  assert(payload.find("\"type\":\"airport\"") != std::string::npos);
  assert(payload.find("\"altitude_ft\":5000") != std::string::npos);
  assert(payload.find("\"altitude\":\"reported\"") != std::string::npos);
  assert(payload.find("\"identifier\":null") != std::string::npos);
  assert(payload.find("\"type\":\"lat_lon\"") != std::string::npos);
  assert(payload.find("\"identifier\":\"unavailable\"") !=
         std::string::npos);
  assert(payload.find("\"bridge_version\":3") != std::string::npos);
  assert(payload.find("\"position\":\"available\"") != std::string::npos);

  std::vector<char> partial(8);
  const int length = snapshot.read_data(snapshot.read_refcon, nullptr, 0, 0);
  assert(length > 0);
  assert(snapshot.read_data(snapshot.read_refcon, partial.data(), 5,
                            static_cast<int>(partial.size())) ==
         static_cast<int>(partial.size()));
  assert(std::string(partial.begin(), partial.end()) == payload.substr(5, 8));

  g_active_fms_entry = 8;
  g_fms_entries[1].latitude = 120.0f;
  assert(g_flight_loop_callback(1.0f, 1.0f, 2, g_flight_loop_refcon) ==
         1.0f);
  payload = read_snapshot_payload(snapshot);
  assert(payload.find("\"active_index\":null") != std::string::npos);
  assert(payload.find("\"active_index_status\":\"unavailable\"") !=
         std::string::npos);
  assert(payload.find("\"latitude_deg\":null") != std::string::npos);
  assert(payload.find("\"position\":\"unavailable\"") != std::string::npos);

  g_nav_aids = {
      {xplm_Nav_Airport, "KSEA", 47.4489f, -122.3094f},
      {xplm_Nav_Fix, "SEA", 47.4500f, -122.3000f},
      {xplm_Nav_Fix, "FIX2", 45.0f, -120.0f},
      {xplm_Nav_Fix, "NEW", 44.0f, -119.0f},
      {xplm_Nav_Fix, "DUP", 43.0f, -118.0f},
      {xplm_Nav_Fix, "DUP", 42.0f, -117.0f},
      {xplm_Nav_Airport, "KBWI", 39.1754f, -76.6684f},
  };
  g_fms_entries = {
      {xplm_Nav_Airport, "KSEA", 0, 47.4489f, -122.3094f, 0},
      {xplm_Nav_Fix, "SEA", 5000, 47.4500f, -122.3000f, 1},
      {xplm_Nav_LatLon, "USER", 3000, 45.0f, -120.0f},
  };
  g_active_fms_entry = 1;

  const auto &request = g_datarefs.at("xplane_api/flight_plan/direct_to_request");
  assert(request.type == xplmType_Data);
  assert(request.write_data != nullptr);
  const auto &result = g_datarefs.at("xplane_api/flight_plan/direct_to_result");
  assert(result.type == xplmType_Data);

  const std::string request_id = "0123456789abcdef0123456789abcdef";
  const auto send_direct_to = [&](const std::string &target) {
    const std::string request_value = request_id + "|" + target;
    request.write_data(nullptr, const_cast<char *>(request_value.data()), 0,
                       static_cast<int>(request_value.size()));
    trigger_command("xplane_api/flight_plan/direct_to");
    assert(g_flight_loop_callback(2.0f, 2.0f, 3, g_flight_loop_refcon) == 1.0f);
    return read_snapshot_payload(result);
  };

  payload = send_direct_to("NEW");
  assert(payload.find("\"status\":\"applied\"") != std::string::npos);
  assert(payload.find("\"target_identifier\":\"NEW\"") != std::string::npos);
  assert(g_fms_entries.size() == 4);
  assert(g_fms_entries[0].identifier == "KSEA");
  assert(g_fms_entries[1].identifier == "NEW");
  assert(g_fms_entries[2].identifier == "SEA");
  assert(g_fms_entries[3].identifier == "USER");
  assert(g_active_fms_entry == 1);

  payload = send_direct_to("SEA");
  assert(payload.find("\"status\":\"applied\"") != std::string::npos);
  assert(g_fms_entries.size() == 4);
  assert(g_active_fms_entry == 2);

  const auto entry_count_before_error = g_fms_entries.size();
  const auto active_index_before_error = g_active_fms_entry;
  payload = send_direct_to("MISSING");
  assert(payload.find("\"status\":\"unknown_target\"") !=
         std::string::npos);
  assert(g_fms_entries.size() == entry_count_before_error);
  assert(g_active_fms_entry == active_index_before_error);

  payload = send_direct_to("DUP");
  assert(payload.find("\"status\":\"ambiguous_target\"") !=
         std::string::npos);
  assert(g_fms_entries.size() == entry_count_before_error);
  assert(g_active_fms_entry == active_index_before_error);

  payload = send_direct_to("bad target");
  assert(payload.find("\"status\":\"invalid_request\"") !=
         std::string::npos);
  assert(g_fms_entries.size() == entry_count_before_error);
  assert(g_active_fms_entry == active_index_before_error);

  g_fms_entries.clear();
  g_active_fms_entry = -1;
  payload = send_direct_to("KBWI");
  assert(payload.find("\"status\":\"applied\"") != std::string::npos);
  assert(payload.find("\"target_identifier\":\"KBWI\"") !=
         std::string::npos);
  assert(g_fms_entries.size() == 1);
  assert(g_fms_entries[0].identifier == "KBWI");
  assert(g_active_fms_entry == 0);

  XPluginStop();
  assert(g_datarefs.empty());
  assert(!g_commands.at("xplane_api/flight_plan/probe").registered);
  assert(!g_commands.at("xplane_api/flight_plan/direct_to").registered);
  assert(g_flight_loop_callback == nullptr);
}
