#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
platform=${1:-simulator}
mode=${2:-debug}
case "$platform" in
  simulator) sdk=iphonesimulator; target=arm64-apple-ios17.0-simulator; bundle=org.oneai.personal.ios ;;
  mac) sdk=macosx; target=arm64-apple-macos14.0; bundle=org.oneai.personal.mac ;;
  *) echo 'Usage: build.sh simulator|mac [debug|release]'; exit 2 ;;
esac
out="state/build/$platform/oneAI.app"
mkdir -p "$out"
exe="$out"
plist="$out"
if [ "$platform" = mac ]; then exe="$out/Contents/MacOS"; plist="$out/Contents"; mkdir -p "$exe"; fi
set --
if [ "$mode" = debug ]; then set -- -D DEBUG; fi
xcrun --sdk "$sdk" swiftc -module-cache-path state/build/module-cache -parse-as-library -target "$target" -sdk "$(xcrun --sdk "$sdk" --show-sdk-path)" "$@" apps/apple/OneAI.swift -o "$exe/oneAI"
python3 - "$plist" "$bundle" "$platform" "$mode" <<'PY'
import plistlib,sys
from pathlib import Path
out,bundle,platform,mode=sys.argv[1:]
p={'CFBundleIdentifier':bundle,'CFBundleName':'oneAI','CFBundleDisplayName':'oneAI','CFBundleExecutable':'oneAI','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1'}
if platform=='simulator':
 p.update(MinimumOSVersion='17.0',UIDeviceFamily=[1,2],UILaunchScreen={},UISupportedInterfaceOrientations=['UIInterfaceOrientationPortrait','UIInterfaceOrientationLandscapeLeft','UIInterfaceOrientationLandscapeRight'])
else: p.update(LSMinimumSystemVersion='14.0',NSHighResolutionCapable=True)
if mode=='debug':
 p['NSAppTransportSecurity']={'NSAllowsLocalNetworking':True}
 p['OneAITestURL']='http://127.0.0.1:8765'
Path(out,'Info.plist').write_bytes(plistlib.dumps(p))
PY
codesign --force --sign - "$out"
echo "$out"
