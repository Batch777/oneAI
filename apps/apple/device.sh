#!/bin/bash
# Build/sign with the Apple account already configured in Xcode on this Mac.
set -euo pipefail
if [[ $# != 2 || ! "$1" =~ ^[A-Z0-9]{10}$ || ! "$2" =~ ^[A-Za-z0-9-]+$ ]]; then
  echo "Usage: bash apps/apple/device.sh APPLE_TEAM_ID IPHONE_UDID" >&2
  echo "Find the team in Xcode and the UDID with: xcrun devicectl list devices" >&2
  exit 2
fi
repo="$(cd "$(dirname "$0")/../.." && pwd)"
app="$repo/state/build/device/Build/Products/Release-iphoneos/oneAI-iOS.app"
xcodebuild -project "$repo/apps/apple/oneAI.xcodeproj" -scheme oneAI-iOS \
  -configuration Release -destination "id=$2" \
  -derivedDataPath "$repo/state/build/device" DEVELOPMENT_TEAM="$1" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration build
codesign --verify --deep --strict "$app"
xcrun devicectl device install app --device "$2" "$app"
if ! xcrun devicectl device process launch --device "$2" org.oneai.personal.ios; then
  echo "Installed, but launch failed. Unlock iPhone and check Developer Mode and Settings > General > VPN & Device Management. Read the error above for other causes." >&2
  exit 1
fi
