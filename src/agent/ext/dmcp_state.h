#ifndef DOSBOX_DMCP_STATE_H
#define DOSBOX_DMCP_STATE_H

#include "dmcp_internal.h"

#include <cstdint>
#include <string>

namespace dmcp {

// Writes <name>.sav and the sidecar <name>.json (dosbox-mcp-state/1) into the output directory.
// aufruf != nullptr adds the field "aufruf" (D31: expected return of a recorded call).
bool SaveStateFile(const std::string& name, const JsonValue* aufruf, std::string* path, std::string* info_path,
                   uint64_t* size, Error* error);
// All checks of state.load, then loads and resets agent state that belongs to the replaced machine state.
// *sidecar = parsed sidecar object, or null JSON when none exists (only allowed with unchecked).
bool LoadStateFile(const std::string& path, bool unchecked, JsonValue* sidecar, Error* error);
// The parsed sidecar <path without .sav>.json of a savestate; false if missing or not a JSON object.
bool ReadStateSidecar(const std::string& path, JsonValue* sidecar);

} // namespace dmcp

#endif
