"use strict";

const LINUX_DESKTOP_PET_UNAVAILABLE_REASON = "linux_desktop_pet_input_passthrough_unreliable";
const LINUX_DESKTOP_PET_UNAVAILABLE_MESSAGE =
  "Desktop Pet is unavailable on Linux because its current full-screen interactive window has no verified safe click-through contract. "
  + "Core V8OS interfaces (Engine, Admin, Web, and Shell) are unaffected.";

function desktopPetAvailability(platform = process.platform) {
  const normalizedPlatform = String(platform || "").toLowerCase();
  if (normalizedPlatform === "linux") {
    return {
      componentId: "desktop-pet",
      platform: normalizedPlatform,
      available: false,
      status: "unavailable",
      reasonCode: LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
      message: LINUX_DESKTOP_PET_UNAVAILABLE_MESSAGE,
    };
  }
  return {
    componentId: "desktop-pet",
    platform: normalizedPlatform,
    available: true,
    status: "available",
    reasonCode: null,
    message: null,
  };
}

module.exports = {
  desktopPetAvailability,
  LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
};
