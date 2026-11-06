# Visual Studio Build Stopwatch

A lightweight Windows system tray utility that displays live build timers for Visual Studio solutions and projects.

## Changelog
v1.0.0.0
November 6, 2026
- Initial release.

## Requirements

- Windows 10 or Windows 11
- Visual Studio 2026
- No Python installation required

**Note:** Visual Studio 2026 is the intended release target. Compatibility with earlier Visual Studio versions has not been tested.

## Installation

1. Download and extract the release ZIP file.
2. Close Visual Studio if it is running.
3. Double-click `VSBuildStopwatch.Extension.vsix`.
4. Follow the Visual Studio extension installer instructions.
5. Launch Visual Studio.
6. Run `Visual Studio Build Stopwatch.exe`.

The stopwatch icon will appear in the Windows notification area (system tray).

## Usage

Build a solution or project in Visual Studio.

The stopwatch automatically displays:

- A timer for the solution build.
- Individual timers for projects being built.
- Build success or failure indicators.
- Optional notification sounds when a solution build completes.

The overlay disappears after the configured delay.

## Settings

Right-click the stopwatch icon in the system tray to access:

- Overlay size
- Font customization
- Build completion sounds (WAV, MP3, OGG, FLAC)
- Disappearance delay
- Overlay position
- Appearance animation
- About

## Important

Both components are required for automatic build tracking:

- The VSIX extension detects Visual Studio build events.
- The stopwatch EXE receives those events and displays the timers.

The stopwatch application must be running to receive build notifications.

## Uninstallation

To remove the extension, open Visual Studio's **Extensions -> Manage Extensions** and uninstall Visual Studio Build Stopwatch.

To remove the stopwatch application, exit it from the system tray and delete the EXE.

## Author

Gal Shemesh (PacketLauncher)
https://github.com/packetlauncher


The following info is for developers who wish to make further adjustments to the program for their own personal use:

- To make adjustments to the extension, download the source files and open the VSBuildStopwatch.Extension.slnx project in Visual Studio. Modify it to your liking and create a new release when you're done.

- To make adjustments to the tray icon program, do so in the VS_Build_Stopwatch.py script, then make an EXE from it as follows:

1. Install Python if it's not already installed.
2. Open CMD window in the VS_Build_Stopwatch.py directory and install the following libraries if you don't have them:

python -m pip install pystray pillow pywin32
python -m pip install pygame
python -m pip install pyinstaller

* If installing pygame gives an error, try this instead:
python -m pip install --only-binary=:all: pygame-ce

3. Create the EXE either by running the BuildMe.bat script, or from CMD by running:
python -m PyInstaller --onefile --windowed --clean --name "Visual Studio Build Stopwatch" --icon "VS_Build_Stopwatch.ico" --add-data "VS_Build_Stopwatch.ico;." --collect-all pygame --hidden-import pystray._win32 "VS_Build_Stopwatch.py"

* You may use the Build_Leftovers_CleanUp.bat script for cleaning up any leftovers afterwards.