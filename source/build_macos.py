from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "release-macos"
APP_NAME = "夏令营日程助手"
DMG_NAME = "SummerCampPlanner-macOS.dmg"
ICON = ROOT / "assets" / "app.icns"
PLAYWRIGHT_BROWSER_DIR_NAMES = (
    "chromium-*",
    "chromium_headless_shell-*",
)


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def clean() -> None:
    for path in [ROOT / "build", ROOT / "dist", DIST]:
        if path.exists():
            shutil.rmtree(path)


def playwright_package_dir() -> Path:
    try:
        import playwright
    except Exception as exc:
        raise RuntimeError("当前 Python 环境缺少 playwright，无法打包网页抓取功能。") from exc
    return Path(playwright.__file__).resolve().parent / "driver" / "package"


def playwright_browser_cache_dir(env: dict[str, str]) -> Path:
    configured = env.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured and configured != "0":
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Caches" / "ms-playwright"


def has_playwright_chromium(local_browsers: Path) -> bool:
    if not local_browsers.exists():
        return False
    for pattern in PLAYWRIGHT_BROWSER_DIR_NAMES:
        if any(local_browsers.glob(pattern)):
            continue
        return False
    return True


def ensure_playwright_chromium(env: dict[str, str]) -> Path:
    browser_cache = playwright_browser_cache_dir(env)
    if not has_playwright_chromium(browser_cache):
        run([sys.executable, "-m", "playwright", "install", "chromium"], env=env)
    if not has_playwright_chromium(browser_cache):
        raise RuntimeError(
            "Playwright Chromium 下载或安装失败，macOS 发布包不能缺少内置浏览器。\n"
            f"期望目录：{browser_cache}"
        )
    return browser_cache


def add_data_args(paths: list[tuple[Path, str]]) -> list[str]:
    args: list[str] = []
    for source, target in paths:
        args.extend(["--add-data", f"{source}:{target}"])
    return args


def copy_playwright_browsers_to_app(app_path: Path, local_browsers: Path) -> None:
    target = app_path / "Contents" / "Resources" / "playwright" / "driver" / "package" / ".local-browsers"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(local_browsers, target, symlinks=True)


def assert_playwright_chromium_runtime(app_path: Path) -> None:
    resources = app_path / "Contents" / "Resources"
    local_browsers = resources / "playwright" / "driver" / "package" / ".local-browsers"
    if not has_playwright_chromium(local_browsers):
        raise RuntimeError("macOS app 缺少 Playwright 内置 Chromium：" + str(local_browsers))


def ensure_icon() -> None:
    if ICON.exists():
        return
    png = ROOT / "assets" / "app.png"
    if not png.exists():
        run([sys.executable, "tools/make_icon.py"])
    iconset = ROOT / "assets" / "app.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        target = iconset / f"icon_{size}x{size}.png"
        run(["sips", "-z", str(size), str(size), str(png), "--out", str(target)])
        if size <= 512:
            target2x = iconset / f"icon_{size}x{size}@2x.png"
            run(["sips", "-z", str(size * 2), str(size * 2), str(png), "--out", str(target2x)])
    run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICON)])
    shutil.rmtree(iconset, ignore_errors=True)


def build() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("macOS 打包必须在 macOS 环境运行，请使用 GitHub Actions 或 Mac 电脑。")
    clean()
    ensure_icon()
    env = os.environ.copy()
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    playwright_browsers = ensure_playwright_chromium(env)
    extra_datas = [
        (ROOT / "assets", "assets"),
    ]
    print(f"Including Playwright browsers: {playwright_browsers}")
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(ICON),
            *add_data_args(extra_datas),
            "summer_camp_planner.py",
        ],
        env=env,
    )
    DIST.mkdir(parents=True, exist_ok=True)
    app_path = ROOT / "dist" / f"{APP_NAME}.app"
    copy_playwright_browsers_to_app(app_path, playwright_browsers)
    assert_playwright_chromium_runtime(app_path)
    dmg_path = DIST / DMG_NAME
    shutil.copytree(app_path, DIST / f"{APP_NAME}.app")
    dmg_root = ROOT / "build" / "dmg-root"
    if dmg_root.exists():
        shutil.rmtree(dmg_root)
    dmg_root.mkdir(parents=True)
    shutil.copytree(app_path, dmg_root / f"{APP_NAME}.app")
    applications_link = dmg_root / "Applications"
    if not applications_link.exists():
        applications_link.symlink_to("/Applications")
    run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(dmg_root),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
    )
    print(f"macOS release ready: {DIST}")


if __name__ == "__main__":
    build()
