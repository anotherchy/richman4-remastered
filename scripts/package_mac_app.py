#!/usr/bin/env python3
"""Build a self-contained macOS app: the game files, a font and the Homebrew libraries go inside it.

The result runs without this repository or Homebrew. It contains your original game files, so keep
it on your own Mac; never upload or share it (see NOTICE.md).

    scripts/package_mac_app.py [--output dist]

Game files come from game-assets/ (override with RICH4_GAME_ASSETS). The font is Noto Serif CJK TC,
reused from ios/third_party/downloads/ or downloaded and checked by SHA-256 (same pin as iOS).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "build/mac-native"
GENERATED = REPO / "build/mac-generated"
GAME_FILES = (".mkf", ".mid", ".wav", ".ogg")
FONT_NAME = "NotoSerifCJKtc-Regular.otf"
FONT_URL = ("https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/OTF/TraditionalChinese/"
            "NotoSerifCJKtc-Regular.otf")
FONT_SHA256 = "234301038e76e7c35c43113785024700c4e4fe7bdce1d1fbbc42fca7e6683798"
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")


def run(*command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def build():
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    # fluidsynth.pc lists readline as a private requirement; Homebrew's readline is keg-only.
    env["PKG_CONFIG_PATH"] = ":".join(filter(None, ["/opt/homebrew/opt/readline/lib/pkgconfig",
                                                    env.get("PKG_CONFIG_PATH")]))
    run("cmake", "-S", REPO, "-B", BUILD, "-DCMAKE_BUILD_TYPE=Release", "-DRICH4_SANITIZERS=OFF",
        "-DRICH4_BUILD_VIEWER=OFF", "-DRICH4_BUILD_GAME=ON", "-DBUILD_TESTING=OFF", env=env)
    run("cmake", "--build", BUILD, "--target", "rich4-native", "--parallel", env=env)
    return BUILD / "rich4-native.app"


def font():
    cached = REPO / "ios/third_party/downloads" / FONT_NAME
    target = cached if cached.is_file() else GENERATED / FONT_NAME
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != FONT_SHA256:
        GENERATED.mkdir(parents=True, exist_ok=True)
        target = GENERATED / FONT_NAME
        print(f"Downloading {FONT_NAME}")
        data = urllib.request.urlopen(FONT_URL).read()
        if hashlib.sha256(data).hexdigest() != FONT_SHA256:
            raise SystemExit(f"Checksum mismatch: {FONT_NAME}")
        target.write_bytes(data)
    return target


def icon(exe, resources):
    """The original rich4.exe icon (same artwork as the iOS/Android icons) as an .icns file."""
    catalog = GENERATED / "Assets.xcassets"
    run("python3", REPO / "scripts/make_ios_icon.py", exe, catalog)
    master = catalog / "AppIcon.appiconset/AppIcon-1024.png"
    iconset = GENERATED / "rich4.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            run("sips", "-z", str(size * scale), str(size * scale), master, "--out", iconset / name,
                stdout=subprocess.DEVNULL)
    run("iconutil", "-c", "icns", iconset, "-o", resources / "rich4.icns")


def dependencies(binary):
    """Non-system libraries `binary` loads, as (install name, resolved path)."""
    output = run("otool", "-L", binary, capture_output=True, text=True).stdout.splitlines()[1:]
    rpaths = [line.split()[1] for line in run("otool", "-l", binary, capture_output=True, text=True)
              .stdout.splitlines() if line.strip().startswith("path ")]
    result = []
    for line in output:
        name = line.split()[0]
        if name.startswith(SYSTEM_PREFIXES) or name.startswith("@executable_path/../Frameworks/"):
            continue
        if name.startswith("@loader_path/"):
            path = binary.parent / name.removeprefix("@loader_path/")
        elif name.startswith("@rpath/"):
            candidates = [Path(r.replace("@loader_path", str(binary.parent))) / name.removeprefix("@rpath/")
                          for r in rpaths]
            path = next((c for c in candidates if c.exists()), None)
            if path is None:
                raise SystemExit(f"cannot resolve {name} from {binary}")
        else:
            path = Path(name)
        if path.resolve() == binary.resolve():
            continue  # a library's own install name
        result.append((name, path.resolve()))
    return result


def bundle_libraries(executable, frameworks):
    """Copies every Homebrew library into Contents/Frameworks and points the load commands at it."""
    frameworks.mkdir(exist_ok=True)
    pending = [(executable, executable)]  # (file to patch, where its dependencies resolve from)
    copied = {}
    while pending:
        target, origin = pending.pop()
        for name, path in dependencies(origin):
            bundled = frameworks / path.name
            if path not in copied:
                shutil.copy2(path, bundled)
                bundled.chmod(0o644)
                run("install_name_tool", "-id", f"@executable_path/../Frameworks/{path.name}", bundled,
                    stderr=subprocess.DEVNULL)
                copied[path] = bundled
                pending.append((bundled, path))
            run("install_name_tool", "-change", name, f"@executable_path/../Frameworks/{path.name}", target,
                stderr=subprocess.DEVNULL)
    return sorted(copied.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=REPO / "dist")
    args = parser.parse_args()
    assets = Path(os.environ.get("RICH4_GAME_ASSETS", REPO / "game-assets"))
    files = {p.name.lower(): p for p in assets.iterdir() if p.is_file()}
    if "data.mkf" not in files:
        raise SystemExit(f"Missing original game files in {assets} (set RICH4_GAME_ASSETS)")

    source = build()
    app = args.output / "大富翁4.app"
    shutil.rmtree(app, ignore_errors=True)
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, app)
    contents = app / "Contents"
    resources = contents / "Resources"
    (resources / "game").mkdir(parents=True)
    for path in files.values():
        if path.suffix.lower() in GAME_FILES:
            shutil.copyfile(path, resources / "game" / path.name)  # follows symlinks
    shutil.copyfile(font(), resources / FONT_NAME)
    plist = contents / "Info.plist"
    if "rich4.exe" in files:
        icon(files["rich4.exe"], resources)
        run("plutil", "-replace", "CFBundleIconFile", "-string", "rich4", plist)
    run("plutil", "-replace", "NSHighResolutionCapable", "-bool", "YES", plist)

    executable = contents / "MacOS/rich4-native"
    libraries = bundle_libraries(executable, contents / "Frameworks")
    # install_name_tool invalidates signatures; arm64 refuses to load unsigned code.
    for path in libraries + [executable]:
        run("codesign", "--force", "--sign", "-", path, stderr=subprocess.DEVNULL)
    run("codesign", "--force", "--sign", "-", app, stderr=subprocess.DEVNULL)
    size = run("du", "-sh", app, capture_output=True, text=True).stdout.split()[0]
    print(f"{app} ({size}, {len(libraries)} bundled libraries)")


if __name__ == "__main__":
    main()
