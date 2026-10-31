// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef DOSBOX_DMCP_WATCH_H
#define DOSBOX_DMCP_WATCH_H

#include "dmcp_internal.h"

namespace dmcp {

// Watches exist: the per-instruction hook must run.
bool WatchesActive();
// Before every instruction while watches exist: remember the instruction, re-install TLB redirections.
void WatchOnInstruction();
// A watch with action "halt" fired since the last call: true exactly once.
bool WatchTakeStop();
// Forgets a pending watch stop (state.load: the access belongs to the replaced state).
void WatchDropPending();
// The access that caused the last watch stop (RPC format).
JsonValue WatchStopJson();

} // namespace dmcp

#endif
